#!/usr/bin/env python3
"""Staged, idempotent SOXX historical GAAP TTM PE backfill."""
import argparse
import json
import logging
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_company_concept_registry import _backup_sqlite
from src.data.fmp_client import FMPClient, FMPResponseError
from src.data.fmp_forward_ingestion import (
    _SOXX_REBALANCE_MONTHS,
    anchor_trading_date,
    infer_basket_rebalance_close,
    load_basket_configs,
    load_soxx_symbol_aliases,
    normalize_fund_disclosure_snapshot,
)
from src.data.fx_validation import (
    is_plausible_usd_per_unit,
    validate_usd_per_unit,
)
from src.data.market_store import MarketStore
from terminal.historical_basket_valuation import (
    compute_daily_basket_valuation,
    select_four_continuous_asof_quarters,
)
from terminal.historical_market_cap_sanity import (
    accepted_market_cap_status,
    build_forced_refresh_windows,
    refresh_market_cap_windows,
    scan_market_cap_candidates,
)


STAGE_ORDER = (
    "source", "fundamentals", "mcap", "splits", "mcap_sanity", "fx", "compute",
)
NETWORK_STAGES = frozenset({"source", "fundamentals", "mcap", "splits", "fx"})
CRITICAL_FAILURE_RATE = 0.20
logger = logging.getLogger(__name__)


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
    # Consensus rows for the hindsight tail. Only the three-index weekly
    # backfill populates this; the SOXX TTM line never reads it.
    estimates_by_symbol: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=dict)


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
            if row.get("alias_mode") == "authoritative" and row.get("alias_symbol"):
                symbols.add(str(row["alias_symbol"]).upper())
            else:
                symbols.add(str(row["symbol"]).upper())
        if row.get("covered_by"):
            symbols.add(str(row["covered_by"]).upper())
        if (row.get("alias_symbol")
                and row.get("alias_mode") != "authoritative"):
            symbols.add(str(row["alias_symbol"]).upper())
    return sorted(symbols)


def _snapshot_member_universe(
    snapshots: Sequence[Mapping[str, Any]],
) -> List[str]:
    """Return disclosed economic members, excluding alias-only support symbols."""
    symbols = set()
    for row in snapshots:
        if row.get("included") == 1 and row.get("symbol"):
            symbols.add(str(row["symbol"]).upper())
        if row.get("covered_by"):
            symbols.add(str(row["covered_by"]).upper())
    return sorted(symbols)


def validate_snapshot_quality(
    rows: Sequence[Mapping[str, Any]],
    *,
    minimum_members: int = 25,
    maximum_members: int = 31,
    minimum_weight: float = 99.5,
    maximum_weight: float = 100.5,
) -> Dict[str, Any]:
    """Block truncated/partial snapshots before weights can be renormalized.

    Defaults are SOXX's audited bounds (~25-31 holdings). Any other basket
    (SPY has ~500+ rows, QQQ ~100+) must pass its own bounds explicitly --
    see the ``snapshot_quality`` block per basket in
    ``config/baskets/index_pe_baskets.json``, the SSOT for those numbers.
    """
    weights = []
    for row in rows:
        try:
            weights.append(float(row.get("weight_pct")))
        except (TypeError, ValueError):
            raise ValueError("snapshot contains non-numeric source weight") from None
    members = sum(bool(row.get("included") == 1 or row.get("covered_by"))
                  for row in rows)
    weight_sum = sum(weights)
    detail = {"members": members, "raw_weight_sum": weight_sum}
    if not minimum_members <= members <= maximum_members:
        raise ValueError(
            f"snapshot member count outside {minimum_members}-{maximum_members}: "
            f"{members}")
    if not minimum_weight <= weight_sum <= maximum_weight:
        raise ValueError(
            f"snapshot raw weight outside {minimum_weight}-{maximum_weight}: "
            f"{weight_sum:.6f}")
    return detail


