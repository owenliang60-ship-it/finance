"""Broad universe breadth participation study.

The module is intentionally research-only: it reads market.db and optional
sidecar parquet files, writes CSV/Markdown artifacts, and never writes back to
market.db.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from backtest.factor_study.report import _apply_bh_fdr
from backtest.metrics import compute_metrics
from config.settings import FMP_API_KEY
from src.data.fmp_client import FMPClient


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "breadth_study"
DEFAULT_MARKET_DB = PROJECT_ROOT / "data" / "market.db"
DEFAULT_OVERLAY_JSON = PROJECT_ROOT / "data" / "pool" / "delisted_large_caps.json"
DEFAULT_REPORT_PATH = (
    PROJECT_ROOT / "docs" / "research" / "2026-04-28-broad-breadth-qqq-soxx-study.md"
)

PRIMARY_TARGETS = ("QQQ", "SOXX")
BASELINE_SYMBOLS = ("SPY",)
TARGET_SYMBOLS = PRIMARY_TARGETS + BASELINE_SYMBOLS
PRIMARY_HORIZONS = (10, 20)
SUPPLEMENTARY_HORIZONS = (5, 60)
ALL_HORIZONS = (5, 10, 20, 60)
MA_WINDOWS = (20, 50)
TC_BPS = (5, 10, 20)
MIN_EVENT_N_TESTED = 15
MIN_EVENT_N_SUPPORTIVE = 10

EXCLUDED_TICKERS = {
    "QQQ",
    "SOXX",
    "SPY",
    "DIA",
    "IWM",
    "VTI",
    "VOO",
    "IVV",
    "SOXL",
    "SQQQ",
    "TQQQ",
}


@dataclass(frozen=True)
class StudyConfig:
    market_db: Path = DEFAULT_MARKET_DB
    output_dir: Path = DEFAULT_OUTPUT_DIR
    report_path: Path = DEFAULT_REPORT_PATH
    overlay_json: Path = DEFAULT_OVERLAY_JSON
    from_date: str = "2021-02-01"
    to_date: Optional[str] = None
    min_market_cap: float = 10_000_000_000.0
    max_staleness_days: int = 90
    oos_start: str = "2025-01-01"
    cooldown_days: int = 20
    bootstrap_samples: int = 1000
    bootstrap_block_days: int = 20
    random_seed: int = 42
    refresh_sidecar: bool = False


def run_study(config: StudyConfig) -> Dict[str, Path]:
    """Run the full research pipeline and return artifact paths."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_dir = config.output_dir / "sidecar"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    overlay_symbols = load_overlay_symbols(config.overlay_json)
    if config.refresh_sidecar or not _sidecar_files_exist(sidecar_dir):
        build_sidecar(
            overlay_symbols=overlay_symbols,
            output_dir=sidecar_dir,
            from_date=config.from_date,
            to_date=config.to_date or datetime.now().strftime("%Y-%m-%d"),
        )

    active_prices = read_daily_prices(
        config.market_db,
        from_date=config.from_date,
        to_date=config.to_date,
        exclude_symbols=(),
    )
    active_caps = read_historical_market_caps(
        config.market_db,
        from_date=config.from_date,
        to_date=config.to_date,
    )
    sidecar_prices, sidecar_caps = read_sidecar(sidecar_dir)

    daily_breadth, coverage_audit, ew_indices = build_breadth_outputs(
        active_prices=active_prices,
        active_caps=active_caps,
        sidecar_prices=sidecar_prices,
        sidecar_caps=sidecar_caps,
        overlay_symbols=overlay_symbols,
        config=config,
    )

    daily_breadth_path = config.output_dir / "daily_breadth.csv"
    coverage_audit_path = config.output_dir / "coverage_audit.csv"
    daily_breadth.to_csv(daily_breadth_path, index=False)
    coverage_audit.to_csv(coverage_audit_path, index=False)

    target_prices = active_prices[active_prices["symbol"].isin(TARGET_SYMBOLS)].copy()
    if target_prices.empty:
        raise ValueError("Target price data missing for QQQ/SOXX/SPY")

    state_results, event_results = run_forward_return_study(
        daily_breadth=daily_breadth,
        target_prices=target_prices,
        config=config,
    )
    state_path = config.output_dir / "state_forward_returns.csv"
    event_path = config.output_dir / "event_forward_returns.csv"
    state_results.to_csv(state_path, index=False)
    event_results.to_csv(event_path, index=False)

    overlay_results = run_overlay_backtests(
        daily_breadth=daily_breadth,
        target_prices=target_prices,
        ew_indices=ew_indices,
        config=config,
    )
    overlay_path = config.output_dir / "overlay_backtest.csv"
    overlay_results.to_csv(overlay_path, index=False)

    write_report(
        report_path=config.report_path,
        config=config,
        daily_breadth=daily_breadth,
        coverage_audit=coverage_audit,
        state_results=state_results,
        event_results=event_results,
        overlay_results=overlay_results,
    )

    return {
        "daily_breadth": daily_breadth_path,
        "coverage_audit": coverage_audit_path,
        "state_forward_returns": state_path,
        "event_forward_returns": event_path,
        "overlay_backtest": overlay_path,
        "report": config.report_path,
        "sidecar_coverage_report": sidecar_dir / "sidecar_coverage_report.md",
    }


def load_overlay_symbols(path: Path) -> List[str]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbols = payload.get("symbols", [])
    if not isinstance(symbols, list):
        raise ValueError(f"{path} must contain a 'symbols' list")
    return sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})


def _sidecar_files_exist(sidecar_dir: Path) -> bool:
    return (
        (sidecar_dir / "delisted_prices.parquet").exists()
        and (sidecar_dir / "delisted_market_cap.parquet").exists()
    )


