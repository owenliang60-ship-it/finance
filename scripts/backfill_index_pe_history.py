#!/usr/bin/env python3
"""Five-year weekly GAAP P/E backfill for SPY, QQQ and SOXX.

A wrapper around the audited SOXX historical backfill: source snapshots,
fundamentals, market cap, splits, market-cap sanity and FX all run through
that script's stage functions, now threaded with each basket's own config
(``config/baskets/index_pe_baskets.json``). What is new here is the product
layer -- weekly sampling and the aggregate caliber of
``terminal.index_pe_weekly`` -- and the append-only run manifest that lets a
read-only verifier check a finished run against the denominator and the repair
provenance that were true while it ran (issue 048).

Each basket is one transaction: a basket that trips a fuse or loses its
composition publishes nothing, and says so in the manifest, without touching
the baskets that succeeded.

    python -m scripts.backfill_index_pe_history \\
        --baskets SPY,QQQ,SOXX --frequency weekly --years 5 --dry-run

``--dry-run`` opens the database read-only and makes no network call unless
``--allow-network`` is also given; it reports the calls a real run would make.
"""
import argparse
import json
import sqlite3
import sys
import uuid
from argparse import Namespace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill_soxx_historical_pe import (  # noqa: E402
    BackfillState,
    _connect_ro,
    _fetch_sources,
    _hydrate_symbols,
    _run_fundamentals,
    _run_fx,
    _run_mcap,
    _run_sanity,
    _run_splits,
    _snapshot_groups,
    _snapshot_member_universe,
    _snapshot_universe,
    basket_snapshot_rules,
    load_state,
    open_write_dependencies,
    validate_snapshot_quality,
)
from src.data.fmp_client import FMPClient, FMPResponseError  # noqa: E402
from src.data.fmp_forward_ingestion import (  # noqa: E402
    basket_history_gap,
    load_index_pe_basket_configs,
)
from src.data.market_store import MarketStore  # noqa: E402
from terminal.index_pe_weekly import (  # noqa: E402
    MINIMUM_MCAP_COVERAGE,
    MINIMUM_WEIGHT_COVERAGE,
    WEEKLY_METHODOLOGY_VERSION,
    compute_weekly_point,
    default_share_class_config,
    expected_week_ends,
    group_trading_dates_by_week,
    select_weekly_rows,
    weekly_calendar_hash,
    weekly_result_hash,
    share_class_secondary_symbols,
)


CONFIG_DIR = PROJECT_ROOT / "config" / "baskets"
FREQUENCIES = ("weekly",)


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc


def _baskets(value: str) -> List[str]:
    configured = load_index_pe_basket_configs(CONFIG_DIR)  # CLI validation only
    names = [item.strip().upper() for item in str(value).split(",")
             if item.strip()]
    if not names:
        raise argparse.ArgumentTypeError("--baskets must name at least one basket")
    unknown = [name for name in names if name not in configured]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unconfigured basket(s): {', '.join(unknown)}; "
            f"known: {', '.join(sorted(configured))}")
    seen: List[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill weekly aggregate GAAP P/E history for SPY/QQQ/SOXX")
    parser.add_argument("--baskets", type=_baskets, default="SPY,QQQ,SOXX")
    parser.add_argument("--frequency", choices=FREQUENCIES, default="weekly")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--as-of", dest="as_of", type=_iso_date,
                        default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-network", action="store_true",
        help="Permit API calls during dry-run; non-dry stages are explicit writes")
    parser.add_argument("--refresh-live", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR,
                        help="basket + share-class config root (SSOT)")
    parser.add_argument("--db", type=Path,
                        default=PROJECT_ROOT / "data" / "market.db")
    args = parser.parse_args(argv)
    if args.years <= 0:
        parser.error("--years must be positive")
    if args.run_id is None:
        args.run_id = f"index-pe-{args.as_of}-{uuid.uuid4().hex[:8]}"
    return args