def _previous_snapshot_symbols(
    snapshots: Sequence[Mapping[str, Any]], holding_date: str,
) -> Optional[Set[str]]:
    prior_dates = sorted({
        str(row["holding_date"]) for row in snapshots
        if row.get("source_kind") == "disclosure"
        and str(row.get("holding_date")) < holding_date
    })
    if not prior_dates:
        return None
    previous_date = prior_dates[-1]
    return {
        str(row.get("raw_symbol") or row.get("symbol")
            or row.get("covered_by")).upper()
        for row in snapshots
        if row.get("source_kind") == "disclosure"
        and row.get("holding_date") == previous_date
        and (row.get("included") == 1 or row.get("covered_by"))
        and (row.get("raw_symbol") or row.get("symbol")
             or row.get("covered_by"))
    }


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


def load_state(
    conn: sqlite3.Connection, to_date: str, basket_symbol: str = "SOXX",
) -> BackfillState:
    """Hydrate one basket's source state. The ETF's own price series is the
    trading calendar, so every basket carries its own listing history."""
    basket = basket_symbol.upper()
    state = BackfillState()
    state.trading_dates = [row["date"] for row in _rows(
        conn,
        "SELECT date FROM daily_price WHERE symbol = ? AND date <= ? ORDER BY date",
        [basket, to_date],
    )]
    if _table_exists(conn, "fmp_fund_disclosure_holdings"):
        state.snapshots = _rows(
            conn, "SELECT * FROM fmp_fund_disclosure_holdings "
            "WHERE basket_symbol = ? ORDER BY holding_date, raw_row_index",
            [basket])
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