def build_sidecar(
    overlay_symbols: Sequence[str],
    output_dir: Path,
    from_date: str,
    to_date: str,
    client: Optional[FMPClient] = None,
) -> None:
    """Fetch delisted overlay data into sidecar parquet files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    client = client or FMPClient()
    if overlay_symbols and not (client.api_key or FMP_API_KEY):
        raise ValueError("FMP_API_KEY is required to build sidecar parquet files")

    price_rows: List[Dict[str, Any]] = []
    cap_rows: List[Dict[str, Any]] = []
    coverage_rows: List[Dict[str, Any]] = []
    fetched_at = datetime.utcnow().isoformat(timespec="seconds")

    for symbol in overlay_symbols:
        logger.info("Fetching sidecar data for %s", symbol)
        fmp_prices = client.get_historical_price_range(symbol, from_date, to_date)
        price_source = "fmp_historical"
        if not fmp_prices:
            fmp_prices = _fetch_yfinance_history(symbol, from_date, to_date)
            price_source = "yfinance" if fmp_prices else "missing"

        for row in fmp_prices:
            if not row.get("date"):
                continue
            price_rows.append(
                {
                    "symbol": symbol,
                    "date": str(row["date"])[:10],
                    "open": _safe_float(row.get("open")),
                    "high": _safe_float(row.get("high")),
                    "low": _safe_float(row.get("low")),
                    "close": _safe_float(row.get("close")),
                    "volume": _safe_float(row.get("volume")),
                    "source": price_source,
                    "fetched_at": fetched_at,
                }
            )

        fmp_caps = client.get_historical_market_cap(symbol, from_date, to_date)
        for row in fmp_caps:
            if not row.get("date"):
                continue
            cap_rows.append(
                {
                    "symbol": symbol,
                    "date": str(row["date"])[:10],
                    "market_cap": _safe_float(row.get("market_cap")),
                    "source": "fmp_stable",
                    "fetched_at": fetched_at,
                }
            )

        coverage_rows.append(
            {
                "symbol": symbol,
                "price_source": price_source,
                "price_rows": len(fmp_prices),
                "price_start": _min_date(fmp_prices),
                "price_end": _max_date(fmp_prices),
                "market_cap_source": "fmp_stable" if fmp_caps else "missing",
                "market_cap_rows": len(fmp_caps),
                "market_cap_start": _min_date(fmp_caps),
                "market_cap_end": _max_date(fmp_caps),
            }
        )

    price_df = pd.DataFrame(
        price_rows,
        columns=[
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
            "fetched_at",
        ],
    )
    cap_df = pd.DataFrame(
        cap_rows,
        columns=["symbol", "date", "market_cap", "source", "fetched_at"],
    )
    price_df.to_parquet(output_dir / "delisted_prices.parquet", index=False)
    cap_df.to_parquet(output_dir / "delisted_market_cap.parquet", index=False)
    write_sidecar_coverage_report(pd.DataFrame(coverage_rows), output_dir)


def _fetch_yfinance_history(symbol: str, from_date: str, to_date: str) -> List[Dict[str, Any]]:
    try:
        import yfinance as yf
    except ImportError:
        return []

    ticker = yf.Ticker(symbol)
    df = ticker.history(start=from_date, end=to_date, auto_adjust=False)
    if df is None or df.empty:
        return []
    df = df.reset_index()
    rows = []
    for _, row in df.iterrows():
        rows.append(
            {
                "date": pd.to_datetime(row["Date"]).strftime("%Y-%m-%d"),
                "open": row.get("Open"),
                "high": row.get("High"),
                "low": row.get("Low"),
                "close": row.get("Close"),
                "volume": row.get("Volume"),
            }
        )
    return rows


def write_sidecar_coverage_report(df: pd.DataFrame, output_dir: Path) -> None:
    path = output_dir / "sidecar_coverage_report.md"
    if df.empty:
        body = "# Sidecar Coverage Report\n\nNo delisted overlay symbols configured.\n"
    else:
        source_counts = df["price_source"].value_counts(dropna=False).to_dict()
        body = [
            "# Sidecar Coverage Report",
            "",
            "FMP historical is the primary price source. yfinance is fallback only.",
            "",
            f"- Symbols: {len(df)}",
            f"- Price source breakdown: `{json.dumps(source_counts, sort_keys=True)}`",
            "",
            "| Symbol | Price source | Price rows | Price range | Market cap source | Market cap rows | Market cap range |",
            "|---|---:|---:|---|---:|---:|---|",
        ]
        for _, row in df.sort_values("symbol").iterrows():
            body.append(
                "| {symbol} | {price_source} | {price_rows} | {price_start} -> {price_end} "
                "| {market_cap_source} | {market_cap_rows} | {market_cap_start} -> {market_cap_end} |".format(
                    **row.fillna("").to_dict()
                )
            )
        body = "\n".join(body) + "\n"
    path.write_text(body, encoding="utf-8")


def read_sidecar(sidecar_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    price_path = sidecar_dir / "delisted_prices.parquet"
    cap_path = sidecar_dir / "delisted_market_cap.parquet"
    prices = _read_parquet_or_empty(
        price_path,
        columns=["symbol", "date", "open", "high", "low", "close", "volume", "source", "fetched_at"],
    )
    caps = _read_parquet_or_empty(
        cap_path,
        columns=["symbol", "date", "market_cap", "source", "fetched_at"],
    )
    if not prices.empty:
        prices["date"] = pd.to_datetime(prices["date"])
        prices["symbol"] = prices["symbol"].str.upper()
    if not caps.empty:
        caps["date"] = pd.to_datetime(caps["date"])
        caps["symbol"] = caps["symbol"].str.upper()
    return prices, caps


def _read_parquet_or_empty(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    df = pd.read_parquet(path)
    for col in columns:
        if col not in df.columns:
            df[col] = np.nan
    return df[list(columns)]


def read_daily_prices(
    market_db: Path,
    from_date: str,
    to_date: Optional[str],
    exclude_symbols: Sequence[str],
) -> pd.DataFrame:
    params: List[Any] = [from_date]
    where = ["date >= ?"]
    if to_date:
        where.append("date <= ?")
        params.append(to_date)
    if exclude_symbols:
        placeholders = ",".join("?" for _ in exclude_symbols)
        where.append(f"symbol NOT IN ({placeholders})")
        params.extend([symbol.upper() for symbol in exclude_symbols])
    sql = f"""
        SELECT symbol, date, open, high, low, close, volume
        FROM daily_price
        WHERE {' AND '.join(where)}
    """
    with sqlite3.connect(market_db) as conn:
        df = pd.read_sql_query(sql, conn, params=params, parse_dates=["date"])
    if df.empty:
        raise ValueError(f"No daily_price rows found in {market_db}")
    df["symbol"] = df["symbol"].str.upper()
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


def read_historical_market_caps(
    market_db: Path,
    from_date: str,
    to_date: Optional[str],
) -> pd.DataFrame:
    params: List[Any] = [from_date]
    where = ["date >= ?"]
    if to_date:
        where.append("date <= ?")
        params.append(to_date)
    sql = f"""
        SELECT symbol, date, market_cap
        FROM historical_market_cap
        WHERE {' AND '.join(where)}
    """
    with sqlite3.connect(market_db) as conn:
        df = pd.read_sql_query(sql, conn, params=params, parse_dates=["date"])
    if df.empty:
        raise ValueError(f"No historical_market_cap rows found in {market_db}")
    df["symbol"] = df["symbol"].str.upper()
    df["source"] = "market_db"
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


def build_breadth_outputs(
    active_prices: pd.DataFrame,
    active_caps: pd.DataFrame,
    sidecar_prices: pd.DataFrame,
    sidecar_caps: pd.DataFrame,
    overlay_symbols: Sequence[str],
    config: StudyConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, pd.DataFrame]]:
    active_prices = _prepare_price_frame(active_prices, source="market_db")
    active_caps = _prepare_cap_frame(active_caps, source="market_db")
    sidecar_prices = _prepare_price_frame(sidecar_prices, source=None)
    sidecar_caps = _prepare_cap_frame(sidecar_caps, source=None)

    overlay_set = set(overlay_symbols)
    active_prices = active_prices[~active_prices["symbol"].isin(overlay_set)]
    active_caps = active_caps[~active_caps["symbol"].isin(overlay_set)]

    active_eligible = build_eligible_price_frame(
        prices=active_prices,
        caps=active_caps,
        min_market_cap=config.min_market_cap,
        max_staleness_days=config.max_staleness_days,
    )
    partial_prices = pd.concat([active_prices, sidecar_prices], ignore_index=True)
    partial_caps = pd.concat([active_caps, sidecar_caps], ignore_index=True)
    partial_eligible = build_eligible_price_frame(
        prices=partial_prices,
        caps=partial_caps,
        min_market_cap=config.min_market_cap,
        max_staleness_days=config.max_staleness_days,
    )

    active_daily = _aggregate_breadth(active_eligible, suffix="_active")
    partial_daily = _aggregate_breadth(partial_eligible, suffix="")
    partial_daily = partial_daily.rename(
        columns={
            "eligible_count": "eligible_count_with_delisted_partial",
            "ma20_usable_count": "ma20_usable_count_with_delisted_partial",
            "ma50_usable_count": "ma50_usable_count_with_delisted_partial",
        }
    )
    # Anchor the breadth series to dates that actually produced breadth aggregates.
    # Raw inputs can contain non-equity-market rows (for example ^VIX on US
    # stock holidays); using raw price dates would create mid-stream NaN rows.
    dates = pd.DataFrame({"date": sorted(set(active_daily["date"]) | set(partial_daily["date"]))})
    daily = dates.merge(active_daily, on="date", how="left").merge(partial_daily, on="date", how="left")
    daily = daily.sort_values("date").reset_index(drop=True)
    for window in MA_WINDOWS:
        daily[f"breadth_{window}"] = daily[f"breadth_{window}"].astype(float)
        daily[f"breadth_{window}_active"] = daily[f"breadth_{window}_active"].astype(float)
        daily[f"breadth_{window}_sma5"] = daily[f"breadth_{window}"].rolling(5, min_periods=5).mean()
        daily[f"breadth_{window}_slope10"] = daily[f"breadth_{window}_sma5"] - daily[
            f"breadth_{window}_sma5"
        ].shift(10)
        daily[f"breadth_{window}_active_sma5"] = daily[f"breadth_{window}_active"].rolling(
            5, min_periods=5
        ).mean()
        daily[f"breadth_{window}_active_slope10"] = daily[
            f"breadth_{window}_active_sma5"
        ] - daily[f"breadth_{window}_active_sma5"].shift(10)

    coverage = build_coverage_audit(
        daily=daily,
        active_eligible=active_eligible,
        partial_eligible=partial_eligible,
        partial_prices=partial_prices,
        partial_caps=partial_caps,
        overlay_symbols=overlay_symbols,
        config=config,
    )

    ew_indices = {
        "active_only": build_equal_weight_index(active_eligible),
        "with_delisted_partial": build_equal_weight_index(partial_eligible),
    }
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    coverage["date"] = coverage["date"].dt.strftime("%Y-%m-%d")
    for name, df in ew_indices.items():
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
    return daily, coverage, ew_indices


def _prepare_price_frame(df: pd.DataFrame, source: Optional[str]) -> pd.DataFrame:
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "source"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    out = df.copy()
    out["symbol"] = out["symbol"].str.upper()
    out["date"] = pd.to_datetime(out["date"])
    if source is not None or "source" not in out.columns:
        out["source"] = source or "unknown"
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out[~out["symbol"].isin(EXCLUDED_TICKERS)]
    out = out.dropna(subset=["date", "symbol", "close"])
    return out[cols].sort_values(["symbol", "date"]).reset_index(drop=True)


def _prepare_cap_frame(df: pd.DataFrame, source: Optional[str]) -> pd.DataFrame:
    cols = ["symbol", "date", "market_cap", "source"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    out = df.copy()
    out["symbol"] = out["symbol"].str.upper()
    out["date"] = pd.to_datetime(out["date"])
    if source is not None or "source" not in out.columns:
        out["source"] = source or "unknown"
    out["market_cap"] = pd.to_numeric(out["market_cap"], errors="coerce")
    out = out[~out["symbol"].isin(EXCLUDED_TICKERS)]
    out = out.dropna(subset=["date", "symbol", "market_cap"])
    return out[cols].sort_values(["symbol", "date"]).reset_index(drop=True)


def build_eligible_price_frame(
    prices: pd.DataFrame,
    caps: pd.DataFrame,
    min_market_cap: float,
    max_staleness_days: int,
    ma_windows: Sequence[int] = MA_WINDOWS,
) -> pd.DataFrame:
    """Attach PIT latest market cap and apply strict eligibility."""
    if prices.empty or caps.empty:
        return pd.DataFrame()

    enriched = attach_latest_market_cap(prices, caps)
    enriched["cap_age_days"] = (enriched["date"] - enriched["latest_cap_date"]).dt.days
    enriched["pit_excluded_due_to_staleness"] = (
        enriched["latest_cap"].ge(min_market_cap)
        & enriched["cap_age_days"].gt(max_staleness_days)
    )
    eligible = enriched[
        enriched["latest_cap"].ge(min_market_cap)
        & enriched["cap_age_days"].le(max_staleness_days)
    ].copy()
    eligible = eligible.sort_values(["symbol", "date"]).reset_index(drop=True)
    for window in ma_windows:
        eligible[f"sma{window}"] = eligible.groupby("symbol")["close"].transform(
            lambda s: s.rolling(window, min_periods=window).mean()
        )
        eligible[f"above_ma{window}"] = eligible["close"] > eligible[f"sma{window}"]
    return eligible


def attach_latest_market_cap(prices: pd.DataFrame, caps: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time latest-cap attach, per symbol, with no future values."""
    if prices.empty:
        return prices.copy()
    chunks: List[pd.DataFrame] = []
    caps_by_symbol = {symbol: group.sort_values("date") for symbol, group in caps.groupby("symbol")}
    for symbol, price_group in prices.groupby("symbol", sort=False):
        cap_group = caps_by_symbol.get(symbol)
        if cap_group is None or cap_group.empty:
            continue
        left = price_group.sort_values("date")
        right = cap_group[["date", "market_cap", "source"]].rename(
            columns={
                "date": "latest_cap_date",
                "market_cap": "latest_cap",
                "source": "cap_source",
            }
        )
        joined = pd.merge_asof(
            left,
            right,
            left_on="date",
            right_on="latest_cap_date",
            direction="backward",
        )
        chunks.append(joined)
    if not chunks:
        return pd.DataFrame(columns=list(prices.columns) + ["latest_cap", "latest_cap_date", "cap_source"])
    return pd.concat(chunks, ignore_index=True)