def resolve_window(args: argparse.Namespace) -> Sequence[str]:
    """Five-year window measured back from the report as-of date (§3.2)."""
    as_of = date.fromisoformat(args.as_of)
    try:
        start = as_of.replace(year=as_of.year - args.years)
    except ValueError:  # 29 February
        start = as_of.replace(year=as_of.year - args.years, day=28)
    return (start.isoformat(), as_of.isoformat())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Manifest:
    """Append-only run manifest for one basket.

    Events are emitted in order and, in write mode, land in the database
    before the work they describe: ``run_started`` freezes the target universe
    and the expected date range so a later verifier cannot be handed a
    narrower range and declare a suffix complete.
    """

    def __init__(self, *, run_id: str, basket: str, frequency: str,
                 expected_from: str, expected_to: str,
                 methodology_version: str, store: Optional[MarketStore]):
        self.run_id = run_id
        self.basket = basket.upper()
        self.frequency = frequency
        self.expected_from = expected_from
        self.expected_to = expected_to
        self.methodology_version = methodology_version
        self.store = store
        self.events: List[Dict[str, Any]] = []
        self._universe: List[str] = []

    def build_event(self, event_kind: str, payload: Mapping[str, Any],
                    universe: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        if universe is not None:
            self._universe = [str(value) for value in universe]
        event = {
            "run_id": self.run_id,
            "basket": self.basket,
            "event_seq": len(self.events),
            "event_kind": event_kind,
            "frequency": self.frequency,
            "expected_from_date": self.expected_from,
            "expected_to_date": self.expected_to,
            "methodology_version": self.methodology_version,
            "target_count": len(self._universe),
            "target_universe_json": list(self._universe),
            "payload_json": dict(payload),
        }
        return event

    def append(self, event_kind: str, payload: Mapping[str, Any],
               universe: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        event = self.build_event(event_kind, payload, universe)
        if self.store is not None:
            self.store.append_basket_pe_run_events([event])
        self.events.append(event)
        return event


def latest_consensus_snapshot(
    conn: Optional[sqlite3.Connection], as_of: str,
) -> Optional[str]:
    """The one weekly consensus vintage the whole run completes tails with.

    Picking it once per run, rather than per member, is the hindsight engine's
    contract: a tail assembled from per-symbol vintages would silently mix
    consensus ages inside a single basket point.
    """
    if conn is None:
        return None
    row = conn.execute(
        "SELECT MAX(snapshot_date) AS snapshot FROM fmp_estimates "
        "WHERE snapshot_kind = 'weekly' AND snapshot_date <= ?", [as_of]
    ).fetchone()
    return str(row["snapshot"]) if row and row["snapshot"] else None


def _hydrate_estimates(
    state: BackfillState,
    conn: Optional[sqlite3.Connection],
    symbols: Sequence[str],
    snapshot_date: Optional[str],
) -> None:
    if conn is None or not snapshot_date:
        return
    for symbol in symbols:
        state.estimates_by_symbol[symbol] = [dict(row) for row in conn.execute(
            "SELECT * FROM fmp_estimates WHERE symbol = ? "
            "AND snapshot_date = ? AND snapshot_kind = 'weekly' "
            "AND period_type = 'Q' ORDER BY fiscal_date",
            [symbol, snapshot_date]).fetchall()]


def _stage_args(args: argparse.Namespace, window: Sequence[str]) -> Namespace:
    """Adapt the multi-basket CLI to the audited stage functions' namespace."""
    return Namespace(
        from_date=window[0], to_date=window[1], dry_run=args.dry_run,
        allow_network=args.allow_network, refresh_live=args.refresh_live,
        db=args.db)


def backfill_basket(
    args: argparse.Namespace,
    basket: str,
    client: Optional[FMPClient] = None,
    store: Optional[MarketStore] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Run every stage for one basket and return its report.

    Raises on any fail-closed condition; the caller decides whether one bad
    basket should stop the others. Nothing is written until the whole weekly
    batch is ready, so a mid-run failure cannot leave a partial series behind.
    """
    basket = basket.upper()
    config_dir = Path(getattr(args, "config_dir", None) or CONFIG_DIR)
    configs = load_index_pe_basket_configs(config_dir)
    if basket not in configs:
        raise ValueError(f"{basket} is not configured in {config_dir}")
    basket_config = configs[basket]
    window = resolve_window(args)
    stage_args = _stage_args(args, window)
    share_config = default_share_class_config(config_dir)

    report: Dict[str, Any] = {
        "basket": basket,
        "status": "running",
        "frequency": args.frequency,
        "from_date": window[0],
        "to_date": window[1],
        "dry_run": bool(args.dry_run),
        "network_enabled": client is not None,
        "stages": {},
        "planned_network_calls": [],
        "manifest": [],
        "rows": [],
        "weekly_rows": 0,
    }
    manifest = _Manifest(
        run_id=args.run_id, basket=basket, frequency=args.frequency,
        expected_from=window[0], expected_to=window[1],
        methodology_version=WEEKLY_METHODOLOGY_VERSION, store=store)
    rows_written = False

    try:
        state = load_state(conn, window[1], basket) if conn is not None \
            else BackfillState()
        if not state.trading_dates:
            raise ValueError(f"{basket} trading calendar is empty")

        if client is None:
            report["planned_network_calls"].append(
                {"stage": "source", "basket": basket,
                 "endpoint": "fund disclosures/live holdings"})
            report["stages"]["source"] = {"planned": True}
        else:
            # One config root for this run: the same share_class_groups.json
            # drives the upstream weight merge and the product market-cap merge.
            _fetch_sources(stage_args, state, client, store, report,
                           basket_symbol=basket, basket_config=basket_config,
                           config_dir=config_dir)

        member_symbols = _snapshot_member_universe(state.snapshots)
        income_symbols = _snapshot_universe(state.snapshots)
        if not income_symbols:
            raise ValueError(f"{basket} historical basket universe is empty")
        secondary_symbols = share_class_secondary_symbols(
            state.snapshots, share_config["groups"])
        # Secondary listings are valued but never asked for fundamentals: the
        # company's net income is filed under the primary class.
        mcap_symbols = sorted(set(income_symbols) | set(secondary_symbols))
        if conn is not None:
            _hydrate_symbols(state, conn, mcap_symbols)
        consensus_snapshot = latest_consensus_snapshot(conn, window[1])
        _hydrate_estimates(state, conn, income_symbols, consensus_snapshot)
        report["consensus_snapshot_date"] = consensus_snapshot
        report["member_universe_count"] = len(member_symbols)
        report["evaluation_universe_count"] = len(income_symbols)
        report["share_class_secondary_symbols"] = secondary_symbols

        # A composition is usable from the day it was disclosed, so the first
        # such date is the floor of what this run can be held accountable for.
        available_dates = sorted(
            str(row["composition_available_date"]) for row in state.snapshots
            if row.get("composition_available_date"))
        composition_floor = available_dates[0] if available_dates else None
        window_calendar = [day for day in state.trading_dates
                           if window[0] <= day <= window[1]]
        expected_weeks = expected_week_ends(
            state.trading_dates, window[0], window[1], composition_floor)
        report["expected_weeks"] = expected_weeks

        # The manifest lands before the first per-symbol remote call, with the
        # universe that run is actually about to fetch and the denominator it
        # will be judged against. Freezing the week set and the calendar hash
        # here is what stops a later verification from quietly recomputing a
        # smaller expectation out of tables that changed since.
        manifest.append("run_started", {
            "as_of": args.as_of,
            "years": args.years,
            "member_universe_count": len(member_symbols),
            "share_class_secondary_symbols": secondary_symbols,
            "minimum_mcap_coverage": MINIMUM_MCAP_COVERAGE,
            "minimum_weight_coverage": MINIMUM_WEIGHT_COVERAGE,
            "consensus_snapshot_date": consensus_snapshot,
            "composition_floor": composition_floor,
            "expected_weeks": expected_weeks,
            "trading_days": len(window_calendar),
            "calendar_hash": weekly_calendar_hash(window_calendar),
            "started_at": _now(),
        }, universe=mcap_symbols)

        # The fuse guards against the vendor failing us, not against a member
        # that simply had not listed yet at the start of a five-year window.
        _run_fundamentals(stage_args, state, income_symbols, client, store,
                          report, fuse_on_incompleteness=False)
        _run_mcap(stage_args, state, mcap_symbols, client, store, report,
                  fuse_on_incompleteness=False)
        _run_splits(state, mcap_symbols, client, store, report)
        # issue035: sanity always rescans, even when the market-cap range is
        # already "complete" -- completeness is not cleanliness.
        _run_sanity(stage_args, state, mcap_symbols, client, store, report)
        _run_fx(stage_args, state, income_symbols, client, store, report)

        for provenance in report.get("forced_refresh_provenance", []):
            manifest.append("forced_refresh", provenance)

        rows = _compute_weekly_rows(
            args, basket, basket_config, state, window, share_config, report,
            consensus_snapshot_date=consensus_snapshot)
        report["rows"] = rows
        report["weekly_rows"] = len(rows)

        completion_payload = {
            "weekly_rows": len(rows),
            "published_ttm": sum(row["ttm_pe_gaap"] is not None for row in rows),
            "published_hindsight": sum(
                row["hindsight_ntm_pe_gaap"] is not None for row in rows),
            "written": bool(store is not None and rows),
            # What this run says it produced. Recomputable from the stored
            # rows, so a deleted week or an edited P/E stops matching.
            "result_hash": weekly_result_hash(rows),
            "completed_at": _now(),
        }
        if store is not None:
            from scripts.verify_index_pe_history import verify_database

            def certify(candidate_conn):
                verification = verify_database(
                    candidate_conn, [basket], args.as_of, args.years,
                    sample=50, config_dir=config_dir)
                report["verification"] = verification
                return verification["passed"]

            completed = manifest.build_event("run_completed", completion_payload)
            store.commit_basket_weekly_pe_window([
                {key: value for key, value in row.items()
                 if not key.startswith("_")}
                for row in ({**row, "run_id": args.run_id} for row in rows)],
                completed, certify)
            rows_written = True
            manifest.events.append(completed)
        else:
            manifest.append("run_completed", completion_payload)
        report["status"] = "complete"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        if not manifest.events:
            # Preflight can fail before a universe/calendar can be frozen.
            # Record that fact as a failed run, not a terminal-only event
            # which would invalidate every later certification for the basket.
            manifest.append("run_started", {
                "preflight_failed": True, "expected_weeks": [],
                "started_at": _now(), "as_of": args.as_of,
            }, universe=[])
        # Whether the batch had already committed decides whether this run
        # left rows behind that no completed manifest accounts for.
        manifest.append("run_failed", {"error": report["error"],
                                       "rows_written": rows_written,
                                       "failed_at": _now()})
        report["manifest"] = manifest.events
        # Carry the partial report out with the exception. Which stages ran,
        # what the manifest recorded and which window was in flight are what an
        # operator debugs from; the caller would otherwise have only the message.
        setattr(exc, "backfill_report", report)
        raise
    report["manifest"] = manifest.events
    return report


def _compute_weekly_rows(
    args: argparse.Namespace,
    basket: str,
    basket_config: Mapping[str, Any],
    state: BackfillState,
    window: Sequence[str],
    share_config: Mapping[str, Any],
    report: Dict[str, Any],
    consensus_snapshot_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Weekly points over the window, from one daily evidence pass."""
    rules = basket_snapshot_rules(basket_config)
    groups = _snapshot_groups(state.snapshots, state.trading_dates)
    for _, holding_rows in groups:
        validate_snapshot_quality(holding_rows, **rules["snapshot_quality"])
    staleness = int(basket_config.get("market_cap_staleness_days", 7))
    cache: Dict[str, Optional[Dict[str, Any]]] = {}

    def compute(valuation_date: str) -> Optional[Dict[str, Any]]:
        if valuation_date in cache:
            return cache[valuation_date]
        # A composition is usable only once it is both in force and public.
        # Effective-date-only selection reaches back to a rebalance whose
        # membership and weights nobody could know yet: FMP disclosed the
        # 2025-12-22 SPY composition on 2026-02-15, and using it to value
        # 2025-12-26 imports two months of hindsight into a point-in-time
        # line. Until the disclosure lands, the previous public composition
        # is the only thing that was knowable.
        eligible = [item for item in groups
                    if item[0]["composition_effective_date"] <= valuation_date
                    and item[0]["composition_available_date"] <= valuation_date]
        row: Optional[Dict[str, Any]] = None
        if eligible:
            # Newest by effective date, stated rather than inherited from the
            # order `_snapshot_groups` happened to return. A disclosure
            # supersedes the live-tail proxy for the same effective date.
            composition, holding_rows = max(
                eligible,
                key=lambda item: (item[0]["composition_effective_date"],
                                  0 if item[0]["source_kind"] == "live" else 1,
                                  item[0]["holding_date"]))
            row = compute_weekly_point(
                basket_symbol=basket,
                valuation_date=valuation_date,
                holding_rows=holding_rows,
                composition=composition,
                trading_dates=state.trading_dates,
                income_by_symbol=state.income_by_symbol,
                market_cap_by_symbol=state.market_cap_by_symbol,
                estimates_by_symbol=state.estimates_by_symbol,
                fx_by_currency=state.fx_by_currency,
                sanity_by_symbol=state.sanity_by_symbol,
                share_class_groups=share_config["groups"],
                share_class_conventions=share_config["conventions"],
                consensus_snapshot_date=consensus_snapshot_date,
                max_staleness_days=staleness,
            )
        cache[valuation_date] = row
        return row

    weeks = group_trading_dates_by_week(
        state.trading_dates, window[0], window[1])
    rows = select_weekly_rows(weeks, compute)
    gap_weeks = [week[-1] for week in weeks
                 if basket_history_gap(basket, week[-1],
                                       {basket: dict(basket_config)})]
    report["coverage_weekly"] = {
        "weeks": len(weeks),
        "rows": len(rows),
        "expected_gap_weeks": len(gap_weeks),
        "published_ttm": sum(row["ttm_pe_gaap"] is not None for row in rows),
        "published_hindsight": sum(
            row["hindsight_ntm_pe_gaap"] is not None for row in rows),
        "history_available_from": basket_config.get("history_available_from"),
    }
    return rows


def run_backfill(
    args: argparse.Namespace,
    client: Optional[FMPClient] = None,
    store: Optional[MarketStore] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Run every requested basket. One basket's failure never blocks another."""
    window = resolve_window(args)
    report: Dict[str, Any] = {
        "run_id": args.run_id,
        "frequency": args.frequency,
        "as_of": args.as_of,
        "from_date": window[0],
        "to_date": window[1],
        "dry_run": bool(args.dry_run),
        "network_enabled": client is not None,
        "baskets": {},
    }
    for basket in args.baskets:
        try:
            result = backfill_basket(args, basket, client=client, store=store,
                                     conn=conn)
        except Exception as exc:  # per-basket isolation is the point
            partial = getattr(exc, "backfill_report", None)
            result = dict(partial) if isinstance(partial, dict) else {}
            result.update({
                "basket": basket, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "weekly_rows": result.get("weekly_rows", 0)})
        report["baskets"][basket] = {
            key: value for key, value in result.items() if key != "rows"}
    report["failed_baskets"] = sorted(
        name for name, item in report["baskets"].items()
        if item.get("status") != "complete")
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
            client = FMPClient() if args.allow_network else None
        else:
            backup_path, store = open_write_dependencies(args.db)
            conn = store._get_conn()
            client = FMPClient()
        report = run_backfill(args, client=client, store=store, conn=conn)
        report["backup_path"] = str(backup_path) if backup_path else None
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True,
                         default=str))
        return 0 if not report["failed_baskets"] else 1
    except (FileNotFoundError, FMPResponseError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()
        elif conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
