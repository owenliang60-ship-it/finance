#!/usr/bin/env python3
"""Staged, idempotent SOXX historical GAAP TTM PE backfill."""
import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_company_concept_registry import _backup_sqlite
from src.data.fmp_client import FMPClient
from src.data.fmp_forward_ingestion import (
    anchor_trading_date,
    infer_soxx_rebalance_close,
    load_basket_configs,
    load_soxx_symbol_aliases,
    normalize_fund_disclosure_snapshot,
)
from src.data.market_store import MarketStore
from terminal.historical_basket_valuation import (
    compute_daily_basket_valuation,
    select_four_continuous_asof_quarters,
)
from terminal.historical_market_cap_sanity import (
    build_forced_refresh_windows,
    scan_market_cap_candidates,
)


STAGE_ORDER = (
    "source", "fundamentals", "mcap", "splits", "mcap_sanity", "fx", "compute",
)
NETWORK_STAGES = frozenset({"source", "fundamentals", "mcap", "splits", "fx"})
CRITICAL_FAILURE_RATE = 0.20


@dataclass
class BackfillState:
    trading_dates: List[str] = field(default_factory=list)
    snapshots: List[Dict[str, Any]] = field(default_factory=list)
    income_by_symbol: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    market_cap_by_symbol: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    price_by_symbol: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    splits_by_symbol: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    fx_by_currency: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    sanity_by_symbol: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill SOXX historical rebalance-weighted GAAP TTM PE")
    parser.add_argument("--stage", choices=(*STAGE_ORDER, "all"), default="all")
    parser.add_argument("--from-date", type=_iso_date, required=True)
    parser.add_argument("--to-date", type=_iso_date, default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-network", action="store_true",
        help="Permit API calls during dry-run; non-dry network stages are explicit writes",
    )
    parser.add_argument("--refresh-live", action="store_true")
    parser.add_argument(
        "--db", type=Path, default=PROJECT_ROOT / "data" / "market.db")
    args = parser.parse_args(argv)
    if args.from_date > args.to_date:
        parser.error("--from-date must be on or before --to-date")
    return args


def failure_rate_exceeded(failure_count: int, total_count: int) -> bool:
    if total_count <= 0:
        return failure_count > 0
    return failure_count / total_count > CRITICAL_FAILURE_RATE


def fundamentals_complete(
    rows: Sequence[Mapping[str, Any]],
    from_date: str,
    to_date: str,
    trading_dates: Sequence[str],
) -> bool:
    return (
        select_four_continuous_asof_quarters(
            rows, from_date, trading_dates) is not None
        and select_four_continuous_asof_quarters(
            rows, to_date, trading_dates) is not None
    )


def market_cap_complete(
    rows: Sequence[Mapping[str, Any]],
    trading_dates: Sequence[str],
    from_date: str,
    to_date: str,
    max_staleness_days: int = 7,
) -> bool:
    observations = sorted(
        date.fromisoformat(str(row["date"])[:10])
        for row in rows if row.get("date") and row.get("market_cap") is not None)
    targets = [
        date.fromisoformat(value) for value in trading_dates
        if from_date <= value <= to_date
    ]
    if not observations or not targets:
        return False
    index = 0
    latest: Optional[date] = None
    for target in targets:
        while index < len(observations) and observations[index] <= target:
            latest = observations[index]
            index += 1
        if latest is None or (target - latest).days > max_staleness_days:
            return False
    return True


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", [table]
    ).fetchone()
    return row is not None