def _aggregate_breadth(
    eligible: pd.DataFrame,
    suffix: str,
    ma_windows: Sequence[int] = MA_WINDOWS,
) -> pd.DataFrame:
    if eligible.empty:
        columns = ["date", f"eligible_count{suffix}"]
        for window in ma_windows:
            columns.extend([f"ma{window}_usable_count{suffix}", f"breadth_{window}{suffix}"])
        return pd.DataFrame(columns=columns)
    rows = []
    for date_value, group in eligible.groupby("date"):
        row: Dict[str, Any] = {
            "date": date_value,
            f"eligible_count{suffix}": int(group["symbol"].nunique()),
        }
        for window in ma_windows:
            usable = group.dropna(subset=[f"sma{window}"])
            row[f"ma{window}_usable_count{suffix}"] = int(usable["symbol"].nunique())
            row[f"breadth_{window}{suffix}"] = (
                float(usable[f"above_ma{window}"].mean()) if not usable.empty else np.nan
            )
        rows.append(row)
    return pd.DataFrame(rows).sort_values("date")


def build_coverage_audit(
    daily: pd.DataFrame,
    active_eligible: pd.DataFrame,
    partial_eligible: pd.DataFrame,
    partial_prices: pd.DataFrame,
    partial_caps: pd.DataFrame,
    overlay_symbols: Sequence[str],
    config: StudyConfig,
) -> pd.DataFrame:
    dates = pd.DataFrame({"date": pd.to_datetime(daily["date"])})
    audit = dates.merge(
        daily[
            [
                "date",
                "eligible_count_active",
                "eligible_count_with_delisted_partial",
                "breadth_20",
                "breadth_50",
                "breadth_20_active",
                "breadth_50_active",
            ]
        ],
        on="date",
        how="left",
    )
    overlay_set = set(overlay_symbols)
    delisted_eligible = partial_eligible[partial_eligible["symbol"].isin(overlay_set)].copy()
    source_breakdown: Dict[pd.Timestamp, str] = {}
    if not delisted_eligible.empty:
        for date_value, group in delisted_eligible.groupby("date"):
            counts = group["source"].fillna("unknown").value_counts().sort_index().to_dict()
            source_breakdown[date_value] = json.dumps(counts, sort_keys=True)
    audit["delisted_source_breakdown"] = audit["date"].map(source_breakdown).fillna("{}")
    audit["delisted_overlay_eligible_count"] = audit["date"].map(
        delisted_eligible.groupby("date")["symbol"].nunique().to_dict()
        if not delisted_eligible.empty
        else {}
    ).fillna(0).astype(int)
    denominator = max(len(overlay_set), 1)
    audit["delisted_overlay_coverage_ratio"] = audit["delisted_overlay_eligible_count"] / denominator

    stale_counts = compute_staleness_counts(
        prices=partial_prices,
        caps=partial_caps,
        dates=pd.to_datetime(audit["date"]),
        min_market_cap=config.min_market_cap,
        max_staleness_days=config.max_staleness_days,
    )
    audit["pit_excluded_due_to_staleness_count"] = audit["date"].map(stale_counts).fillna(0).astype(int)

    delisted_missing_ratio = compute_delisted_price_missing_ratio(
        dates=pd.to_datetime(audit["date"]),
        sidecar_prices=partial_prices[partial_prices["symbol"].isin(overlay_set)],
        sidecar_caps=partial_caps[partial_caps["symbol"].isin(overlay_set)],
        min_market_cap=config.min_market_cap,
        max_staleness_days=config.max_staleness_days,
    )
    audit["delisted_price_missing_ratio"] = audit["date"].map(delisted_missing_ratio).fillna(0.0)

    valid = filter_effective_breadth(daily)
    effective_start = valid["date"].min() if not valid.empty else pd.NaT
    effective_end = valid["date"].max() if not valid.empty else pd.NaT
    audit["effective_sample_start"] = effective_start
    audit["effective_sample_end"] = effective_end
    return audit.sort_values("date").reset_index(drop=True)


def compute_staleness_counts(
    prices: pd.DataFrame,
    caps: pd.DataFrame,
    dates: Iterable[pd.Timestamp],
    min_market_cap: float,
    max_staleness_days: int,
) -> Dict[pd.Timestamp, int]:
    enriched = attach_latest_market_cap(prices, caps)
    if enriched.empty:
        return {}
    enriched["cap_age_days"] = (enriched["date"] - enriched["latest_cap_date"]).dt.days
    stale = enriched[
        enriched["latest_cap"].ge(min_market_cap)
        & enriched["cap_age_days"].gt(max_staleness_days)
    ]
    return stale.groupby("date")["symbol"].nunique().to_dict()


def compute_delisted_price_missing_ratio(
    dates: Iterable[pd.Timestamp],
    sidecar_prices: pd.DataFrame,
    sidecar_caps: pd.DataFrame,
    min_market_cap: float,
    max_staleness_days: int,
) -> Dict[pd.Timestamp, float]:
    if sidecar_caps.empty:
        return {}
    price_index = set(zip(sidecar_prices["symbol"], sidecar_prices["date"]))
    ratios: Dict[pd.Timestamp, float] = {}
    for date_value in dates:
        rows = []
        for symbol, cap_group in sidecar_caps.groupby("symbol"):
            caps_asof = cap_group[cap_group["date"] <= date_value].sort_values("date")
            if caps_asof.empty:
                continue
            latest = caps_asof.iloc[-1]
            age = (date_value - latest["date"]).days
            if latest["market_cap"] >= min_market_cap and age <= max_staleness_days:
                rows.append(symbol)
        if not rows:
            ratios[date_value] = 0.0
            continue
        missing = [symbol for symbol in rows if (symbol, date_value) not in price_index]
        ratios[date_value] = len(missing) / len(rows)
    return ratios