def basket_snapshot_rules(
    basket_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Calendar + snapshot-plausibility rules for one basket.

    Defaults are SOXX's audited values, so every pre-existing SOXX call site
    behaves exactly as before; SPY/QQQ pass their own entry from
    ``config/baskets/index_pe_baskets.json``.
    """
    config = dict(basket_config or {})
    quality = dict(config.get("snapshot_quality") or {})
    return {
        "rebalance_months": tuple(
            config.get("rebalance_months") or _SOXX_REBALANCE_MONTHS),
        "expected_reconstitution_month": (
            config.get("expected_reconstitution_month", 9)
            if basket_config is not None else 9),
        "snapshot_quality": {
            "minimum_members": quality.get("minimum_members", 25),
            "maximum_members": quality.get("maximum_members", 31),
            "minimum_weight": quality.get("minimum_weight", 99.5),
            "maximum_weight": quality.get("maximum_weight", 100.5),
        },
    }


def resolve_config_paths(config_dir: Optional[Path] = None) -> Dict[str, Path]:
    """Locate the basket config root and the symbol-alias file under it.

    One root per run. The share-class groups read here decide which ticker is a
    secondary class whose weight folds into its primary; the weekly product
    layer reads the same file to decide how that company's market cap is
    composed. Two roots in one run could merge weights for one set of pairs and
    market caps for another -- the exact double-count the convention mechanism
    exists to prevent.

    The alias file sits beside the config root in the repo
    (`config/soxx_symbol_aliases.json` next to `config/baskets/`), so a
    self-contained root may also carry its own copy inside.
    """
    root = Path(config_dir) if config_dir is not None \
        else PROJECT_ROOT / "config" / "baskets"
    aliases = root / "soxx_symbol_aliases.json"
    if not aliases.exists():
        aliases = root.parent / "soxx_symbol_aliases.json"
    return {"baskets": root, "aliases": aliases}


def _fetch_sources(
    args: argparse.Namespace,
    state: BackfillState,
    client: FMPClient,
    store: Optional[MarketStore],
    report: Dict[str, Any],
    basket_symbol: str = "SOXX",
    basket_config: Optional[Mapping[str, Any]] = None,
    config_dir: Optional[Path] = None,
) -> None:
    basket = basket_symbol.upper()
    if not state.trading_dates:
        raise ValueError(f"{basket} trading calendar is empty")
    rules = basket_snapshot_rules(basket_config)
    paths = resolve_config_paths(config_dir)
    listing, groups, _ = load_basket_configs(paths["baskets"])
    aliases = load_soxx_symbol_aliases(paths["aliases"])
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")
    existing = {
        (row["holding_date"], row["source_kind"])
        for row in state.snapshots
    }
    dates = client.get_fund_disclosure_dates(basket)
    if not dates:
        raise ValueError("FMP disclosure-date source is empty")
    added = 0
    skipped = 0
    quality = []
    ordered = sorted(dates, key=lambda value: value["date"])
    # Preserve two predecessor quarters for the public-composition lag at the
    # left boundary, not the provider's entire fund history (SPY/QQQ starts in
    # 2019 even when the requested/available price history starts years later).
    predecessors = [item for item in ordered if str(item["date"]) < args.from_date][-2:]
    required = predecessors + [item for item in ordered
                               if args.from_date <= str(item["date"]) <= args.to_date]
    calendar_start = min(state.trading_dates)
    for item in required:
        holding_date = str(item["date"])
        if holding_date < calendar_start:
            skipped += 1
            report.setdefault("warnings", []).append(
                f"disclosure_before_price_calendar:{basket}:{holding_date}")
            continue
        if (holding_date, "disclosure") in existing:
            skipped += 1
            continue
        raw = client.get_fund_disclosure(
            basket, int(item["year"]), int(item["quarter"]))
        previous_symbols = _previous_snapshot_symbols(
            state.snapshots, holding_date)
        normalized, metadata = normalize_fund_disclosure_snapshot(
            basket, raw, "disclosure", fetched_at, state.trading_dates,
            listing, groups, aliases, previous_symbols=previous_symbols,
            rebalance_months=rules["rebalance_months"],
            expected_reconstitution_month=rules["expected_reconstitution_month"])
        quality.append({"holding_date": holding_date,
                        "source_kind": metadata["source_kind"],
                        "data_quality_tier": metadata["data_quality_tier"],
                        "warnings": metadata["warnings"],
                        **validate_snapshot_quality(
                            normalized, **rules["snapshot_quality"])})
        if store is not None:
            store.replace_fund_disclosure_snapshot(
                basket, metadata["holding_date"], "disclosure", normalized,
                rebalance_close_date=metadata["rebalance_close_date"],
                composition_effective_date=metadata["composition_effective_date"],
                composition_available_date=metadata["composition_available_date"],
                fetched_at=fetched_at)
        _replace_snapshot_in_state(state, normalized, metadata, False)
        added += 1

    fetch_date = fetched_at[:10]
    live_rebalance = infer_basket_rebalance_close(
        fetch_date, rules["rebalance_months"])
    first_session = date.fromisoformat(live_rebalance) + timedelta(days=1)
    while first_session.weekday() >= 5:
        first_session += timedelta(days=1)
    # Conservative cutoff: 20:00 UTC is no later than the regular US close.
    # Holidays / winter-time gaps fail closed after this boundary.
    expected_close = datetime.combine(
        first_session, datetime.min.time(), tzinfo=timezone.utc).replace(hour=20)
    live_deferred = None
    if (max(state.trading_dates) == live_rebalance
            and datetime.fromisoformat(fetched_at.replace("Z", "+00:00")) < expected_close):
        live_deferred = {
            "rebalance_close_date": live_rebalance,
            "expected_first_session": first_session.isoformat(),
            "expected_close_utc": expected_close.isoformat().replace("+00:00", "Z"),
            "fetched_at": fetched_at, "calendar_end": max(state.trading_dates),
        }
    live_exists = any(
        row.get("source_kind") == "live"
        and row.get("rebalance_close_date") == live_rebalance
        for row in state.snapshots)
    if live_deferred:
        skipped += 1
    elif not live_exists or args.refresh_live:
        raw_live = client.get_etf_holdings(basket)
        normalized, metadata = normalize_fund_disclosure_snapshot(
            basket, raw_live, "live", fetched_at, state.trading_dates,
            listing, groups, aliases,
            previous_symbols=_previous_snapshot_symbols(
                state.snapshots, fetch_date) or None,
            rebalance_months=rules["rebalance_months"],
            expected_reconstitution_month=rules["expected_reconstitution_month"])
        quality.append({"holding_date": metadata["holding_date"],
                        "source_kind": metadata["source_kind"],
                        "data_quality_tier": metadata["data_quality_tier"],
                        "warnings": metadata["warnings"],
                        **validate_snapshot_quality(
                            normalized, **rules["snapshot_quality"])})
        if store is not None:
            store.replace_fund_disclosure_snapshot(
                basket, metadata["holding_date"], "live", normalized,
                rebalance_close_date=metadata["rebalance_close_date"],
                composition_effective_date=metadata["composition_effective_date"],
                composition_available_date=metadata["composition_available_date"],
                fetched_at=fetched_at, refresh_live=args.refresh_live)
        _replace_snapshot_in_state(
            state, normalized, metadata, args.refresh_live)
        added += 1
    else:
        skipped += 1
    report["stages"]["source"] = {
        "added": added, "skipped": skipped, "snapshot_quality": quality}
    if live_deferred:
        report["stages"]["source"]["live_deferred"] = live_deferred


def _check_fuse(stage: str, failures: List[str], total: int) -> None:
    if failure_rate_exceeded(len(failures), total):
        raise RuntimeError(
            f"{stage} failure fuse tripped: {len(failures)}/{total} > 20%")


def _run_fundamentals(
    args: argparse.Namespace, state: BackfillState, symbols: Sequence[str],
    client: Optional[FMPClient], store: Optional[MarketStore], report: Dict[str, Any],
    fuse_on_incompleteness: bool = True,
) -> None:
    """Fetch every member whose visible quarterly history has a hole.

    `fuse_on_incompleteness` is the SOXX contract: over a window chosen so the
    data exists, a member still missing four visible quarters at either end is
    a failure, measured against the whole basket. It does not transfer to a
    five-year, several-hundred-member window, where a 2023 IPO is legitimately
    incomplete at the window start and no amount of refetching will change
    that. The three-index weekly backfill therefore fuses on empty vendor
    responses only and lets the per-point coverage gates decide which early
    dates are publishable.

    That second fuse is measured against the members actually **attempted**,
    not the universe. A vendor outage only shows up in the responses to the
    requests a run made: 60 empty responses to 60 requests is a total outage,
    and dividing it by 500 untouched members would report 12% and publish.
    """
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
    final_incomplete = [symbol for symbol in symbols if not fundamentals_complete(
        state.income_by_symbol.get(symbol, []), args.from_date, args.to_date,
        state.trading_dates)]
    if fuse_on_incompleteness:
        _check_fuse("fundamentals", final_incomplete, len(symbols))
    else:
        _check_fuse("fundamentals empty-response", failures, len(missing))
    final_complete = len(symbols) - len(final_incomplete)
    report["stages"]["fundamentals"] = {
        "preexisting_complete": len(symbols) - len(missing),
        "complete": final_complete, "fetched": fetched,
        "rows_available": sum(bool(state.income_by_symbol.get(symbol))
                              for symbol in symbols),
        "empty_responses": failures,
        "incomplete": final_incomplete,
        "failed": final_incomplete}


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
    fuse_on_incompleteness: bool = True,
) -> None:
    """Refetch every member whose as-of market-cap chain has a gap.

    See `_run_fundamentals` for why the three-index weekly backfill fuses on
    failed vendor responses instead of residual incompleteness -- a member
    listed part-way through the window cannot be made complete at its start --
    and why that fuse counts the members attempted rather than the universe.
    Invalid nonempty ranges count as failures too; memory and storage both
    preserve the previous range, with an explicit rejection reason.
    """
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
    invalid_responses = {}
    fetched = 0
    for symbol in missing:
        rows = client.get_historical_market_cap(
            symbol, from_date=fetch_from, to_date=args.to_date)
        if not rows:
            failures.append(symbol)
            continue
        try:
            MarketStore.prepare_historical_market_cap_range(
                symbol, fetch_from, args.to_date, list(rows))
        except ValueError as exc:
            invalid_responses[symbol] = str(exc)
            logger.warning("invalid market-cap response for %s; prior range preserved: %s",
                           symbol, exc)
            continue
        if store is not None:
            store.replace_historical_market_cap_range(
                symbol, fetch_from, args.to_date, list(rows))
        # Do not make memory appear newer than storage if a write failed.
        state.market_cap_by_symbol[symbol] = _replace_rows_in_memory(
            state.market_cap_by_symbol.get(symbol, []), fetch_from, args.to_date, rows)
        fetched += 1
    final_incomplete = [symbol for symbol in symbols if not market_cap_complete(
        state.market_cap_by_symbol.get(symbol, []), state.trading_dates,
        args.from_date, args.to_date)]
    final_complete = len(symbols) - len(final_incomplete)
    report["stages"]["mcap"] = {
        "preexisting_complete": len(symbols) - len(missing),
        "complete": final_complete, "fetched": fetched,
        "rows_available": sum(bool(state.market_cap_by_symbol.get(symbol))
                              for symbol in symbols),
        "empty_responses": failures,
        "invalid_responses": invalid_responses,
        "incomplete": final_incomplete,
        "failed": final_incomplete}
    if fuse_on_incompleteness:
        _check_fuse("mcap", final_incomplete, len(symbols))
    else:
        _check_fuse("mcap failed-response", failures + list(invalid_responses), len(missing))


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
        pre_status = {str(row["date"]): str(row["status"])
                      for row in classifications}
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
        refreshed_windows: List[Dict[str, Any]] = []
        if windows and client is not None:
            refresh_results = refresh_market_cap_windows(
                symbol, windows, client, store,
                existing_rows=state.market_cap_by_symbol.get(symbol, []))
            for window, outcome in zip(windows, refresh_results):
                # Provenance is recorded for skipped windows too: a refresh
                # that changed nothing is evidence, not absence of evidence.
                provenance = {
                    **dict(window),
                    "symbol": symbol,
                    "skipped": outcome["skipped"],
                    "response_rows": outcome["rows"],
                    "pre_row_hash": outcome["pre_row_hash"],
                    "post_row_hash": outcome["post_row_hash"],
                    "pre_row_count": outcome["pre_row_count"],
                    "post_row_count": outcome["post_row_count"],
                }
                if outcome.get("rejection_reason"):
                    provenance["rejection_reason"] = outcome["rejection_reason"]
                report.setdefault("forced_refresh_provenance", []).append(
                    provenance)
                if outcome["skipped"]:
                    continue
                state.market_cap_by_symbol[symbol] = _replace_rows_in_memory(
                    state.market_cap_by_symbol.get(symbol, []),
                    window["from_date"], window["to_date"], outcome["row_data"])
                refreshed_windows.append(provenance)
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
        enriched = []
        for row in classifications:
            matching = [window for window in windows
                        if window["from_date"] <= row["date"] <= window["to_date"]]
            enriched.append({
                **row,
                "pre_refresh_status": pre_status.get(str(row["date"])),
                "forced_refresh_planned": bool(matching),
                "forced_refresh_attempted": bool(matching and client is not None),
                "refresh_succeeded": any(
                    window["from_date"] <= row["date"] <= window["to_date"]
                    for window in refreshed_windows
                ),
                "refresh_windows": matching,
                "quarantined": not accepted_market_cap_status(str(row["status"])),
            })
        classifications = enriched
        state.sanity_by_symbol[symbol] = classifications
        quarantined = [row["date"] for row in relevant
                       if row["status"] in {"invalid_mcap", "unresolved"}]
        summary[symbol] = {
            "flagged": sum(row["candidate"] for row in relevant),
            "refresh_windows": windows,
            "refreshed": len(refreshed_windows),
            "refreshed_windows": refreshed_windows,
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
    if not rows or any(not is_plausible_usd_per_unit(
            str(row.get("currency") or ""), row.get("usd_per_unit"),
            row.get("source_symbol")) for row in rows):
        return False
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
        normalized = []
        try:
            for row in raw:
                if not row.get("date") or row.get("close") is None:
                    continue
                source_symbol = f"{currency}USD"
                normalized.append({
                    "currency": currency, "date": row["date"],
                    "usd_per_unit": validate_usd_per_unit(
                        currency, row["close"], source_symbol),
                    "source_symbol": source_symbol,
                    "source": "fmp",
                })
        except ValueError:
            normalized = []
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
        raw_warnings = first.get("snapshot_warnings_json", [])
        if isinstance(raw_warnings, str):
            try:
                raw_warnings = json.loads(raw_warnings)
            except ValueError as exc:
                raise ValueError("invalid snapshot_warnings_json") from exc
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
            "snapshot_warnings": list(raw_warnings),
            "source_kind": source_kind,
        }
        result.append((composition, sorted(rows, key=lambda row: row["raw_row_index"])))
    # For the same rebalance/effective date, a later official disclosure
    # supersedes the temporary live-tail proxy regardless of holding_date.
    priority = {"live": 0, "disclosure": 1}
    return sorted(result, key=lambda item: (
        item[0]["composition_effective_date"],
        priority[item[0]["source_kind"]], item[0]["holding_date"]))


def _preview_metric(
    outputs: Sequence[Mapping[str, Any]], field: str,
) -> Dict[str, Any]:
    rows = [row for row in outputs if row.get(field) is not None]
    if not rows:
        return {
            "current": None, "percentile": None, "minimum": None,
            "minimum_date": None, "median": None, "maximum": None,
            "maximum_date": None, "observations": 0,
        }
    current = outputs[-1].get(field)
    values = [float(row[field]) for row in rows]
    minimum = min(rows, key=lambda row: (float(row[field]), row["valuation_date"]))
    maximum = max(rows, key=lambda row: (float(row[field]), row["valuation_date"]))
    percentile = (sum(value <= float(current) for value in values) / len(values) * 100
                  if current is not None else None)
    return {
        "current": current,
        "percentile": percentile,
        "minimum": minimum[field],
        "minimum_date": minimum["valuation_date"],
        "median": statistics.median(values),
        "maximum": maximum[field],
        "maximum_date": maximum["valuation_date"],
        "observations": len(values),
    }


def _valuation_preview(outputs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not outputs:
        return {}
    current = outputs[-1]
    coverages = [float(row["weight_coverage"]) for row in outputs]
    anchors = [{
        "holding_date": row["holding_date"],
        "valuation_date": row["valuation_date"],
        "primary_pe": row["rebalance_weighted_ttm_pe_gaap_proxy"],
        "secondary_pe": row["uncapped_mcap_basket_pe_gaap"],
        "weight_coverage": row["weight_coverage"],
        "data_quality_tier": row["data_quality_tier"],
    } for row in outputs if row["is_observed_weight_date"] == 1]
    missing = [{
        "raw_symbol": member.get("raw_symbol"),
        "resolved_symbol": member.get("resolved_symbol"),
        "weight_pct": member.get("weight_pct"),
        "reason": member.get("exclusion_reason"),
    } for member in current["members_json"] if member.get("exclusion_reason")]
    return {
        "date_range": [outputs[0]["valuation_date"], current["valuation_date"]],
        "current": {
            "valuation_date": current["valuation_date"],
            "holding_date": current["holding_date"],
            "composition_effective_date": current["composition_effective_date"],
            "composition_available_date": current["composition_available_date"],
            "data_quality_tier": current["data_quality_tier"],
            "weight_coverage": current["weight_coverage"],
            "primary_pe": current["rebalance_weighted_ttm_pe_gaap_proxy"],
            "secondary_pe": current["uncapped_mcap_basket_pe_gaap"],
            "missing_members": missing,
        },
        "primary": _preview_metric(
            outputs, "rebalance_weighted_ttm_pe_gaap_proxy"),
        "secondary": _preview_metric(outputs, "uncapped_mcap_basket_pe_gaap"),
        "weight_coverage": {
            "minimum": min(coverages),
            "median": statistics.median(coverages),
            "maximum": max(coverages),
        },
        "observed_weight_anchors": anchors,
    }


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
    for _, holding_rows in groups:
        validate_snapshot_quality(holding_rows)
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
    report["valuation_preview"] = _valuation_preview(outputs)
    if store is not None and not report["coverage_7d"]["target_passed"]:
        raise RuntimeError(
            "compute publishable coverage missed required 95% target; "
            "valuation range was not written")
    if store is not None:
        store.replace_basket_ttm_valuation_range(
            "SOXX", args.from_date, args.to_date, outputs)


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

    member_symbols = _snapshot_member_universe(state.snapshots)
    symbols = _snapshot_universe(state.snapshots)
    if not symbols:
        raise ValueError("historical basket universe is empty")
    if conn is not None:
        _hydrate_symbols(state, conn, symbols)
    report["member_universe_count"] = len(member_symbols)
    report["evaluation_universe_count"] = len(symbols)
    report["alias_support_symbols"] = sorted({
        str(row["alias_symbol"]).upper() for row in state.snapshots
        if row.get("alias_symbol")
    })
    # Backward-compatible report field; this is the full evaluation universe.
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
        return (0 if report.get("coverage_7d", {}).get(
            "target_passed", True) else 1)
    except (FileNotFoundError, FMPResponseError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