def _rows(conn: sqlite3.Connection, query: str,
          params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _snapshot_universe(snapshots: Sequence[Mapping[str, Any]]) -> List[str]:
    symbols = set()
    for row in snapshots:
        if row.get("included") == 1 and row.get("symbol"):
            symbols.add(str(row["symbol"]).upper())
        if row.get("covered_by"):
            symbols.add(str(row["covered_by"]).upper())
    return sorted(symbols)


def _hydrate_symbols(
    state: BackfillState, conn: sqlite3.Connection, symbols: Sequence[str],
) -> None:
    for symbol in symbols:
        state.income_by_symbol[symbol] = _rows(
            conn, "SELECT * FROM income_quarterly WHERE symbol = ? ORDER BY date",
            [symbol])
        state.market_cap_by_symbol[symbol] = _rows(
            conn, "SELECT * FROM historical_market_cap WHERE symbol = ? ORDER BY date",
            [symbol])
        state.price_by_symbol[symbol] = _rows(
            conn, "SELECT * FROM daily_price WHERE symbol = ? ORDER BY date",
            [symbol])
        if _table_exists(conn, "fmp_stock_splits"):
            state.splits_by_symbol[symbol] = _rows(
                conn, "SELECT * FROM fmp_stock_splits WHERE symbol = ? ORDER BY date",
                [symbol])
        else:
            state.splits_by_symbol[symbol] = []


def load_state(conn: sqlite3.Connection, to_date: str) -> BackfillState:
    state = BackfillState()
    state.trading_dates = [row["date"] for row in _rows(
        conn,
        "SELECT date FROM daily_price WHERE symbol = 'SOXX' AND date <= ? ORDER BY date",
        [to_date],
    )]
    if _table_exists(conn, "fmp_fund_disclosure_holdings"):
        state.snapshots = _rows(
            conn, "SELECT * FROM fmp_fund_disclosure_holdings "
            "WHERE basket_symbol = 'SOXX' ORDER BY holding_date, raw_row_index")
    symbols = _snapshot_universe(state.snapshots)
    _hydrate_symbols(state, conn, symbols)
    if _table_exists(conn, "fx_daily"):
        for row in _rows(conn, "SELECT * FROM fx_daily ORDER BY currency, date"):
            state.fx_by_currency.setdefault(row["currency"], []).append(row)
    return state


def open_write_dependencies(db_path: Path) -> Tuple[Optional[Path], MarketStore]:
    """Backup existing pages before MarketStore can initialize schema/WAL."""
    backup_path = _backup_sqlite(Path(db_path), "pre-soxx-historical-pe")
    return backup_path, MarketStore(Path(db_path))


def _attach_metadata(
    rows: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    fields = (
        "basket_symbol", "holding_date", "source_kind", "rebalance_close_date",
        "composition_effective_date", "composition_available_date", "fetched_at",
    )
    return [{**dict(row), **{field: metadata[field] for field in fields}}
            for row in rows]


def _replace_snapshot_in_state(
    state: BackfillState, rows: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any], refresh_live: bool,
) -> None:
    if metadata["source_kind"] == "live" and refresh_live:
        state.snapshots = [
            row for row in state.snapshots
            if not (row.get("source_kind") == "live"
                    and row.get("rebalance_close_date")
                    == metadata["rebalance_close_date"])
        ]
    else:
        state.snapshots = [
            row for row in state.snapshots
            if not (row.get("holding_date") == metadata["holding_date"]
                    and row.get("source_kind") == metadata["source_kind"])
        ]
    state.snapshots.extend(_attach_metadata(rows, metadata))


def _fetch_sources(
    args: argparse.Namespace,
    state: BackfillState,
    client: FMPClient,
    store: Optional[MarketStore],
    report: Dict[str, Any],
) -> None:
    if not state.trading_dates:
        raise ValueError("SOXX trading calendar is empty")
    listing, groups, _ = load_basket_configs(PROJECT_ROOT / "config" / "baskets")
    aliases = load_soxx_symbol_aliases(PROJECT_ROOT / "config" / "soxx_symbol_aliases.json")
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")
    existing = {
        (row["holding_date"], row["source_kind"])
        for row in state.snapshots
    }
    dates = client.get_fund_disclosure_dates("SOXX")
    if not dates:
        raise ValueError("FMP disclosure-date source is empty")
    added = 0
    skipped = 0
    for item in sorted(dates, key=lambda value: value["date"]):
        holding_date = str(item["date"])
        if (holding_date, "disclosure") in existing:
            skipped += 1
            continue
        raw = client.get_fund_disclosure(
            "SOXX", int(item["year"]), int(item["quarter"]))
        normalized, metadata = normalize_fund_disclosure_snapshot(
            "SOXX", raw, "disclosure", fetched_at, state.trading_dates,
            listing, groups, aliases)
        if store is not None:
            store.replace_fund_disclosure_snapshot(
                "SOXX", metadata["holding_date"], "disclosure", normalized,
                rebalance_close_date=metadata["rebalance_close_date"],
                composition_effective_date=metadata["composition_effective_date"],
                composition_available_date=metadata["composition_available_date"],
                fetched_at=fetched_at)
        _replace_snapshot_in_state(state, normalized, metadata, False)
        added += 1

    fetch_date = fetched_at[:10]
    live_rebalance = infer_soxx_rebalance_close(fetch_date)
    live_exists = any(
        row.get("source_kind") == "live"
        and row.get("rebalance_close_date") == live_rebalance
        for row in state.snapshots)
    if not live_exists or args.refresh_live:
        raw_live = client.get_etf_holdings("SOXX")
        normalized, metadata = normalize_fund_disclosure_snapshot(
            "SOXX", raw_live, "live", fetched_at, state.trading_dates,
            listing, groups, aliases)
        if store is not None:
            store.replace_fund_disclosure_snapshot(
                "SOXX", metadata["holding_date"], "live", normalized,
                rebalance_close_date=metadata["rebalance_close_date"],
                composition_effective_date=metadata["composition_effective_date"],
                composition_available_date=metadata["composition_available_date"],
                fetched_at=fetched_at, refresh_live=args.refresh_live)
        _replace_snapshot_in_state(
            state, normalized, metadata, args.refresh_live)
        added += 1
    else:
        skipped += 1
    report["stages"]["source"] = {"added": added, "skipped": skipped}


def _check_fuse(stage: str, failures: List[str], total: int) -> None:
    if failure_rate_exceeded(len(failures), total):
        raise RuntimeError(
            f"{stage} failure fuse tripped: {len(failures)}/{total} > 20%")


def _run_fundamentals(
    args: argparse.Namespace, state: BackfillState, symbols: Sequence[str],
    client: Optional[FMPClient], store: Optional[MarketStore], report: Dict[str, Any],
) -> None:
    missing = [symbol for symbol in symbols if not fundamentals_complete(
        state.income_by_symbol.get(symbol, []), args.from_date, args.to_date,
        state.trading_dates)]
    if client is None:
        report["planned_network_calls"].extend(
            {"stage": "fundamentals", "symbol": symbol} for symbol in missing)
        report["stages"]["fundamentals"] = {
            "complete": len(symbols) - len(missing), "planned": len(missing)}
        return
    failures = []
    fetched = 0
    for symbol in missing:
        rows = client.get_income_statement(symbol, period="quarter", limit=40)
        if not rows:
            failures.append(symbol)
            continue
        state.income_by_symbol[symbol] = list(rows)
        if store is not None:
            store.upsert_income(symbol, list(rows))
        fetched += 1
    _check_fuse("fundamentals", failures, len(symbols))
    report["stages"]["fundamentals"] = {
        "complete": len(symbols) - len(missing), "fetched": fetched,
        "failed": failures}


def _replace_rows_in_memory(
    existing: Sequence[Mapping[str, Any]], from_date: str, to_date: str,
    new_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    kept = [dict(row) for row in existing
            if not from_date <= str(row["date"]) <= to_date]
    kept.extend(dict(row) for row in new_rows)
    return sorted(kept, key=lambda row: row["date"])


def _run_mcap(
    args: argparse.Namespace, state: BackfillState, symbols: Sequence[str],
    client: Optional[FMPClient], store: Optional[MarketStore], report: Dict[str, Any],
) -> None:
    missing = [symbol for symbol in symbols if not market_cap_complete(
        state.market_cap_by_symbol.get(symbol, []), state.trading_dates,
        args.from_date, args.to_date)]
    fetch_from = (date.fromisoformat(args.from_date) - timedelta(days=7)).isoformat()
    if client is None:
        report["planned_network_calls"].extend(
            {"stage": "mcap", "symbol": symbol,
             "from_date": fetch_from, "to_date": args.to_date}
            for symbol in missing)
        report["stages"]["mcap"] = {
            "complete": len(symbols) - len(missing), "planned": len(missing)}
        return
    failures = []
    fetched = 0
    for symbol in missing:
        rows = client.get_historical_market_cap(
            symbol, from_date=fetch_from, to_date=args.to_date)
        if not rows:
            failures.append(symbol)
            continue
        state.market_cap_by_symbol[symbol] = _replace_rows_in_memory(
            state.market_cap_by_symbol.get(symbol, []), fetch_from, args.to_date, rows)
        if store is not None:
            store.replace_historical_market_cap_range(
                symbol, fetch_from, args.to_date, list(rows))
        fetched += 1
    _check_fuse("mcap", failures, len(symbols))
    report["stages"]["mcap"] = {
        "complete": len(symbols) - len(missing), "fetched": fetched,
        "failed": failures}


def _run_splits(
    state: BackfillState, symbols: Sequence[str], client: Optional[FMPClient],
    store: Optional[MarketStore], report: Dict[str, Any],
) -> None:
    if client is None:
        report["planned_network_calls"].extend(
            {"stage": "splits", "symbol": symbol} for symbol in symbols)
        report["stages"]["splits"] = {"planned": len(symbols)}
        return
    for symbol in symbols:
        rows = client.get_stock_splits(symbol)
        state.splits_by_symbol[symbol] = list(rows)
        if store is not None:
            store.replace_stock_splits(symbol, list(rows))
    report["stages"]["splits"] = {"fetched": len(symbols)}


def _run_sanity(
    args: argparse.Namespace, state: BackfillState, symbols: Sequence[str],
    client: Optional[FMPClient], store: Optional[MarketStore], report: Dict[str, Any],
) -> None:
    summary: Dict[str, Any] = {}
    for symbol in symbols:
        classifications = scan_market_cap_candidates(
            state.market_cap_by_symbol.get(symbol, []),
            state.price_by_symbol.get(symbol, []),
            state.splits_by_symbol.get(symbol, []))
        relevant = [row for row in classifications
                    if args.from_date <= row["date"] <= args.to_date]
        # A vendor can publish a market-cap row on a date missing from the
        # local price series. Keep that row in the refresh calendar so the
        # anomaly is quarantined/refetched instead of crashing the planner.
        trading = sorted({
            *[str(row["date"]) for row in state.price_by_symbol.get(symbol, [])],
            *[str(row["date"]) for row in relevant],
        })
        windows = build_forced_refresh_windows(relevant, trading, pad=5) if trading else []
        refreshed = 0
        if windows and client is not None:
            for window in windows:
                rows = client.get_historical_market_cap(
                    symbol, from_date=window["from_date"], to_date=window["to_date"])
                if not rows:
                    continue
                state.market_cap_by_symbol[symbol] = _replace_rows_in_memory(
                    state.market_cap_by_symbol.get(symbol, []),
                    window["from_date"], window["to_date"], rows)
                if store is not None:
                    store.replace_historical_market_cap_range(
                        symbol, window["from_date"], window["to_date"], list(rows))
                refreshed += 1
            classifications = scan_market_cap_candidates(
                state.market_cap_by_symbol.get(symbol, []),
                state.price_by_symbol.get(symbol, []),
                state.splits_by_symbol.get(symbol, []))
            relevant = [row for row in classifications
                        if args.from_date <= row["date"] <= args.to_date]
        elif windows:
            report["planned_network_calls"].extend(
                {"stage": "mcap_sanity", "symbol": symbol, **window}
                for window in windows)
        state.sanity_by_symbol[symbol] = classifications
        quarantined = [row["date"] for row in relevant
                       if row["status"] in {"invalid_mcap", "unresolved"}]
        summary[symbol] = {
            "flagged": sum(row["candidate"] for row in relevant),
            "refresh_windows": windows,
            "refreshed": refreshed,
            "quarantined": quarantined,
        }
    report["stages"]["mcap_sanity"] = summary


def _currencies(state: BackfillState, symbols: Sequence[str]) -> List[str]:
    values = set()
    for symbol in symbols:
        for row in state.income_by_symbol.get(symbol, []):
            currency = row.get("reported_currency", row.get("reportedCurrency"))
            if currency:
                values.add(str(currency).upper())
    values.discard("USD")
    return sorted(values)


def _fx_complete(
    rows: Sequence[Mapping[str, Any]], trading_dates: Sequence[str],
    from_date: str, to_date: str,
) -> bool:
    proxy = [{"date": row["date"], "market_cap": row.get("usd_per_unit")}
             for row in rows]
    return market_cap_complete(proxy, trading_dates, from_date, to_date)


def _run_fx(
    args: argparse.Namespace, state: BackfillState, symbols: Sequence[str],
    client: Optional[FMPClient], store: Optional[MarketStore], report: Dict[str, Any],
) -> None:
    currencies = _currencies(state, symbols)
    missing = [currency for currency in currencies if not _fx_complete(
        state.fx_by_currency.get(currency, []), state.trading_dates,
        args.from_date, args.to_date)]
    fetch_from = (date.fromisoformat(args.from_date) - timedelta(days=7)).isoformat()
    if client is None:
        report["planned_network_calls"].extend(
            {"stage": "fx", "currency": currency,
             "from_date": fetch_from, "to_date": args.to_date}
            for currency in missing)
        report["stages"]["fx"] = {
            "currencies": currencies, "planned": missing}
        return
    failures = []
    for currency in missing:
        raw = client.get_historical_fx(
            f"{currency}USD", fetch_from, args.to_date)
        normalized = [{
            "currency": currency, "date": row["date"],
            "usd_per_unit": row["close"], "source_symbol": f"{currency}USD",
            "source": "fmp",
        } for row in raw if row.get("date") and row.get("close")]
        if not normalized:
            failures.append(currency)
            continue
        state.fx_by_currency[currency] = normalized
        if store is not None:
            store.upsert_fx_daily(normalized)
    _check_fuse("fx", failures, len(currencies))
    report["stages"]["fx"] = {
        "currencies": currencies, "fetched": len(missing) - len(failures),
        "failed": failures}


def _snapshot_groups(
    snapshots: Sequence[Mapping[str, Any]], trading_dates: Sequence[str],
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in snapshots:
        key = (str(row["holding_date"]), str(row["source_kind"]))
        grouped.setdefault(key, []).append(dict(row))
    result = []
    for (_, source_kind), rows in grouped.items():
        first = rows[0]
        composition = {
            "holding_date": first["holding_date"],
            "anchor_trading_date": anchor_trading_date(
                first["holding_date"], trading_dates),
            "composition_effective_date": first["composition_effective_date"],
            "composition_available_date": first["composition_available_date"],
            "weight_basis": (
                "live_snapshot_backcast_proxy" if source_kind == "live"
                else "fixed_rebalance_weight_proxy"),
            "data_quality_tier": (
                "live_tail_weaker" if source_kind == "live"
                else "historical_disclosure_fixed_proxy"),
        }
        result.append((composition, sorted(rows, key=lambda row: row["raw_row_index"])))
    return sorted(result, key=lambda item: (
        item[0]["composition_effective_date"], item[0]["holding_date"]))


def _run_compute(
    args: argparse.Namespace, state: BackfillState, symbols: Sequence[str],
    store: Optional[MarketStore], report: Dict[str, Any],
) -> None:
    for symbol in symbols:
        if symbol not in state.sanity_by_symbol:
            state.sanity_by_symbol[symbol] = scan_market_cap_candidates(
                state.market_cap_by_symbol.get(symbol, []),
                state.price_by_symbol.get(symbol, []),
                state.splits_by_symbol.get(symbol, []))
    groups = _snapshot_groups(state.snapshots, state.trading_dates)
    dates = [value for value in state.trading_dates
             if args.from_date <= value <= args.to_date]
    outputs = []
    for valuation_date in dates:
        eligible = [item for item in groups
                    if item[0]["composition_effective_date"] <= valuation_date]
        if not eligible:
            continue
        composition, holding_rows = eligible[-1]
        outputs.append(compute_daily_basket_valuation(
            valuation_date=valuation_date,
            holding_rows=holding_rows,
            income_by_symbol=state.income_by_symbol,
            market_cap_by_symbol=state.market_cap_by_symbol,
            fx_by_currency=state.fx_by_currency,
            sanity_by_symbol=state.sanity_by_symbol,
            trading_dates=state.trading_dates,
            composition=composition,
        ))
    if store is not None:
        store.replace_basket_ttm_valuation_range(
            "SOXX", args.from_date, args.to_date, outputs)
    publishable = sum(
        row["rebalance_weighted_ttm_pe_gaap_proxy"] is not None for row in outputs)
    report["coverage_7d"] = {
        "trading_dates": len(dates),
        "computed_dates": len(outputs),
        "publishable_dates": publishable,
        "publishable_pct": publishable / len(dates) * 100 if dates else 0.0,
        "target_passed": bool(dates and publishable / len(dates) >= 0.95),
    }
    report["stages"]["compute"] = {
        "rows": len(outputs), "publishable": publishable}


def run_backfill(
    args: argparse.Namespace,
    state: BackfillState,
    client: Optional[FMPClient] = None,
    store: Optional[MarketStore] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "dry_run": bool(args.dry_run),
        "network_enabled": client is not None,
        "from_date": args.from_date,
        "to_date": args.to_date,
        "stages": {},
        "planned_network_calls": [],
        "coverage_7d": {},
    }
    stages = STAGE_ORDER if args.stage == "all" else (args.stage,)
    if "source" in stages:
        if client is None:
            report["planned_network_calls"].append(
                {"stage": "source", "endpoint": "fund disclosures/live holdings"})
            report["stages"]["source"] = {"planned": True}
        else:
            _fetch_sources(args, state, client, store, report)

    symbols = _snapshot_universe(state.snapshots)
    if not symbols:
        raise ValueError("historical basket universe is empty")
    if conn is not None:
        _hydrate_symbols(state, conn, symbols)
    report["universe_count"] = len(symbols)

    if "fundamentals" in stages:
        _run_fundamentals(args, state, symbols, client, store, report)
    if "mcap" in stages:
        _run_mcap(args, state, symbols, client, store, report)
    if "splits" in stages:
        _run_splits(state, symbols, client, store, report)
    if "mcap_sanity" in stages:
        _run_sanity(args, state, symbols, client, store, report)
    if "fx" in stages:
        _run_fx(args, state, symbols, client, store, report)
    if "compute" in stages:
        _run_compute(args, state, symbols, store, report)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    conn: Optional[sqlite3.Connection] = None
    store: Optional[MarketStore] = None
    backup_path: Optional[Path] = None
    try:
        if args.dry_run:
            if not args.db.exists():
                raise FileNotFoundError(f"market.db not found: {args.db}")
            conn = _connect_ro(args.db)
            state = load_state(conn, args.to_date)
            client = FMPClient() if args.allow_network else None
        else:
            backup_path, store = open_write_dependencies(args.db)
            state = load_state(store._get_conn(), args.to_date)
            client = FMPClient() if (
                args.stage == "all" or args.stage in NETWORK_STAGES
                or args.stage == "mcap_sanity") else None
        report = run_backfill(
            args, state, client=client, store=store,
            conn=conn if conn is not None else store._get_conn() if store else None)
        report["backup_path"] = str(backup_path) if backup_path else None
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