def build_equal_weight_index(eligible: pd.DataFrame) -> pd.DataFrame:
    if eligible.empty:
        return pd.DataFrame(columns=["date", "ew_index", "ew_index_ma50", "ew_above_ma50"])
    prices = eligible[["date", "symbol", "close"]].dropna().copy()
    prices["ret"] = prices.groupby("symbol")["close"].pct_change()
    daily_ret = prices.groupby("date")["ret"].mean().dropna()
    if daily_ret.empty:
        return pd.DataFrame(columns=["date", "ew_index", "ew_index_ma50", "ew_above_ma50"])
    out = pd.DataFrame({"date": daily_ret.index, "ew_ret": daily_ret.values})
    out["ew_index"] = (1.0 + out["ew_ret"]).cumprod()
    out["ew_index_ma50"] = out["ew_index"].rolling(50, min_periods=50).mean()
    out["ew_above_ma50"] = out["ew_index"] > out["ew_index_ma50"]
    return out


def filter_effective_breadth(df: pd.DataFrame) -> pd.DataFrame:
    """Use only dates where active and partial MA20/MA50 breadth are all valid."""
    required = ["breadth_20", "breadth_50", "breadth_20_active", "breadth_50_active"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"daily breadth missing required columns: {missing}")
    return df.dropna(subset=required).copy()


def run_forward_return_study(
    daily_breadth: pd.DataFrame,
    target_prices: pd.DataFrame,
    config: StudyConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    breadth = daily_breadth.copy()
    breadth["date"] = pd.to_datetime(breadth["date"])
    breadth = filter_effective_breadth(breadth)
    target_returns = build_forward_returns(target_prices, ALL_HORIZONS)
    merged = breadth.merge(target_returns, on="date", how="left")

    state_rows: List[Dict[str, Any]] = []
    event_rows: List[Dict[str, Any]] = []
    for universe_variant in ("active_only", "with_delisted_partial"):
        suffix = "_active" if universe_variant == "active_only" else ""
        for sample_name, sample_df in iter_samples(merged, config.oos_start):
            state_rows.extend(
                run_state_tests(
                    df=sample_df,
                    universe_variant=universe_variant,
                    suffix=suffix,
                    sample_name=sample_name,
                    config=config,
                )
            )
            event_rows.extend(
                run_event_tests(
                    df=sample_df,
                    full_df=merged,
                    universe_variant=universe_variant,
                    suffix=suffix,
                    sample_name=sample_name,
                    config=config,
                )
            )

    state_df = pd.DataFrame(state_rows)
    event_df = pd.DataFrame(event_rows)
    primary_df = build_primary_family(state_df, event_df)
    primary_df = apply_primary_fdr(primary_df)
    state_df = merge_primary_q_values(state_df, primary_df)
    event_df = merge_primary_q_values(event_df, primary_df)
    return state_df, event_df


def build_forward_returns(target_prices: pd.DataFrame, horizons: Sequence[int]) -> pd.DataFrame:
    rows = []
    for symbol, group in target_prices.groupby("symbol"):
        g = group.sort_values("date").reset_index(drop=True)
        for horizon in horizons:
            g[f"fwd_{horizon}d"] = g["close"].shift(-horizon) / g["open"].shift(-1) - 1.0
        for _, row in g.iterrows():
            out = {"date": row["date"]}
            for horizon in horizons:
                out[f"{symbol}_fwd_{horizon}d"] = row[f"fwd_{horizon}d"]
            rows.append(out)
    merged = None
    for symbol in sorted(target_prices["symbol"].unique()):
        cols = ["date"] + [f"{symbol}_fwd_{horizon}d" for horizon in horizons]
        sdf = pd.DataFrame([row for row in rows if f"{symbol}_fwd_{horizons[0]}d" in row])
        sdf = sdf[cols].drop_duplicates("date")
        merged = sdf if merged is None else merged.merge(sdf, on="date", how="outer")
    if merged is None:
        return pd.DataFrame(columns=["date"])
    for target in PRIMARY_TARGETS:
        for horizon in horizons:
            if f"{target}_fwd_{horizon}d" in merged and f"SPY_fwd_{horizon}d" in merged:
                merged[f"{target}_excess_spy_{horizon}d"] = (
                    merged[f"{target}_fwd_{horizon}d"] - merged[f"SPY_fwd_{horizon}d"]
                )
    for horizon in horizons:
        if f"SOXX_fwd_{horizon}d" in merged and f"QQQ_fwd_{horizon}d" in merged:
            merged[f"SOXX_excess_qqq_{horizon}d"] = (
                merged[f"SOXX_fwd_{horizon}d"] - merged[f"QQQ_fwd_{horizon}d"]
            )
    return merged.sort_values("date").reset_index(drop=True)


def iter_samples(df: pd.DataFrame, oos_start: str) -> Iterable[Tuple[str, pd.DataFrame]]:
    oos_dt = pd.to_datetime(oos_start)
    yield "Full", df.copy()
    yield "IS", df[df["date"] < oos_dt].copy()
    yield "OOS", df[df["date"] >= oos_dt].copy()


def run_state_tests(
    df: pd.DataFrame,
    universe_variant: str,
    suffix: str,
    sample_name: str,
    config: StudyConfig,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    rng = np.random.default_rng(config.random_seed)
    for window, hypothesis in ((50, "H1"), (20, "H2")):
        signal_col = f"breadth_{window}{suffix}_sma5"
        if signal_col not in df.columns:
            continue
        for target in PRIMARY_TARGETS:
            for horizon in ALL_HORIZONS:
                ret_col = f"{target}_fwd_{horizon}d"
                data = df[["date", signal_col, ret_col]].dropna()
                if data.empty:
                    continue
                data = data.rename(columns={signal_col: "signal", ret_col: "forward_return"})
                rows.extend(
                    state_bucket_rows(
                        data=data,
                        universe_variant=universe_variant,
                        sample_name=sample_name,
                        target=target,
                        horizon=horizon,
                        window=window,
                    )
                )
                is_primary = horizon in PRIMARY_HORIZONS
                stat = compare_state_means(
                    data["forward_return"].to_numpy(dtype=float),
                    (data["signal"].to_numpy(dtype=float) > 0.50).astype(float),
                    lag=horizon,
                    alternative="upper",
                    bootstrap_samples=config.bootstrap_samples,
                    block_size=config.bootstrap_block_days,
                    rng=rng,
                )
                rows.append(
                    {
                        "row_type": "hypothesis",
                        "universe_variant": universe_variant,
                        "sample": sample_name,
                        "hypothesis": hypothesis,
                        "target": target,
                        "horizon": horizon,
                        "ma_window": window,
                        "state_bucket": ">50_vs_<=50",
                        "n": int(stat["n"]),
                        "accepted_n": int(stat["accepted_n"]),
                        "rejected_n": int(stat["rejected_n"]),
                        "mean_return": stat["accepted_mean"],
                        "rejected_mean_return": stat["rejected_mean"],
                        "diff_mean": stat["diff"],
                        "median_return": np.nan,
                        "hit_rate": np.nan,
                        "hac_t_stat": stat["t_stat"],
                        "hac_p_value": stat["p_value"],
                        "bootstrap_p_value": stat["bootstrap_p"],
                        "bootstrap_ci_low": stat["bootstrap_ci_low"],
                        "bootstrap_ci_high": stat["bootstrap_ci_high"],
                        "alternative": "upper",
                        "primary_family": bool(is_primary),
                        "verdict_p": stat["p_value"] if is_primary else np.nan,
                        "audit_p": stat["p_value"] if is_primary else np.nan,
                        "cell_status": "tested" if is_primary else "supplementary",
                    }
                )
    return rows


def state_bucket_rows(
    data: pd.DataFrame,
    universe_variant: str,
    sample_name: str,
    target: str,
    horizon: int,
    window: int,
) -> List[Dict[str, Any]]:
    bins = [-np.inf, 0.20, 0.30, 0.50, 0.70, np.inf]
    labels = ["<20", "20-30", "30-50", "50-70", ">70"]
    data = data.copy()
    data["state_bucket"] = pd.cut(data["signal"], bins=bins, labels=labels, right=False)
    rows = []
    for bucket, group in data.groupby("state_bucket", observed=False):
        returns = group["forward_return"].dropna()
        rows.append(
            {
                "row_type": "state_bucket",
                "universe_variant": universe_variant,
                "sample": sample_name,
                "hypothesis": "",
                "target": target,
                "horizon": horizon,
                "ma_window": window,
                "state_bucket": str(bucket),
                "n": int(len(returns)),
                "accepted_n": np.nan,
                "rejected_n": np.nan,
                "mean_return": float(returns.mean()) if len(returns) else np.nan,
                "rejected_mean_return": np.nan,
                "diff_mean": np.nan,
                "median_return": float(returns.median()) if len(returns) else np.nan,
                "hit_rate": float((returns > 0).mean()) if len(returns) else np.nan,
                "hac_t_stat": np.nan,
                "hac_p_value": np.nan,
                "bootstrap_p_value": np.nan,
                "bootstrap_ci_low": np.nan,
                "bootstrap_ci_high": np.nan,
                "alternative": "",
                "primary_family": False,
                "verdict_p": np.nan,
                "audit_p": np.nan,
                "cell_status": "bucket",
            }
        )
    return rows


def compare_state_means(
    returns: np.ndarray,
    accepted: np.ndarray,
    lag: int,
    alternative: str,
    bootstrap_samples: int,
    block_size: int,
    rng: np.random.Generator,
) -> Dict[str, float]:
    mask = np.isfinite(returns) & np.isfinite(accepted)
    y = returns[mask]
    x = accepted[mask]
    accepted_returns = y[x > 0.5]
    rejected_returns = y[x <= 0.5]
    if len(accepted_returns) == 0 or len(rejected_returns) == 0:
        return _empty_stat(len(y), len(accepted_returns), len(rejected_returns))
    hac = hac_dummy_t_test(y, x, lag=lag, alternative=alternative)
    boot = block_bootstrap_state_diff(
        y,
        x,
        samples=bootstrap_samples,
        block_size=block_size,
        alternative=alternative,
        rng=rng,
    )
    return {
        "n": float(len(y)),
        "accepted_n": float(len(accepted_returns)),
        "rejected_n": float(len(rejected_returns)),
        "accepted_mean": float(np.mean(accepted_returns)),
        "rejected_mean": float(np.mean(rejected_returns)),
        "diff": float(np.mean(accepted_returns) - np.mean(rejected_returns)),
        "t_stat": hac[0],
        "p_value": hac[1],
        "bootstrap_p": boot[0],
        "bootstrap_ci_low": boot[1],
        "bootstrap_ci_high": boot[2],
    }


def hac_dummy_t_test(
    returns: np.ndarray,
    accepted: np.ndarray,
    lag: int,
    alternative: str,
) -> Tuple[float, float]:
    y = np.asarray(returns, dtype=float)
    x = np.column_stack([np.ones(len(y)), np.asarray(accepted, dtype=float)])
    if len(y) <= 3 or np.linalg.matrix_rank(x) < 2:
        return np.nan, 1.0
    xtx_inv = np.linalg.pinv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    resid = y - x @ beta
    xu = x * resid[:, None]
    meat = xu.T @ xu
    max_lag = min(int(lag), len(y) - 1)
    for l in range(1, max_lag + 1):
        weight = 1.0 - l / (max_lag + 1.0)
        gamma = xu[l:].T @ xu[:-l]
        meat += weight * (gamma + gamma.T)
    cov = xtx_inv @ meat @ xtx_inv
    se = math.sqrt(max(float(cov[1, 1]), 0.0))
    if se <= 1e-12:
        return np.nan, 1.0
    t_stat = float(beta[1] / se)
    df = max(len(y) - 2, 1)
    p_value = _one_sided_p(t_stat, df, alternative)
    return t_stat, p_value


def block_bootstrap_state_diff(
    returns: np.ndarray,
    accepted: np.ndarray,
    samples: int,
    block_size: int,
    alternative: str,
    rng: np.random.Generator,
) -> Tuple[float, float, float]:
    n = len(returns)
    diffs = []
    for _ in range(samples):
        idx = _sample_block_indices(n, block_size, rng)
        y = returns[idx]
        x = accepted[idx]
        if not np.any(x > 0.5) or not np.any(x <= 0.5):
            continue
        diffs.append(float(np.mean(y[x > 0.5]) - np.mean(y[x <= 0.5])))
    if not diffs:
        return 1.0, np.nan, np.nan
    arr = np.asarray(diffs)
    if alternative == "upper":
        p_value = (np.sum(arr <= 0.0) + 1.0) / (len(arr) + 1.0)
    else:
        p_value = (np.sum(arr >= 0.0) + 1.0) / (len(arr) + 1.0)
    return float(p_value), float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))


def _sample_block_indices(n: int, block_size: int, rng: np.random.Generator) -> np.ndarray:
    idx: List[int] = []
    while len(idx) < n:
        start = int(rng.integers(0, max(n, 1)))
        stop = min(start + block_size, n)
        idx.extend(range(start, stop))
    return np.asarray(idx[:n], dtype=int)


def run_event_tests(
    df: pd.DataFrame,
    full_df: pd.DataFrame,
    universe_variant: str,
    suffix: str,
    sample_name: str,
    config: StudyConfig,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    rng = np.random.default_rng(config.random_seed + 17)
    signal_col = f"breadth_50{suffix}_sma5"
    if signal_col not in df.columns:
        return rows
    sample_events = {
        "H3": detect_events(df[["date", signal_col]].dropna(), signal_col, "recovery", config.cooldown_days),
        "H4": detect_events(
            df[["date", signal_col]].dropna(),
            signal_col,
            "deterioration",
            config.cooldown_days,
        ),
    }
    is_events = {
        "H3": detect_events(
            full_df[full_df["date"] < pd.to_datetime(config.oos_start)][["date", signal_col]].dropna(),
            signal_col,
            "recovery",
            config.cooldown_days,
        ),
        "H4": detect_events(
            full_df[full_df["date"] < pd.to_datetime(config.oos_start)][["date", signal_col]].dropna(),
            signal_col,
            "deterioration",
            config.cooldown_days,
        ),
    }
    for hypothesis, event_type in (("H3", "recovery"), ("H4", "deterioration")):
        alternative = "upper" if hypothesis == "H3" else "lower"
        event_dates = sample_events[hypothesis]
        is_event_n = len(is_events[hypothesis])
        cell_status = event_cell_status(is_event_n)
        for target in PRIMARY_TARGETS:
            for horizon in ALL_HORIZONS:
                ret_col = f"{target}_fwd_{horizon}d"
                data = df[["date", ret_col]].dropna().rename(columns={ret_col: "forward_return"})
                event_returns = data[data["date"].isin(event_dates)].copy()
                stat = compare_event_returns(
                    event_returns=event_returns,
                    daily_returns=data,
                    alternative=alternative,
                    bootstrap_samples=config.bootstrap_samples,
                    rng=rng,
                )
                is_primary = horizon in PRIMARY_HORIZONS
                actual_p = stat["p_value"]
                verdict_p = actual_p if is_primary and cell_status == "tested" else 1.0
                audit_p = actual_p if is_primary and cell_status in {"tested", "supportive"} else 1.0
                rows.append(
                    {
                        "universe_variant": universe_variant,
                        "sample": sample_name,
                        "hypothesis": hypothesis,
                        "event_type": event_type,
                        "target": target,
                        "horizon": horizon,
                        "event_n": int(len(event_returns)),
                        "is_event_n": int(is_event_n),
                        "cell_status": cell_status if is_primary else "supplementary",
                        "mean_return": stat["event_mean"],
                        "baseline_mean_return": stat["baseline_mean"],
                        "diff_mean": stat["diff"],
                        "median_return": stat["event_median"],
                        "hit_rate": stat["hit_rate"],
                        "hac_t_stat": stat["t_stat"],
                        "hac_p_value": actual_p,
                        "bootstrap_p_value": stat["bootstrap_p"],
                        "bootstrap_ci_low": stat["bootstrap_ci_low"],
                        "bootstrap_ci_high": stat["bootstrap_ci_high"],
                        "alternative": alternative,
                        "primary_family": bool(is_primary),
                        "verdict_p": verdict_p if is_primary else np.nan,
                        "audit_p": audit_p if is_primary else np.nan,
                    }
                )
    return rows


def detect_events(df: pd.DataFrame, signal_col: str, event_type: str, cooldown_days: int) -> List[pd.Timestamp]:
    events: List[pd.Timestamp] = []
    armed = False
    last_event_idx = -10_000
    values = df.sort_values("date").reset_index(drop=True)
    for idx, row in values.iterrows():
        value = row[signal_col]
        if not np.isfinite(value):
            continue
        if event_type == "recovery":
            if value < 0.30:
                armed = True
            if armed and value >= 0.50 and idx - last_event_idx >= cooldown_days:
                events.append(row["date"])
                last_event_idx = idx
                armed = False
        elif event_type == "deterioration":
            if value > 0.70:
                armed = True
            if armed and value < 0.50 and idx - last_event_idx >= cooldown_days:
                events.append(row["date"])
                last_event_idx = idx
                armed = False
        else:
            raise ValueError(f"Unknown event_type: {event_type}")
    return events


def event_cell_status(is_event_n: int) -> str:
    if is_event_n >= MIN_EVENT_N_TESTED:
        return "tested"
    if is_event_n >= MIN_EVENT_N_SUPPORTIVE:
        return "supportive"
    return "not_tested"


def compare_event_returns(
    event_returns: pd.DataFrame,
    daily_returns: pd.DataFrame,
    alternative: str,
    bootstrap_samples: int,
    rng: np.random.Generator,
) -> Dict[str, float]:
    if event_returns.empty:
        return {
            "event_mean": np.nan,
            "baseline_mean": float(daily_returns["forward_return"].mean()) if not daily_returns.empty else np.nan,
            "diff": np.nan,
            "event_median": np.nan,
            "hit_rate": np.nan,
            "t_stat": np.nan,
            "p_value": 1.0,
            "bootstrap_p": 1.0,
            "bootstrap_ci_low": np.nan,
            "bootstrap_ci_high": np.nan,
        }
    events = event_returns.copy()
    events["year"] = events["date"].dt.year
    daily = daily_returns.copy()
    daily["year"] = daily["date"].dt.year
    year_means = daily.groupby("year")["forward_return"].mean().to_dict()
    baseline = events["year"].map(year_means).to_numpy(dtype=float)
    event_values = events["forward_return"].to_numpy(dtype=float)
    diffs = event_values - baseline
    diff_mean = float(np.nanmean(diffs))
    if len(diffs) > 1 and np.nanstd(diffs, ddof=1) > 1e-12:
        se = float(np.nanstd(diffs, ddof=1) / math.sqrt(len(diffs)))
        t_stat = diff_mean / se
        p_value = _one_sided_p(t_stat, max(len(diffs) - 1, 1), alternative)
    else:
        t_stat = np.nan
        p_value = 1.0
    boot_p, ci_low, ci_high = block_bootstrap_event_diff(
        events=events,
        daily=daily,
        samples=bootstrap_samples,
        alternative=alternative,
        rng=rng,
    )
    return {
        "event_mean": float(np.nanmean(event_values)),
        "baseline_mean": float(np.nanmean(baseline)),
        "diff": diff_mean,
        "event_median": float(np.nanmedian(event_values)),
        "hit_rate": float(np.nanmean(event_values > 0.0)),
        "t_stat": float(t_stat) if np.isfinite(t_stat) else np.nan,
        "p_value": float(p_value),
        "bootstrap_p": boot_p,
        "bootstrap_ci_low": ci_low,
        "bootstrap_ci_high": ci_high,
    }


def block_bootstrap_event_diff(
    events: pd.DataFrame,
    daily: pd.DataFrame,
    samples: int,
    alternative: str,
    rng: np.random.Generator,
) -> Tuple[float, float, float]:
    if events.empty or daily.empty:
        return 1.0, np.nan, np.nan
    daily_by_year = {
        year: group["forward_return"].dropna().to_numpy(dtype=float)
        for year, group in daily.groupby("year")
    }
    events = events.dropna(subset=["forward_return"])
    diffs = []
    values = events["forward_return"].to_numpy(dtype=float)
    years = events["year"].to_numpy(dtype=int)
    for _ in range(samples):
        idx = rng.integers(0, len(values), size=len(values))
        sampled_events = values[idx]
        sampled_years = years[idx]
        sampled_baseline = []
        for year in sampled_years:
            pool = daily_by_year.get(int(year))
            if pool is None or len(pool) == 0:
                sampled_baseline.append(np.nan)
            else:
                sampled_baseline.append(float(pool[int(rng.integers(0, len(pool)))]))
        diff = np.nanmean(sampled_events - np.asarray(sampled_baseline, dtype=float))
        if np.isfinite(diff):
            diffs.append(float(diff))
    if not diffs:
        return 1.0, np.nan, np.nan
    arr = np.asarray(diffs)
    if alternative == "upper":
        p_value = (np.sum(arr <= 0.0) + 1.0) / (len(arr) + 1.0)
    else:
        p_value = (np.sum(arr >= 0.0) + 1.0) / (len(arr) + 1.0)
    return float(p_value), float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))


def build_primary_family(state_df: pd.DataFrame, event_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if not state_df.empty:
        rows.extend(
            state_df[
                (state_df["row_type"] == "hypothesis") & (state_df["primary_family"])
            ][
                [
                    "universe_variant",
                    "sample",
                    "hypothesis",
                    "target",
                    "horizon",
                    "verdict_p",
                    "audit_p",
                ]
            ].to_dict("records")
        )
    if not event_df.empty:
        rows.extend(
            event_df[event_df["primary_family"]][
                [
                    "universe_variant",
                    "sample",
                    "hypothesis",
                    "target",
                    "horizon",
                    "verdict_p",
                    "audit_p",
                ]
            ].to_dict("records")
        )
    return pd.DataFrame(rows)


def apply_primary_fdr(primary_df: pd.DataFrame) -> pd.DataFrame:
    if primary_df.empty:
        primary_df["verdict_q"] = []
        primary_df["audit_q"] = []
        return primary_df
    out = []
    for _, group in primary_df.groupby(["universe_variant", "sample"], dropna=False):
        g = group.copy()
        g["verdict_p"] = pd.to_numeric(g["verdict_p"], errors="coerce").fillna(1.0)
        g["audit_p"] = pd.to_numeric(g["audit_p"], errors="coerce").fillna(1.0)
        g["verdict_q"] = _apply_bh_fdr(g["verdict_p"].tolist())
        g["audit_q"] = _apply_bh_fdr(g["audit_p"].tolist())
        out.append(g)
    return pd.concat(out, ignore_index=True)


def merge_primary_q_values(df: pd.DataFrame, primary_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or primary_df.empty:
        return df
    keys = ["universe_variant", "sample", "hypothesis", "target", "horizon"]
    cols = keys + ["verdict_q", "audit_q"]
    return df.merge(primary_df[cols], on=keys, how="left")


def run_overlay_backtests(
    daily_breadth: pd.DataFrame,
    target_prices: pd.DataFrame,
    ew_indices: Mapping[str, pd.DataFrame],
    config: StudyConfig,
) -> pd.DataFrame:
    breadth = daily_breadth.copy()
    breadth["date"] = pd.to_datetime(breadth["date"])
    breadth = filter_effective_breadth(breadth)
    rows: List[Dict[str, Any]] = []
    for universe_variant in ("active_only", "with_delisted_partial"):
        suffix = "_active" if universe_variant == "active_only" else ""
        signals = build_overlay_signals(breadth, suffix=suffix, ew_index=ew_indices.get(universe_variant))
        for target in PRIMARY_TARGETS:
            target_df = target_prices[target_prices["symbol"] == target].sort_values("date")
            spy_df = target_prices[target_prices["symbol"] == "SPY"].sort_values("date")
            for strategy_name, weight_series in signals.items():
                if strategy_name == "own_ma50":
                    weight_series = build_own_ma_signal(target_df)
                elif strategy_name == "spy_ma50":
                    weight_series = build_own_ma_signal(spy_df)
                elif strategy_name == "buy_hold":
                    weight_series = pd.Series(1.0, index=pd.to_datetime(target_df["date"]))
                for tc_bps in TC_BPS:
                    nav = run_single_asset_overlay(target_df, weight_series, tc_bps=tc_bps)
                    for sample_name, sample_nav in iter_nav_samples(nav, config.oos_start):
                        metrics = compute_metrics(
                            list(zip(sample_nav["date"].dt.strftime("%Y-%m-%d"), sample_nav["nav"])),
                            total_costs=float(sample_nav["cost"].sum()),
                            n_trades=int(sample_nav["trade"].sum()),
                            annual_turnover=annualized_turnover(sample_nav),
                        )
                        rows.append(
                            {
                                "universe_variant": universe_variant,
                                "sample": sample_name,
                                "target": target,
                                "strategy": strategy_name,
                                "tc_bps": tc_bps,
                                "total_return": metrics.total_return,
                                "cagr": metrics.cagr,
                                "annual_volatility": metrics.annual_volatility,
                                "sharpe": metrics.sharpe_ratio,
                                "sortino": metrics.sortino_ratio,
                                "max_drawdown": metrics.max_drawdown,
                                "calmar": metrics.calmar_ratio,
                                "exposure": float(sample_nav["weight"].mean()) if not sample_nav.empty else np.nan,
                                "turnover": metrics.annual_turnover,
                                "n_trades": metrics.n_trades,
                                "total_costs": metrics.total_costs,
                                "worst_drawdowns": json.dumps(worst_drawdown_windows(sample_nav, top_n=5)),
                            }
                        )
    result = pd.DataFrame(rows)
    result = add_overlay_baseline_diffs(result)
    return result


def build_overlay_signals(
    breadth: pd.DataFrame,
    suffix: str,
    ew_index: Optional[pd.DataFrame],
) -> Dict[str, pd.Series]:
    b = breadth.set_index("date")
    signals: Dict[str, pd.Series] = {
        "buy_hold": pd.Series(1.0, index=b.index),
        "own_ma50": pd.Series(np.nan, index=b.index),
        "spy_ma50": pd.Series(np.nan, index=b.index),
        "breadth_50_hard": (b[f"breadth_50{suffix}_sma5"] > 0.50).astype(float),
        "breadth_20_hard": (b[f"breadth_20{suffix}_sma5"] > 0.50).astype(float),
        "breadth_20_50_hard": (
            (b[f"breadth_50{suffix}_sma5"] > 0.50)
            & (b[f"breadth_20{suffix}_sma5"] > 0.50)
        ).astype(float),
        "breadth_50_soft_w0": ((b[f"breadth_50{suffix}_sma5"] - 0.30) / 0.40).clip(0.0, 1.0),
        "breadth_50_soft_w25": ((b[f"breadth_50{suffix}_sma5"] - 0.30) / 0.40).clip(0.25, 1.0),
    }
    if ew_index is not None and not ew_index.empty:
        ew = ew_index.copy()
        ew["date"] = pd.to_datetime(ew["date"])
        ew_signal = ew.set_index("date")["ew_above_ma50"].astype(float)
        signals["equal_weight_ma50"] = ew_signal
    else:
        signals["equal_weight_ma50"] = pd.Series(np.nan, index=b.index)
    return signals


def build_own_ma_signal(target_df: pd.DataFrame) -> pd.Series:
    g = target_df.sort_values("date").copy()
    g["ma50"] = g["close"].rolling(50, min_periods=50).mean()
    return pd.Series((g["close"] > g["ma50"]).astype(float).values, index=pd.to_datetime(g["date"]))


def run_single_asset_overlay(
    target_df: pd.DataFrame,
    weight_signal: pd.Series,
    tc_bps: int,
) -> pd.DataFrame:
    g = target_df.sort_values("date").copy()
    g["date"] = pd.to_datetime(g["date"])
    signal = weight_signal.copy()
    signal.index = pd.to_datetime(signal.index)
    g["desired_weight"] = g["date"].map(signal).astype(float)
    g["desired_weight"] = g["desired_weight"].ffill().fillna(0.0)

    nav = 1.0
    prev_weight = 0.0
    rows = []
    prev_close = None
    tc = tc_bps / 10_000.0
    for _, row in g.iterrows():
        if prev_close is not None and prev_weight > 0:
            nav *= 1.0 + prev_weight * (row["open"] / prev_close - 1.0)
        desired_weight = float(row["desired_weight"])
        trade_delta = abs(desired_weight - prev_weight)
        cost = nav * trade_delta * tc
        nav -= cost
        nav *= 1.0 + desired_weight * (row["close"] / row["open"] - 1.0)
        rows.append(
            {
                "date": row["date"],
                "nav": nav,
                "weight": desired_weight,
                "trade": 1 if trade_delta > 1e-8 else 0,
                "turnover": trade_delta,
                "cost": cost,
            }
        )
        prev_weight = desired_weight
        prev_close = row["close"]
    return pd.DataFrame(rows)


def iter_nav_samples(nav: pd.DataFrame, oos_start: str) -> Iterable[Tuple[str, pd.DataFrame]]:
    if nav.empty:
        yield "Full", nav
        yield "IS", nav
        yield "OOS", nav
        return
    oos_dt = pd.to_datetime(oos_start)
    yield "Full", nav.copy()
    yield "IS", nav[nav["date"] < oos_dt].copy()
    yield "OOS", nav[nav["date"] >= oos_dt].copy()


def annualized_turnover(nav: pd.DataFrame) -> float:
    if nav.empty:
        return 0.0
    years = max(len(nav) / 252.0, 1e-9)
    return float(nav["turnover"].sum() / years)


def worst_drawdown_windows(nav: pd.DataFrame, top_n: int) -> List[Dict[str, Any]]:
    if nav.empty:
        return []
    g = nav[["date", "nav"]].copy().reset_index(drop=True)
    g["peak"] = g["nav"].cummax()
    g["drawdown"] = g["nav"] / g["peak"] - 1.0
    windows = []
    in_dd = False
    start_idx = 0
    for idx, row in g.iterrows():
        if row["drawdown"] < 0 and not in_dd:
            in_dd = True
            start_idx = max(idx - 1, 0)
        if in_dd and (row["drawdown"] == 0 or idx == len(g) - 1):
            end_idx = idx
            segment = g.iloc[start_idx : end_idx + 1]
            trough_idx = segment["drawdown"].idxmin()
            windows.append(
                {
                    "start": g.loc[start_idx, "date"].strftime("%Y-%m-%d"),
                    "trough": g.loc[trough_idx, "date"].strftime("%Y-%m-%d"),
                    "end": g.loc[end_idx, "date"].strftime("%Y-%m-%d"),
                    "drawdown": round(float(g.loc[trough_idx, "drawdown"]), 6),
                }
            )
            in_dd = False
    windows.sort(key=lambda item: item["drawdown"])
    return windows[:top_n]


def add_overlay_baseline_diffs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    key_cols = ["universe_variant", "sample", "target", "tc_bps"]
    own = out[out["strategy"] == "own_ma50"][key_cols + ["cagr", "sharpe"]].rename(
        columns={"cagr": "own_ma50_cagr", "sharpe": "own_ma50_sharpe"}
    )
    bh = out[out["strategy"] == "buy_hold"][key_cols + ["cagr", "sharpe"]].rename(
        columns={"cagr": "buy_hold_cagr", "sharpe": "buy_hold_sharpe"}
    )
    out = out.merge(own, on=key_cols, how="left").merge(bh, on=key_cols, how="left")
    out["excess_cagr_vs_own_ma50"] = out["cagr"] - out["own_ma50_cagr"]
    out["sharpe_diff_vs_own_ma50"] = out["sharpe"] - out["own_ma50_sharpe"]
    out["excess_cagr_vs_buy_hold"] = out["cagr"] - out["buy_hold_cagr"]
    out["sharpe_diff_vs_buy_hold"] = out["sharpe"] - out["buy_hold_sharpe"]
    return out


def write_report(
    report_path: Path,
    config: StudyConfig,
    daily_breadth: pd.DataFrame,
    coverage_audit: pd.DataFrame,
    state_results: pd.DataFrame,
    event_results: pd.DataFrame,
    overlay_results: pd.DataFrame,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    verdicts = build_verdicts(state_results, event_results, overlay_results)
    effective_daily = filter_effective_breadth(
        daily_breadth.assign(date=pd.to_datetime(daily_breadth["date"]))
    )
    latest = effective_daily.tail(1)
    latest_line = "n/a"
    if not latest.empty:
        row = latest.iloc[0]
        latest_line = (
            f"{row['date']}: MA20 breadth {row['breadth_20']:.1%}, "
            f"MA50 breadth {row['breadth_50']:.1%}, eligible {int(row['eligible_count_with_delisted_partial'])}"
        )

    body = [
        "# Broad Breadth Participation Signal Study — QQQ/SOXX",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Executive Summary",
        "",
        "This is a research artifact only. It does not create trading instructions and does not modify production logic.",
        "",
        "| Target | Verdict | Primary FDR | Best OOS breadth overlay @10 bps vs own MA50 | Reason |",
        "|---|---|---:|---:|---|",
    ]
    for verdict in verdicts:
        body.append(
            "| {target} | {verdict} | {primary_fdr} | {oos_delta} | {reason} |".format(
                target=verdict["target"],
                verdict=verdict["verdict"],
                primary_fdr=verdict["primary_fdr"],
                oos_delta=verdict["oos_delta"],
                reason=verdict["reason"],
            )
        )

    effective_start = coverage_audit["effective_sample_start"].dropna().iloc[0]
    effective_end = coverage_audit["effective_sample_end"].dropna().iloc[0]
    effective_audit = coverage_audit[
        (pd.to_datetime(coverage_audit["date"]) >= pd.to_datetime(effective_start))
        & (pd.to_datetime(coverage_audit["date"]) <= pd.to_datetime(effective_end))
    ]
    body.extend(
        [
            "",
            "## Study Protocol",
            "",
            f"- Universe: broad {_format_mcap(config.min_market_cap)}+ with active-only and with-delisted_partial variants.",
            f"- Effective sample: {effective_start} -> {effective_end}.",
            f"- OOS split: {config.oos_start}.",
            "- Primary hypotheses: H1/H2 level effects and H3/H4 events, QQQ/SOXX, 10d/20d, fixed 16-cell family.",
            "- P-values: HAC one-sided by pre-registered direction; bootstrap is a second gate.",
            "- Event low-N rule: N>=15 tested, 10<=N<15 supportive, N<10 not_tested; verdict FDR uses p=1.0 for N<15.",
            "",
            "## Coverage Audit",
            "",
            f"- Latest breadth: {latest_line}.",
            f"- Mean active eligible count: {effective_audit['eligible_count_active'].mean():.1f}.",
            f"- Mean with-delisted_partial eligible count: {effective_audit['eligible_count_with_delisted_partial'].mean():.1f}.",
            f"- Mean delisted overlay coverage ratio: {effective_audit['delisted_overlay_coverage_ratio'].mean():.1%}.",
            f"- Max PIT staleness exclusions/day: {int(effective_audit['pit_excluded_due_to_staleness_count'].max())}.",
            "",
            "## Forward Return Tests",
            "",
            _markdown_table(
                summarize_primary_results(state_results, event_results),
                [
                    "universe_variant",
                    "sample",
                    "hypothesis",
                    "target",
                    "horizon",
                    "diff_mean",
                    "hac_p_value",
                    "bootstrap_p_value",
                    "verdict_q",
                    "audit_q",
                    "cell_status",
                ],
            ),
            "",
            "## Overlay Backtest",
            "",
            _markdown_table(
                summarize_overlay_results(overlay_results),
                [
                    "universe_variant",
                    "sample",
                    "target",
                    "strategy",
                    "tc_bps",
                    "cagr",
                    "sharpe",
                    "max_drawdown",
                    "excess_cagr_vs_own_ma50",
                    "sharpe_diff_vs_own_ma50",
                    "turnover",
                ],
            ),
            "",
            "## Robustness Notes",
            "",
            "- Active-only and with-delisted_partial are both exported. A conflict downgrades the verdict.",
            "- Supplementary 5d/60d horizons are exported but not used in the primary FDR family.",
            "- Exploratory slices ($1B/$3B, EMA, industry subsets) are intentionally not included in this first execution.",
            "",
            "## Artifacts",
            "",
            "- `data/breadth_study/daily_breadth.csv`",
            "- `data/breadth_study/coverage_audit.csv`",
            "- `data/breadth_study/state_forward_returns.csv`",
            "- `data/breadth_study/event_forward_returns.csv`",
            "- `data/breadth_study/overlay_backtest.csv`",
            "- `data/breadth_study/sidecar/sidecar_coverage_report.md`",
            "",
        ]
    )
    report_path.write_text("\n".join(body), encoding="utf-8")


def summarize_primary_results(state_results: pd.DataFrame, event_results: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if not state_results.empty:
        frames.append(state_results[(state_results["row_type"] == "hypothesis") & (state_results["primary_family"])])
    if not event_results.empty:
        frames.append(event_results[event_results["primary_family"]])
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True, sort=False)
    df = df[df["sample"].isin(["Full", "IS", "OOS"])]
    return df.sort_values(["sample", "target", "hypothesis", "horizon"]).head(36)


def summarize_overlay_results(overlay_results: pd.DataFrame) -> pd.DataFrame:
    if overlay_results.empty:
        return overlay_results
    primary_strategies = [
        "buy_hold",
        "own_ma50",
        "spy_ma50",
        "equal_weight_ma50",
        "breadth_50_hard",
        "breadth_20_hard",
        "breadth_20_50_hard",
        "breadth_50_soft_w0",
    ]
    df = overlay_results[
        (overlay_results["tc_bps"] == 10)
        & (overlay_results["universe_variant"] == "with_delisted_partial")
        & (overlay_results["strategy"].isin(primary_strategies))
    ].copy()
    return df.sort_values(["target", "sample", "strategy"]).head(72)


def build_verdicts(
    state_results: pd.DataFrame,
    event_results: pd.DataFrame,
    overlay_results: pd.DataFrame,
) -> List[Dict[str, str]]:
    primary = summarize_primary_results(state_results, event_results)
    verdicts = []
    for target in PRIMARY_TARGETS:
        target_primary = primary[
            (primary["target"] == target)
            & (primary["universe_variant"] == "with_delisted_partial")
            & (primary["sample"] == "Full")
        ]
        significant = target_primary[
            (target_primary["verdict_q"] < 0.1)
            & (target_primary["bootstrap_p_value"] < 0.1)
            & (target_primary["cell_status"].isin(["tested", "supplementary"]))
        ]
        primary_fdr = (
            f"min q={target_primary['verdict_q'].min():.4f}"
            if not target_primary.empty and np.isfinite(target_primary["verdict_q"].min())
            else "n/a"
        )
        oos_delta = best_oos_overlay_delta(overlay_results, target)
        active_conflict = has_active_partial_conflict(primary, target)
        if not significant.empty and oos_delta["passes"] and not active_conflict:
            verdict = "可用（requires hardening）"
            reason = "FDR + bootstrap pass, OOS overlay clears own MA50 gate, active/partial direction aligns."
        elif not significant.empty:
            verdict = "仅解释"
            reason = "Primary signal exists, but overlay gate or active/partial consistency failed."
        else:
            verdict = "淘汰"
            reason = "No primary cell passed verdict FDR + bootstrap gate."
        verdicts.append(
            {
                "target": target,
                "verdict": verdict,
                "primary_fdr": primary_fdr,
                "oos_delta": oos_delta["label"],
                "reason": reason,
            }
        )
    return verdicts


def best_oos_overlay_delta(overlay_results: pd.DataFrame, target: str) -> Dict[str, Any]:
    if overlay_results.empty:
        return {"passes": False, "label": "n/a"}
    candidates = overlay_results[
        (overlay_results["target"] == target)
        & (overlay_results["sample"] == "OOS")
        & (overlay_results["tc_bps"] == 10)
        & (overlay_results["universe_variant"] == "with_delisted_partial")
        & (overlay_results["strategy"].str.startswith("breadth_"))
    ]
    if candidates.empty:
        return {"passes": False, "label": "n/a"}
    best = candidates.sort_values("excess_cagr_vs_own_ma50", ascending=False).iloc[0]
    cagr_delta = float(best["excess_cagr_vs_own_ma50"])
    sharpe_delta = float(best["sharpe_diff_vs_own_ma50"])
    passes = cagr_delta >= 0.02 and sharpe_delta >= 0.2
    return {
        "passes": passes,
        "label": f"{best['strategy']}: CAGR {cagr_delta:+.2%}, Sharpe {sharpe_delta:+.2f}",
    }


def has_active_partial_conflict(primary: pd.DataFrame, target: str) -> bool:
    if primary.empty:
        return False
    subset = primary[(primary["target"] == target) & (primary["sample"] == "Full")]
    for _, group in subset.groupby(["hypothesis", "horizon"]):
        active = group[group["universe_variant"] == "active_only"]
        partial = group[group["universe_variant"] == "with_delisted_partial"]
        if active.empty or partial.empty:
            continue
        a = float(active.iloc[0]["diff_mean"])
        p = float(partial.iloc[0]["diff_mean"])
        if np.isfinite(a) and np.isfinite(p) and np.sign(a) != np.sign(p):
            return True
    return False


def _markdown_table(df: pd.DataFrame, columns: Sequence[str]) -> str:
    if df.empty:
        return "_No rows._"
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = np.nan
    out = out[list(columns)].copy()
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].map(lambda value: "" if pd.isna(value) else f"{value:.4f}")
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = [header, sep]
    for _, row in out.iterrows():
        rows.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(rows)


def _format_mcap(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:g}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:g}M"
    return f"${value:g}"


def _one_sided_p(t_stat: float, df: int, alternative: str) -> float:
    if not np.isfinite(t_stat):
        return 1.0
    if alternative == "upper":
        return float(stats.t.sf(t_stat, df))
    if alternative == "lower":
        return float(stats.t.cdf(t_stat, df))
    raise ValueError(f"Unsupported alternative: {alternative}")


def _empty_stat(n: int, accepted_n: int, rejected_n: int) -> Dict[str, float]:
    return {
        "n": float(n),
        "accepted_n": float(accepted_n),
        "rejected_n": float(rejected_n),
        "accepted_mean": np.nan,
        "rejected_mean": np.nan,
        "diff": np.nan,
        "t_stat": np.nan,
        "p_value": 1.0,
        "bootstrap_p": 1.0,
        "bootstrap_ci_low": np.nan,
        "bootstrap_ci_high": np.nan,
    }


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return np.nan
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _min_date(rows: Sequence[Mapping[str, Any]]) -> str:
    dates = [str(row.get("date"))[:10] for row in rows if row.get("date")]
    return min(dates) if dates else ""


def _max_date(rows: Sequence[Mapping[str, Any]]) -> str:
    dates = [str(row.get("date"))[:10] for row in rows if row.get("date")]
    return max(dates) if dates else ""
