#!/usr/bin/env python3
"""Read-only verification of the three-index weekly P/E history.

Positioned per issue 048: this is an evidence and tamper checker, not a second
methodology. It shares the producer's aggregation kernel deliberately -- a
verifier that re-types the formula only proves two transcriptions agree -- and
buys its independence three other ways:

* the **denominator** comes from the append-only run manifest, never from the
  current universe, so a shrunken basket or a later ``--as-of`` cannot make an
  incomplete series look complete;
* a **heterogeneous spot check** recomputes sampled points with plain
  arithmetic and reconciles both sides of the ratio against the raw
  ``historical_market_cap`` and ``income_quarterly`` rows by direct SQL;
* **repair provenance** (pre/post row hashes of every forced-refresh window)
  is checked against the manifest, because the refreshed source tables can no
  longer show what was replaced.

    python -m scripts.verify_index_pe_history \\
        --baskets SPY,QQQ,SOXX --years 5 --mode ro

The database is opened ``mode=ro`` with ``query_only=ON`` and read inside one
transaction, so every check sees the same snapshot even if a writer is active.
"""
import argparse
import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.fmp_forward_ingestion import (  # noqa: E402
    load_index_pe_basket_configs,
    load_soxx_symbol_aliases,
)
from src.data.fx_validation import USD_PER_UNIT_BOUNDS  # noqa: E402
from src.data.fund_issuer_identity import (  # noqa: E402
    load_issuer_overrides, valid_issuer_lei,
)
from terminal.basket_pe_aggregate import (  # noqa: E402
    MINIMUM_MCAP_COVERAGE,
    compute_aggregate_basket_pe,
)
from terminal.historical_market_cap_sanity import (  # noqa: E402
    accepted_market_cap_status,
    scan_market_cap_candidates,
)
from terminal.index_pe_weekly import (  # noqa: E402
    MINIMUM_WEIGHT_COVERAGE,
    WEEKLY_METHODOLOGY_VERSION,
    default_share_class_config,
    expected_week_ends,
    weekly_calendar_hash,
    weekly_result_hash,
)


CONFIG_DIR = PROJECT_ROOT / "config" / "baskets"
REQUIRED_TABLES = (
    "basket_weekly_pe_history", "basket_pe_backfill_runs", "daily_price",
    "income_quarterly", "historical_market_cap",
    "fmp_fund_disclosure_holdings",
)
# Columns that would mean the holding-weighted proxy leaked into the product
# table (R1). The weekly panel compares three aggregate lines; a weighted
# average of member P/Es is a different question.
FORBIDDEN_COLUMNS = (
    "rebalance_weighted_ttm_pe_gaap_proxy", "weighted_earnings_yield",
    "uncapped_mcap_basket_pe_gaap",
)
DEFAULT_SAMPLE = 8
# Fallback only; every basket declares market_cap_staleness_days and the
# producer gates on that per-basket value, so the verifier reads the same
# config rather than blessing data the producer would have rejected.
DEFAULT_MARKET_CAP_STALENESS_DAYS = 7


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only verification of weekly index PE history")
    parser.add_argument("--baskets", default="SPY,QQQ,SOXX")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--as-of", dest="as_of", type=_iso_date,
                        default=date.today().isoformat())
    parser.add_argument("--mode", choices=("ro",), default="ro",
                        help="read-only is the only supported mode")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR,
                        help="basket + share-class config root (SSOT)")
    parser.add_argument("--db", type=Path,
                        default=PROJECT_ROOT / "data" / "market.db")
    args = parser.parse_args(argv)
    args.baskets = [item.strip().upper() for item in args.baskets.split(",")
                    if item.strip()]
    if not args.baskets:
        parser.error("--baskets must name at least one basket")
    if args.years <= 0:
        parser.error("--years must be positive")
    return args


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rows(conn: sqlite3.Connection, query: str,
          params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", [table]
    ).fetchone() is not None


def _check(name: str, passed: bool, detail: Any) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _connection_is_read_only(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Prove the connection cannot write, rather than claim it.

    A verifier that reports "mode=ro" from a string is checking its own
    intentions. This attempts the smallest possible write and requires it to be
    refused.
    """
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _verifier_write_probe (x INTEGER)")
    except sqlite3.Error as exc:
        return {"refused": True, "error": str(exc)}
    return {"refused": False, "error": None}


def _close(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(float(left), float(right), rel_tol=tolerance,
                        abs_tol=tolerance)


def _window(as_of: str, years: int) -> Tuple[str, str]:
    target = date.fromisoformat(as_of)
    try:
        start = target.replace(year=target.year - years)
    except ValueError:
        start = target.replace(year=target.year - years, day=28)
    return start.isoformat(), target.isoformat()


def _sample_indexes(count: int, sample: int) -> List[int]:
    """Evenly spaced sample positions -- deterministic, no RNG seed to record."""
    if count <= 0 or sample <= 0:
        return []
    if sample >= count:
        return list(range(count))
    step = (count - 1) / (sample - 1) if sample > 1 else 0
    return sorted({int(round(index * step)) for index in range(sample)})


# ---------------------------------------------------------------------------
# manifest (denominator SSOT)
# ---------------------------------------------------------------------------

# A run accounts for itself by reaching one of these. Anything else means the
# process died between committing the weekly batch and appending its outcome.
TERMINAL_EVENT_KINDS = frozenset({"run_completed", "run_failed"})


def _invalid_runs(
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, List[str]]:
    """Runs whose event sequence does not describe one run.

    Append-only stops a rewrite, not an append, and every attack since has
    walked through that door: appending a `run_completed` to a failed run,
    appending a *second* `run_completed` with the hash recomputed over
    tampered rows, appending one with a wider window to adopt a stray row.
    None of them rewrote anything, and each was accepted because the
    declaration was read last-wins with no constraint on how many times a run
    could declare itself finished.

    A run therefore says exactly one terminal thing, once, at the end:

    * exactly one `run_completed` **or** `run_failed`, never both and never
      two of either;
    * nothing after it -- a run that kept talking after it finished did not
      finish;
    * one `run_started`, not several.

    They must also finish *in order*. A run killed between committing its
    batch and appending its outcome is a normal operational event, and
    "record that it finished" is cleanup an operator would reach for through
    the sanctioned API -- producing an event set that is perfectly well formed
    (one start, one terminal, terminal last) and that cardinality therefore
    cannot see. What gives it away is that a sequential producer cannot close
    run-0 after run-1 has already closed: terminal events must appear in the
    order the runs began.

    A run failing any of these certifies nothing and lends its window to
    nothing. Note this is a statement about the *manifest*, not about the
    passage of time: a legitimate later run supersedes an earlier failure
    without either of them being invalid, and a run that failed early still
    closed before the runs that started after it.
    """
    # `events` arrives in manifest row order, which is the only ordering this
    # function trusts.
    by_run: Dict[str, List[Mapping[str, Any]]] = {}
    for row in events:
        by_run.setdefault(str(row["run_id"]), []).append(row)

    invalid: Dict[str, List[str]] = {}
    for run_id, rows in by_run.items():
        reasons: List[str] = []
        # Physical order only. `event_seq` is a number the writer chooses, so
        # sorting by it lets an appended event claim any position it likes --
        # a terminal written first and a start written after it read as a
        # well-formed run under an event_seq sort.
        kinds = [str(row["event_kind"]) for row in rows]
        terminals = [index for index, kind in enumerate(kinds)
                     if kind in TERMINAL_EVENT_KINDS]
        starts = [index for index, kind in enumerate(kinds)
                  if kind == "run_started"]
        if "run_failed" in kinds and "run_completed" in kinds:
            reasons.append("run_both_failed_and_completed")
        elif len(terminals) > 1:
            reasons.append(
                f"run_declared_multiple_terminal_events:{len(terminals)}")
        if not starts:
            reasons.append("run_never_started")
        elif len(starts) > 1:
            reasons.append(f"run_started_more_than_once:{len(starts)}")
        elif starts[0] != 0:
            reasons.append(
                f"run_started_is_not_the_first_event:{kinds[0]}")
        if terminals and terminals[0] != len(rows) - 1:
            reasons.append(
                f"run_terminal_is_not_the_last_event:{kinds[terminals[0] + 1]}")
        if reasons:
            invalid[run_id] = reasons

    # Terminal events must appear in the order the runs began. Ordering is by
    # the manifest's own row order, not event_seq, which is a label the writer
    # chooses, and not a timestamp, which a clock can muddle.
    def _rowid(row: Mapping[str, Any], fallback: int) -> int:
        value = row.get("manifest_rowid")
        return int(value) if value is not None else fallback

    terminal_at: Dict[str, int] = {}
    for index, row in enumerate(events):
        run_id = str(row["run_id"])
        if str(row["event_kind"]) in TERMINAL_EVENT_KINDS \
                and run_id not in terminal_at:
            terminal_at[run_id] = _rowid(row, index)
    started = [run_id for run_id in _run_order(events)
               if run_id in terminal_at]
    for position, run_id in enumerate(started):
        later_closed_first = [
            other for other in started[position + 1:]
            if terminal_at[other] < terminal_at[run_id]]
        if later_closed_first:
            invalid.setdefault(run_id, []).append(
                "run_terminal_recorded_after_a_later_run:"
                f"{later_closed_first[0]}")
    return invalid


def _valid_completed_runs(events: Sequence[Mapping[str, Any]]) -> List[str]:
    """Completed runs whose event sequence describes exactly one run."""
    invalid = set(_invalid_runs(events))
    completed = {str(row["run_id"]) for row in events
                 if row["event_kind"] == "run_completed"}
    return [run_id for run_id in _run_order(events)
            if run_id in completed and run_id not in invalid]


def _runs_after(events: Sequence[Mapping[str, Any]],
                run_id: Optional[str]) -> List[str]:
    """Runs the manifest recorded after the certifying one.

    A successful run rewrites every expected week, so anything before it has
    been superseded; anything after it wrote to a database this verification
    is about to certify.
    """
    order = _run_order(events)
    if run_id is None or run_id not in order:
        return order
    return order[order.index(run_id) + 1:]


def _unfinished_runs(events: Sequence[Mapping[str, Any]]) -> List[str]:
    """Runs that never recorded an outcome, including legacy partial writes.

    C1 commits the candidate product and run_completed in one transaction;
    run_started still lands before the work. Keep this check for crashes before
    completion and for legacy runs which wrote rows before their terminal event.
    A later successful full-window retry supersedes an earlier unfinished run.
    """
    kinds_by_run: Dict[str, set] = {}
    for row in events:
        kinds_by_run.setdefault(str(row["run_id"]), set()).add(
            str(row["event_kind"]))
    return sorted(run_id for run_id, kinds in kinds_by_run.items()
                  if not (kinds & TERMINAL_EVENT_KINDS))


def _run_order(events: Sequence[Mapping[str, Any]]) -> List[str]:
    """Run ids in the order the manifest recorded them.

    Insertion order, not timestamps: the manifest is append-only, so its own
    row order is the one thing a clock cannot muddle.
    """
    order: List[str] = []
    for row in events:
        run_id = str(row["run_id"])
        if run_id not in order:
            order.append(run_id)
    return order


def _latest_run(events: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """The newest run that completed without contradicting itself."""
    ordered = _valid_completed_runs(events)
    return ordered[-1] if ordered else None


def _payload(row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(row["payload_json"])
    except (TypeError, ValueError, KeyError):
        return {}
    return value if isinstance(value, dict) else {}


def _manifest_errors(
    basket: str, events: Sequence[Mapping[str, Any]], run_id: Optional[str],
    requested: Tuple[str, str], observed_calendar_hash: Optional[str] = None,
    observed_calendar: Optional[Sequence[str]] = None,
    earliest_disclosure: Optional[str] = None,
    history_available_from: Optional[str] = None,
) -> List[str]:
    errors: List[str] = []
    for invalid_run, reasons in sorted(_invalid_runs(events).items()):
        for reason in reasons:
            errors.append(f"{basket}:{invalid_run}:{reason}")
    later = set(_runs_after(events, run_id))
    for unfinished in _unfinished_runs(events):
        if unfinished in later or run_id is None:
            errors.append(
                f"{basket}:{unfinished}:unfinished_run_with_no_terminal_event")
    # A run that committed its batch and then died is not accounted for by any
    # completed manifest. Falling back to the last good one would certify data
    # that run never certified.
    for row in events:
        if row["event_kind"] != "run_failed":
            continue
        if str(row["run_id"]) not in later and run_id is not None:
            continue
        if _payload(row).get("rows_written"):
            errors.append(
                f"{basket}:{row['run_id']}:wrote_rows_then_failed")
    if run_id is None:
        errors.append(f"{basket}:no_completed_run_manifest")
        return errors
    run_events = [row for row in events if row["run_id"] == run_id]
    sequences = [int(row["event_seq"]) for row in run_events]
    if sequences != list(range(len(sequences))):
        errors.append(f"{basket}:manifest_event_seq_not_append_only:{sequences}")
    kinds = [row["event_kind"] for row in run_events]
    if not kinds or kinds[0] != "run_started":
        errors.append(f"{basket}:manifest_missing_run_started")
    if "run_failed" in kinds:
        errors.append(f"{basket}:manifest_records_a_failed_run")
    started = next((row for row in run_events
                    if row["event_kind"] == "run_started"), None)
    if started is None:
        return errors
    if (started["expected_from_date"] > requested[0]
            or started["expected_to_date"] < requested[1]):
        # The point of an expected range in the manifest: a caller asking for a
        # narrower window must not be able to certify a suffix as complete.
        errors.append(
            f"{basket}:manifest_range_narrower_than_requested:"
            f"{started['expected_from_date']}..{started['expected_to_date']}"
            f" vs {requested[0]}..{requested[1]}")
    if int(started["target_count"]) <= 0:
        errors.append(f"{basket}:manifest_empty_target_universe")
    try:
        universe = json.loads(started["target_universe_json"])
    except (TypeError, ValueError):
        errors.append(f"{basket}:manifest_universe_unparseable")
        universe = []
    if len(universe) != int(started["target_count"]):
        errors.append(f"{basket}:manifest_target_count_mismatch")
    if started["methodology_version"] != WEEKLY_METHODOLOGY_VERSION:
        errors.append(
            f"{basket}:manifest_methodology_version:"
            f"{started['methodology_version']}")
    payload = _payload(started)
    if not payload.get("expected_weeks"):
        # Without a frozen week set the denominator would have to be rebuilt
        # from tables that can change, which is the hole this closes.
        errors.append(f"{basket}:manifest_has_no_frozen_expected_weeks")
    frozen_calendar = payload.get("calendar_hash")
    frozen_weeks = list(payload.get("expected_weeks") or [])
    frozen_floor = payload.get("composition_floor")
    if not frozen_calendar:
        errors.append(f"{basket}:manifest_has_no_calendar_hash")
    elif (observed_calendar_hash is not None
            and frozen_calendar != observed_calendar_hash):
        errors.append(
            f"{basket}:calendar_changed_since_the_run:"
            f"{frozen_calendar[:12]}!={observed_calendar_hash[:12]}")
    elif observed_calendar is not None and frozen_weeks:
        # The hash proves this calendar is the one the run measured itself
        # against, so the frozen week set must be reproducible from it.
        # Without this, a week nobody traded can be inserted into the frozen
        # set (or a real one removed) while the hash still matches, because
        # the calendar itself was never touched.
        reproduced = expected_week_ends(
            observed_calendar, started["expected_from_date"],
            started["expected_to_date"], frozen_floor)
        if sorted(frozen_weeks) != sorted(reproduced):
            missing = sorted(set(reproduced) - set(frozen_weeks))
            extra = sorted(set(frozen_weeks) - set(reproduced))
            errors.append(
                f"{basket}:frozen_weeks_not_reproducible:"
                f"missing={missing[:3]}:extra={extra[:3]}")
    if not frozen_floor:
        errors.append(f"{basket}:manifest_has_no_composition_floor")
    else:
        # A floor moved forward silently shrinks what the run is accountable
        # for, so it may never postdate the earliest disclosure on record.
        if earliest_disclosure is not None \
                and str(frozen_floor) > str(earliest_disclosure):
            errors.append(
                f"{basket}:composition_floor_later_than_disclosures:"
                f"{frozen_floor}>{earliest_disclosure}")
        # `history_available_from` (config) and the availability floor are
        # different statements: the first is the lower bound of disclosable
        # history, the second is where availability actually begins. The
        # frozen set may not reach below either of them.
        bound = max([value for value in (history_available_from, frozen_floor)
                     if value] or [None])
        if bound and frozen_weeks and min(frozen_weeks) < bound:
            errors.append(
                f"{basket}:frozen_week_before_history_bound:"
                f"{min(frozen_weeks)}<{bound}")
    return errors


def _result_integrity_errors(
    basket: str, conn: sqlite3.Connection,
    events: Sequence[Mapping[str, Any]], run_id: Optional[str],
    window: Tuple[str, str],
) -> List[str]:
    """Certification is per run, over the rows that run actually wrote.

    Freezing a result hash stopped an edit from going unnoticed, but not a
    forgery: append a fresh run_started + run_completed whose hash is
    recomputed over the tampered rows and the forged run became the certifying
    one. Every hash matched, because the attacker chose them.

    Rows now name the run that wrote them, which turns certification into a
    claim about specific rows rather than about a date range. A run's declared
    hash is checked against exactly its own rows; rows inside the certifying
    window must belong to the certifying run; and a row naming a run that
    never completed is certified by nobody.

    **What this does not do.** Bypassing it takes one statement --
    ``UPDATE basket_weekly_pe_history SET run_id = '<forged run>'`` -- and
    every check here passes again. Ownership defends the *append* path, which
    is the one the store's own CRUD offers and the one every attack so far has
    used; it is not a defence against an arbitrary writer holding the same
    database file. Anything stored beside the data can be edited with the
    data. Read these checks as tamper *evidence*, not as tamper *proof*
    (issue 048), and put real weight on who can open market.db for writing.
    """
    if run_id is None:
        return [f"{basket}:no_completed_run_to_check_results_against"]
    errors: List[str] = []
    # One terminal event per valid run, so there is nothing to choose between
    # -- which is the point: reading the declaration last-wins is what let a
    # second, appended run_completed speak for the first.
    valid = set(_valid_completed_runs(events))
    declared: Dict[str, Dict[str, Any]] = {}
    for row in events:
        if row["event_kind"] == "run_completed" and str(row["run_id"]) in valid:
            declared[str(row["run_id"])] = _payload(row)

    rows = _rows(conn, "SELECT * FROM basket_weekly_pe_history "
                       "WHERE basket = ? ORDER BY valuation_date", [basket])
    owned: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        owner = str(row["run_id"] or "").strip()
        if not owner:
            errors.append(
                f"{basket}:{row['valuation_date']}:row_names_no_run")
            continue
        owned.setdefault(owner, []).append(row)

    for owner in sorted(owned):
        if owner not in declared:
            errors.append(
                f"{basket}:{owner}:rows_claim_an_uncertified_run:"
                f"{len(owned[owner])}_rows")
            continue
        expected = declared[owner].get("result_hash")
        if not expected:
            errors.append(f"{basket}:{owner}:run_declared_no_result_hash")
            continue
        observed = weekly_result_hash(owned[owner])
        if expected != observed:
            errors.append(
                f"{basket}:{owner}:run_result_hash_mismatch:"
                f"{str(expected)[:12]}!={observed[:12]}")
        rows_declared = declared[owner].get("weekly_rows")
        if rows_declared is not None and int(rows_declared) != len(owned[owner]):
            errors.append(
                f"{basket}:{owner}:run_row_count_mismatch:"
                f"{rows_declared}!={len(owned[owner])}")

    for row in rows:
        if not window[0] <= str(row["valuation_date"]) <= window[1]:
            continue
        if str(row["run_id"] or "").strip() != run_id:
            errors.append(
                f"{basket}:{row['valuation_date']}:"
                f"not_owned_by_the_certifying_run:{row['run_id']}")
    return errors


def _refresh_provenance_errors(
    basket: str, events: Sequence[Mapping[str, Any]], run_id: Optional[str],
) -> List[str]:
    errors: List[str] = []
    for row in events:
        if row["run_id"] != run_id or row["event_kind"] != "forced_refresh":
            continue
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            errors.append(f"{basket}:{row['event_seq']}:payload_unparseable")
            continue
        prefix = f"{basket}:{payload.get('symbol')}:{payload.get('from_date')}"
        for field in ("symbol", "from_date", "to_date"):
            if not payload.get(field):
                errors.append(f"{prefix}:missing_{field}")
        for field in ("pre_row_hash", "post_row_hash"):
            value = str(payload.get(field) or "")
            if len(value) != 64:
                errors.append(f"{prefix}:{field}_not_a_sha256")
        if str(payload.get("from_date") or "") > str(payload.get("to_date") or ""):
            errors.append(f"{prefix}:window_inverted")
        triggers = payload.get("trigger_dates") or []
        if not triggers:
            errors.append(f"{prefix}:no_trigger_dates")
        for trigger in triggers:
            if not (str(payload.get("from_date")) <= str(trigger)
                    <= str(payload.get("to_date"))):
                errors.append(f"{prefix}:trigger_outside_window:{trigger}")
        if payload.get("skipped") is False and _close(
                payload.get("pre_row_count"), 0) and _close(
                    payload.get("post_row_count"), 0):
            errors.append(f"{prefix}:refresh_claims_no_rows_on_either_side")
    return errors


# ---------------------------------------------------------------------------
# row-level consistency
# ---------------------------------------------------------------------------

def _decode_rows(
    raw_rows: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    decoded = []
    errors = []
    for raw in raw_rows:
        row = dict(raw)
        for field in ("members_json", "warnings_json"):
            try:
                row[field] = json.loads(str(row[field]))
            except (TypeError, ValueError):
                errors.append(f"{row.get('valuation_date')}:{field}")
                row[field] = {} if field == "members_json" else []
        decoded.append(row)
    return decoded, errors


def _gate_warning_present(row: Mapping[str, Any], prefixes: Sequence[str]) -> bool:
    return any(str(warning).startswith(prefix)
               for warning in row["warnings_json"]
               for prefix in prefixes)


def _tier_consistency_errors(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    """quality_tier, the two P/Es and the quarter counts are one statement.

    The store enforces none of this: it accepts an ``actual_only`` row with a
    NULL P/E, and a NULL-P/E row that claims four actual quarters.
    """
    errors = []
    for row in rows:
        prefix = f"{row['basket']}:{row['valuation_date']}"
        tier = row["quality_tier"]
        hindsight_pe = row["hindsight_ntm_pe_gaap"]
        if tier == "unpublishable" and hindsight_pe is not None:
            errors.append(f"{prefix}:unpublishable_with_a_hindsight_pe")
        if tier != "unpublishable":
            if hindsight_pe is None:
                errors.append(f"{prefix}:publishable_tier_without_a_hindsight_pe")
            if (int(row["hindsight_actual_quarters"])
                    + int(row["hindsight_estimate_quarters"])) != 4:
                errors.append(f"{prefix}:quarters_do_not_sum_to_four")
            if float(row["mcap_coverage_hindsight"]) < MINIMUM_MCAP_COVERAGE:
                errors.append(f"{prefix}:hindsight_published_below_mcap_gate")
        if tier == "actual_only" and int(row["hindsight_estimate_quarters"]) != 0:
            errors.append(f"{prefix}:actual_only_with_consensus_quarters")
        if tier == "latest_consensus_tail" \
                and int(row["hindsight_estimate_quarters"]) == 0:
            errors.append(f"{prefix}:consensus_tail_without_consensus_quarters")
        if row["ttm_pe_gaap"] is None:
            if not _gate_warning_present(row, (
                    "mcap_coverage_ttm_below_gate",
                    "weight_coverage_ttm_below_gate",
                    "ttm_net_income_not_positive", "ttm_no_covered_member")):
                errors.append(f"{prefix}:ttm_null_without_a_recorded_reason")
        elif float(row["mcap_coverage_ttm"]) < MINIMUM_MCAP_COVERAGE:
            errors.append(f"{prefix}:ttm_published_below_mcap_gate")
    return errors


def _member_list(row: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    members = row["members_json"]
    if isinstance(members, Mapping):
        return list(members.get("members") or [])
    return list(members or [])


def _weight_coverage(members: Sequence[Mapping[str, Any]], income_key: str) -> float:
    total = sum(float(member["weight_pct"]) for member in members
                if member.get("weight_pct") is not None
                and float(member["weight_pct"]) > 0)
    covered = sum(
        float(member["weight_pct"]) for member in members
        if member.get("weight_pct") is not None
        and float(member["weight_pct"]) > 0
        and member.get("market_cap") is not None
        and member.get(income_key) is not None)
    return covered / total if total > 0 else 0.0


def _disclosed_membership(
    conn: sqlite3.Connection, basket: str,
) -> List[Dict[str, Any]]:
    """Reconstruct weights independently, preserving each source snapshot.

    Effective dates are not snapshot identities: live and disclosure rows
    can describe the same rebalance at different weights and availability
    dates. Their source PK is (basket, holding_date, source_kind, row_index).
    Normalized covered_by rows contribute to their target, including orphan
    targets which the producer retains as excluded members.
    """
    snapshots: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in _rows(
            conn,
            "SELECT holding_date, source_kind, composition_effective_date, "
            "composition_available_date, symbol, covered_by, "
            "weight_pct, included FROM fmp_fund_disclosure_holdings "
            "WHERE basket_symbol = ? ORDER BY holding_date, source_kind, "
            "raw_row_index", [basket]):
        key = (str(row["holding_date"]), str(row["source_kind"]))
        snapshot = snapshots.setdefault(key, {
            "holding_date": key[0], "source_kind": key[1],
            "composition_effective_date": str(row["composition_effective_date"]),
            "composition_available_date": str(row["composition_available_date"]),
            "weights": {}, "errors": [],
        })
        for field in ("composition_effective_date", "composition_available_date"):
            if str(row[field]) != snapshot[field]:
                snapshot["errors"].append(f"source_snapshot_inconsistent:{field}")
        target = row["symbol"] if row["included"] == 1 else row["covered_by"]
        if not target:
            continue
        company = str(target).upper()
        try:
            weight = float(row["weight_pct"])
        except (TypeError, ValueError):
            snapshot["errors"].append(f"source_member_weight_invalid:{company}")
            continue
        if not math.isfinite(weight) or weight < 0:
            snapshot["errors"].append(f"source_member_weight_invalid:{company}")
            continue
        weights = snapshot["weights"]
        weights[company] = weights.get(company, 0.0) + weight
    return list(snapshots.values())


def _membership_errors(
    basket: str, rows: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
) -> List[str]:
    """Select the eligible snapshot, then compare its identity and membership."""
    errors: List[str] = []
    for row in rows:
        valuation_date = str(row["valuation_date"])
        prefix = f"{basket}:{valuation_date}"
        eligible = [item for item in snapshots
                    if item["composition_effective_date"] <= valuation_date
                    and item["composition_available_date"] <= valuation_date]
        if not eligible:
            errors.append(f"{prefix}:no_eligible_membership_snapshot")
            continue
        # Independent implementation of the frozen selection contract:
        # newest effective, disclosure before live, newest holding date.
        selected = max(eligible, key=lambda item: (
            item["composition_effective_date"],
            item["source_kind"] == "disclosure", item["holding_date"]))
        errors.extend(f"{prefix}:{error}" for error in selected["errors"])
        if str(row["composition_effective_date"]) != selected[
                "composition_effective_date"]:
            errors.append(f"{prefix}:not_the_latest_eligible_composition")
        payload = row["members_json"]
        basis = ("live_snapshot_backcast_proxy"
                 if selected["source_kind"] == "live"
                 else "fixed_rebalance_weight_proxy")
        if (not isinstance(payload, Mapping)
                or payload.get("holding_date") != selected["holding_date"]
                or payload.get("weight_basis") != basis
                or str(row["composition_available_date"]) != selected[
                    "composition_available_date"]):
            errors.append(f"{prefix}:snapshot_identity_mismatch")
        expected = selected["weights"]
        if not expected:
            errors.append(f"{prefix}:source_snapshot_has_no_eligible_members")
        observed = {}
        for member in _member_list(row):
            symbol = str(member.get("raw_symbol") or member.get("symbol")
                         or "").upper()
            if symbol in observed:
                errors.append(f"{prefix}:duplicate_member:{symbol}")
            try:
                weight = float(member.get("weight_pct"))
            except (TypeError, ValueError):
                weight = None
            observed[symbol] = weight
        for company in sorted(expected):
            if company not in observed:
                errors.append(
                    f"{prefix}:member_missing_from_members_json:{company}")
                continue
            if observed[company] is None:
                errors.append(f"{prefix}:member_has_no_weight:{company}")
            elif not _close(observed[company], expected[company],
                            tolerance=1e-6):
                errors.append(
                    f"{prefix}:member_weight_disagrees_with_the_disclosure:"
                    f"{company}:{observed[company]}!={expected[company]}")
        for company in sorted(observed):
            if company not in expected:
                errors.append(
                    f"{prefix}:member_not_in_the_disclosed_composition:{company}")
    return errors


def _hindsight_evidence_errors(member: Mapping[str, Any], prefix: str) -> List[str]:
    """Required reconstruction inputs are checked on every row, not sampled."""
    if member.get("hindsight_ntm_net_income_usd") is None:
        return []
    prefix = f"{prefix}:{member.get('symbol')}"
    window = member.get("hindsight_window")
    if not isinstance(window, list) or len(window) != 2:
        return [f"{prefix}:hindsight_evidence_missing"]
    try:
        start, end = (date.fromisoformat(value) for value in window)
    except (TypeError, ValueError):
        return [f"{prefix}:hindsight_window_invalid"]
    if start >= end:
        return [f"{prefix}:hindsight_window_invalid"]
    if int(member.get("hindsight_estimate_quarters") or 0) > 0:
        try:
            date.fromisoformat(member.get("hindsight_snapshot_date"))
        except (TypeError, ValueError):
            return [f"{prefix}:hindsight_snapshot_evidence_missing"]
    return []


def _evidence_errors(
    rows: Sequence[Mapping[str, Any]],
    max_staleness_days: int = DEFAULT_MARKET_CAP_STALENESS_DAYS,
) -> List[str]:
    """Recompute both coverages and both member sets from members_json.

    Plain arithmetic, not the producer's kernel: this is the heterogeneous
    half of the reconciliation.
    """
    errors = []
    for row in rows:
        prefix = f"{row['basket']}:{row['valuation_date']}"
        payload = row["members_json"]
        if not isinstance(payload, Mapping):
            errors.append(f"{prefix}:members_json_not_an_object")
            continue
        members = _member_list(row)
        if not members:
            errors.append(f"{prefix}:members_json_empty")
            continue
        if len(members) != int(row["n_members"]):
            errors.append(f"{prefix}:n_members_mismatch")
        if payload.get("is_ex_post") != 1:
            errors.append(f"{prefix}:hindsight_not_flagged_ex_post")
        if "consensus_snapshot_date" not in payload:
            errors.append(f"{prefix}:consensus_vintage_missing")
        if (row["quality_tier"] == "latest_consensus_tail"
                and not payload.get("consensus_snapshot_date")):
            errors.append(f"{prefix}:consensus_tail_without_a_vintage")
        for key, income_key in (
                ("weight_coverage_ttm", "ttm_net_income_usd"),
                ("weight_coverage_hindsight", "hindsight_ntm_net_income_usd")):
            if payload.get(key) is None:
                errors.append(f"{prefix}:{key}_missing")
                continue
            if not _close(payload[key], _weight_coverage(members, income_key),
                          tolerance=1e-6):
                errors.append(f"{prefix}:{key}_not_reproducible")
        published_pairs = (
            ("ttm_pe_gaap", "weight_coverage_ttm"),
            ("hindsight_ntm_pe_gaap", "weight_coverage_hindsight"),
        )
        for pe_field, coverage_key in published_pairs:
            if row[pe_field] is None:
                continue
            if float(payload.get(coverage_key) or 0.0) < MINIMUM_WEIGHT_COVERAGE:
                errors.append(f"{prefix}:{pe_field}_published_below_weight_gate")
        # Symmetric member sets: a covered member has both sides, and the
        # stored totals are exactly the sum over those members.
        ttm_covered = [member for member in members
                       if member.get("market_cap") is not None
                       and member.get("ttm_net_income_usd") is not None]
        if len(ttm_covered) != int(row["n_covered_ttm"]):
            errors.append(f"{prefix}:n_covered_ttm_mismatch")
        if not _close(row["ttm_total_mcap"],
                      sum(float(m["market_cap"]) for m in ttm_covered)):
            errors.append(f"{prefix}:ttm_total_mcap_not_reproducible")
        if not _close(row["ttm_net_income"],
                      sum(float(m["ttm_net_income_usd"]) for m in ttm_covered)):
            errors.append(f"{prefix}:ttm_net_income_not_reproducible")
        hindsight_covered = [
            member for member in members
            if member.get("market_cap") is not None
            and member.get("hindsight_ntm_net_income_usd") is not None]
        if len(hindsight_covered) != int(row["n_covered_hindsight"]):
            errors.append(f"{prefix}:n_covered_hindsight_mismatch")
        for member in hindsight_covered:
            quarters = (int(member.get("hindsight_actual_quarters") or 0)
                        + int(member.get("hindsight_estimate_quarters") or 0))
            if quarters != 4:
                errors.append(
                    f"{prefix}:{member.get('symbol')}:member_quarters_not_four")
        # Date crossing: no observation may be dated after the valuation date,
        # and none may be staler than the as-of rule allows.
        valuation = date.fromisoformat(str(row["valuation_date"]))
        if str(row["valuation_date"]) < str(row["composition_effective_date"]):
            errors.append(f"{prefix}:valuation_before_composition_effective")
        # A composition is knowable only from its disclosure. Checking the
        # effective date alone shares the producer's blind spot: a rebalance in
        # force from 2025-12-22 but disclosed 2026-02-15 would certify a
        # December valuation built on February information.
        if str(row["valuation_date"]) < str(row["composition_available_date"]):
            errors.append(
                f"{prefix}:composition_not_yet_disclosed:"
                f"{row['composition_available_date']}")
        for member in members:
            errors.extend(_hindsight_evidence_errors(member, prefix))
            observed = member.get("market_cap_date")
            if not observed:
                continue
            try:
                observed_date = date.fromisoformat(str(observed))
            except ValueError:
                errors.append(f"{prefix}:{member.get('symbol')}:mcap_date_shape")
                continue
            if observed_date > valuation:
                errors.append(f"{prefix}:{member.get('symbol')}:mcap_date_crossing")
            elif (valuation - observed_date).days > max_staleness_days:
                errors.append(f"{prefix}:{member.get('symbol')}:mcap_date_stale")
    return errors


def _kernel_reconciliation_errors(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    """Shared kernel vs plain arithmetic on the same materialised members.

    The kernel is imported, not re-implemented (R3), so this compares the
    stored number against both: the kernel replayed over members_json, and a
    direct ratio of the two sums.
    """
    errors = []
    for row in rows:
        prefix = f"{row['basket']}:{row['valuation_date']}"
        members = _member_list(row)
        for income_key, pe_field, coverage_field in (
                ("ttm_net_income_usd", "ttm_pe_gaap", "mcap_coverage_ttm"),
                ("hindsight_ntm_net_income_usd", "hindsight_ntm_pe_gaap",
                 "mcap_coverage_hindsight")):
            replay = compute_aggregate_basket_pe(members, income_key=income_key)
            if not _close(replay["mcap_coverage"], row[coverage_field]):
                errors.append(f"{prefix}:{coverage_field}_kernel_mismatch")
            if row[pe_field] is None:
                continue
            covered = [member for member in members
                       if member.get("market_cap") is not None
                       and member.get(income_key) is not None]
            numerator = sum(float(member["market_cap"]) for member in covered)
            denominator = sum(float(member[income_key]) for member in covered)
            if denominator <= 0:
                errors.append(f"{prefix}:{pe_field}_published_on_a_bad_denominator")
                continue
            if not _close(row[pe_field], numerator / denominator):
                errors.append(f"{prefix}:{pe_field}_not_reproducible")
            if not _close(replay["pe"], numerator / denominator):
                errors.append(f"{prefix}:{pe_field}_kernel_disagrees_with_arithmetic")
    return errors


def _company_identities(
    conn: sqlite3.Connection, basket: str, overrides=(),
) -> Dict[Tuple[str, str], Dict[str, Optional[str]]]:
    """Independently derive canonical issuer keys from physical source evidence.

    Do not import the producer's resolver: a normalized-LEI mapping bug must
    disagree with this raw reconstruction. Filer CIK is deliberately unused.
    Conflicting or missing evidence in any class poisons its merged target.
    """
    source_rows = _rows(
        conn, "SELECT * FROM fmp_fund_disclosure_holdings WHERE basket_symbol = ?",
        [basket])
    disclosures = {}

    class EvidenceConflict(ValueError):
        pass

    def source_identity(row):
        try:
            raw = json.loads(row.get("raw_payload_json") or "null")
            if not isinstance(raw, dict):
                raise ValueError("missing raw issuer evidence")
            is_live = row["source_kind"] == "live"
            raw_symbol = str(raw.get("asset" if is_live else "symbol") or "").strip().upper()
            if raw_symbol != row["raw_symbol"]:
                raise ValueError("raw security belongs to another source row")
            if not is_live and raw.get("assetCat") != "EC":
                raise ValueError("non-equity source cannot certify an equity member")
            cusip = (raw.get("securityCusip") or raw.get("cusip")) if is_live else raw.get("cusip")
            if is_live and raw.get("cusip") and raw.get("securityCusip") and raw["cusip"] != cusip:
                raise ValueError("CUSIP conflict")
            for field, original in (("issuer_lei", raw.get("lei")), ("cusip", cusip),
                                    ("isin", raw.get("isin")), ("asset_category", raw.get("assetCat"))):
                if row.get(field) != original:
                    raise ValueError("normalized identity disagrees with raw source")
            original = raw.get("lei")
            in_range = [entry for entry in overrides
                        if entry["valid_from"] <= row["holding_date"] <= entry["valid_to"]]
            matching = []
            for entry in in_range:
                if entry["isin"] != raw.get("isin"):
                    continue
                exact = entry.get("cusip") == cusip and cusip not in (None, "", "N/A", "000000000")
                missing_allowed = (entry.get("match_mode") == "isin_allow_missing_cusip"
                                   and cusip in entry.get("missing_cusip_values", []))
                if not (exact or missing_allowed):
                    raise EvidenceConflict("reviewed ISIN has conflicting CUSIP")
                matching.append(entry)
            choices = {entry.get("canonical_issuer_key") or "lei:" + entry["issuer_lei"] for entry in matching}
            if "sec-cik:" + str(row.get("cik")) in choices:
                raise ValueError("fund filer CIK cannot supply issuer identity")
            corrections = [entry for entry in matching if original in entry.get("expected_raw_leis", [])]
            corrected = bool(original not in (None, "", "N/A") and matching and len(corrections) == len(matching))
            if original not in (None, "", "N/A") and not corrected:
                if not valid_issuer_lei(original):
                    raise ValueError("invalid issuer LEI")
                keys = {entry.get("canonical_issuer_key") or "lei:" + entry["issuer_lei"]
                        for entry in in_range if entry.get("issuer_lei") == original
                        or original in entry.get("equivalent_leis", [])}
                if len(keys) > 1:
                    raise EvidenceConflict("issuer aliases conflict")
                choices.add(next(iter(keys)) if keys else "lei:" + original)
            if "sec-cik:" + str(row.get("cik")) in choices:
                raise ValueError("issuer alias resolves to fund filer CIK")
            if is_live and cusip not in (None, "", "N/A", "000000000"):
                # Select a complete snapshot first: an absent or unresolved
                # security in it cannot make an older disclosure eligible.
                eligible = [day for day, snapshot in disclosures.items()
                            if day <= row["holding_date"]
                            and snapshot["available"] is not None
                            and snapshot["available"] <= row["holding_date"]]
                latest = disclosures[max(eligible)] if eligible else None
                evidence = latest["securities"].get(cusip) if latest else None
                if evidence is not None:
                    inherited, source_isin, status = evidence
                    if (status == "conflict" or (source_isin and raw.get("isin")
                                                and source_isin != raw["isin"])):
                        raise EvidenceConflict("latest disclosure security conflict")
                    if inherited is not None:
                        choices.add(inherited)
            if "sec-cik:" + str(row.get("cik")) in choices:
                raise ValueError("inherited identity resolves to fund filer CIK")
            if len(choices) > 1:
                raise EvidenceConflict("issuer evidence conflicts")
            if len(choices) == 1:
                return next(iter(choices)), "resolved"
        except EvidenceConflict:
            return None, "conflict"
        except (TypeError, ValueError):
            pass  # Invalid raw evidence cannot certify a source identity.
        return None, "unresolved"

    # Keep the entire physical disclosure inventory, including rows which
    # cannot resolve and rows outside the materialised valuation window.
    for row in source_rows:
        if row["source_kind"] != "disclosure":
            continue
        snapshot = disclosures.setdefault(str(row["holding_date"]), {
            "available": row.get("composition_available_date"), "securities": {}})
        available = row.get("composition_available_date")
        if snapshot["available"] is None or available is None:
            snapshot["available"] = None
        else:
            snapshot["available"] = max(snapshot["available"], available)
        cusip = row.get("cusip")
        if cusip in (None, "", "N/A", "000000000"):
            continue
        issuer, status = source_identity(row)
        evidence = (issuer, row.get("isin"), status)
        previous = snapshot["securities"].get(cusip)
        if previous is not None and (previous[2] == "conflict"
                                     or previous[:2] != evidence[:2]):
            evidence = (None, previous[1], "conflict")
        snapshot["securities"][cusip] = evidence

    candidates = {}
    for row in source_rows:
        if not row.get("included") and not row.get("covered_by"):
            continue
        key = (str(row["holding_date"]), str(row["source_kind"]))
        lei, _ = source_identity(row)
        for ticker in (row["raw_symbol"], row["symbol"], row["covered_by"],
                       row.get("alias_symbol") if row.get("alias_mode") == "authoritative" else None):
            if ticker:
                candidates.setdefault(key, {}).setdefault(str(ticker).upper(), set()).add(lei)
    return {key: {ticker: next(iter(values)) if len(values) == 1 else None
                  for ticker, values in names.items()} for key, names in candidates.items()}


def _company_identity_check(
    basket: str,
    rows: Sequence[Mapping[str, Any]],
    identities: Mapping[Tuple[str, str], Mapping[str, Optional[str]]],
) -> Dict[str, Any]:
    """Two covered members of one company on one date is a double count.

    ``share_class_groups.json`` can only cover the pairs someone has looked at.
    A five-year history contains pairs that no longer exist -- SPY held
    DISCA/DISCK until 2022-04 -- and FMP files the whole company's net income
    under every class, so an uncovered pair divides one company's market cap by
    twice its earnings. The hindsight engine rejects a duplicate company key,
    but the TTM aggregate has no such backstop, and neither knows about a pair
    absent from the config. Issuer LEI is distinct from the fund's filer CIK.

    Every covered member must resolve, not most of them: an unresolved member
    is precisely where an uncovered dual-class pair hides, so counting the gap
    would leave the check fail-open on the case it exists for.
    """
    errors: List[str] = []
    with_identity = 0
    without_identity = 0
    for row in rows:
        payload = row["members_json"]
        if not isinstance(payload, Mapping):
            errors.append(f"{basket}:{row['valuation_date']}:identity_members_payload_invalid")
            continue
        kind = "live" if payload.get("weight_basis") == "live_snapshot_backcast_proxy" else "disclosure"
        mapping = identities.get((payload.get("holding_date"), kind), {})
        seen: Dict[str, List[str]] = {}
        for member in _member_list(row):
            if member.get("market_cap") is None:
                continue
            if (member.get("ttm_net_income_usd") is None
                    and member.get("hindsight_ntm_net_income_usd") is None):
                continue
            symbol = str(member.get("symbol") or "").upper()
            raw_symbol = str(member.get("raw_symbol") or symbol).upper()
            lei = mapping.get(raw_symbol)
            if not lei:
                without_identity += 1
                errors.append(
                    f"{basket}:{row['valuation_date']}:"
                    f"company_identity_unresolved:{symbol}")
                continue
            with_identity += 1
            seen.setdefault(lei, []).append(symbol)
        for lei, tickers in sorted(seen.items()):
            if len(tickers) > 1:
                errors.append(
                    f"{basket}:{row['valuation_date']}:duplicate_company_issuer:"
                    f"{lei}:{'+'.join(sorted(tickers))}")
    if rows and with_identity == 0:
        errors.append(f"{basket}:company_identity_unavailable")
    return {"errors": errors, "members_with_identity": with_identity,
            "members_without_identity": without_identity}


# ---------------------------------------------------------------------------
# raw-source spot checks (direct SQL, no producer code path)
# ---------------------------------------------------------------------------

def _market_cap_asof_sql(
    conn: sqlite3.Connection, symbol: str, valuation_date: str,
) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT date, market_cap FROM historical_market_cap "
        "WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        [symbol, valuation_date]).fetchone()
    return dict(row) if row else None


def _sanity_status_sql(
    conn: sqlite3.Connection, symbol: str, observed_date: str,
    cache: Optional[Dict[str, Dict[str, str]]] = None,
) -> Optional[str]:
    """Rescan the raw tables; never trust a status the producer recorded.

    Cached per symbol because the scan is over that symbol's whole history and
    a sample hits the same members on every sampled date.
    """
    store = cache if cache is not None else {}
    if symbol not in store:
        classifications = scan_market_cap_candidates(
            _rows(conn, "SELECT * FROM historical_market_cap WHERE symbol = ? "
                        "ORDER BY date", [symbol]),
            _rows(conn, "SELECT * FROM daily_price WHERE symbol = ? "
                        "ORDER BY date", [symbol]),
            _rows(conn, "SELECT * FROM fmp_stock_splits WHERE symbol = ? "
                        "ORDER BY date", [symbol])
            if _table_exists(conn, "fmp_stock_splits") else [])
        store[symbol] = {str(item["date"]): str(item["status"])
                         for item in classifications}
    return store[symbol].get(observed_date)


def _fx_asof_sql(
    conn: sqlite3.Connection, currency: str, valuation_date: str,
    max_staleness_days: int,
) -> Optional[float]:
    """USD per unit at the valuation date, by the engine's own convention."""
    if currency == "USD":
        return 1.0
    row = conn.execute(
        "SELECT date, usd_per_unit FROM fx_daily WHERE currency = ? "
        "AND date <= ? ORDER BY date DESC LIMIT 1",
        [currency, valuation_date]).fetchone()
    if row is None or row["usd_per_unit"] is None:
        return None
    staleness = (date.fromisoformat(valuation_date)
                 - date.fromisoformat(str(row["date"]))).days
    if staleness > max_staleness_days:
        return None
    return float(row["usd_per_unit"])


def _hindsight_income_sql(
    conn: sqlite3.Connection, symbol: str, valuation_date: str,
    window: Sequence[str], snapshot_date: Optional[str],
    max_staleness_days: int,
) -> Optional[float]:
    """Rebuild the four hindsight quarters from raw rows, or decline.

    The producer records which fiscal window its number covers, so this does
    not have to re-derive the window selection -- it reads the same window out
    of ``income_quarterly`` and, for quarters the member had not reported yet,
    out of ``fmp_estimates`` at the vintage the row recorded. Ambiguity
    (a mixed reporting currency, an estimate landing on top of an actual,
    anything other than four continuous quarters) declines rather than guesses;
    a mismatch on a case it does accept is a real finding.
    """
    if not window or len(window) != 2:
        return None
    start, end = str(window[0]), str(window[1])
    actuals = _rows(
        conn,
        "SELECT date, reported_currency, net_income FROM income_quarterly "
        "WHERE symbol = ? AND date BETWEEN ? AND ? AND period LIKE 'Q%' "
        "AND net_income IS NOT NULL ORDER BY date", [symbol, start, end])
    if len({str(row["date"]) for row in actuals}) != len(actuals):
        return None
    quarters = [(str(row["date"]), str(row["reported_currency"] or "").upper(),
                 float(row["net_income"])) for row in actuals]
    if len(quarters) < 4:
        if not snapshot_date:
            return None
        currencies = {currency for _, currency, _ in quarters}
        if len(currencies) > 1:
            return None
        inherited = next(iter(currencies), None)
        if inherited is None:
            return None
        for row in _rows(
                conn,
                "SELECT fiscal_date, net_income_avg FROM fmp_estimates "
                "WHERE symbol = ? AND snapshot_date = ? AND period_type = 'Q' "
                "AND snapshot_kind = 'weekly' AND fiscal_date BETWEEN ? AND ? "
                "AND net_income_avg IS NOT NULL ORDER BY fiscal_date",
                [symbol, snapshot_date, start, end]):
            fiscal = str(row["fiscal_date"])
            # An estimate sitting on top of a quarter the member already
            # reported is the overlap the engine de-duplicates; decline rather
            # than reproduce that rule here.
            if any(abs((date.fromisoformat(fiscal)
                        - date.fromisoformat(actual)).days) <= 45
                   for actual, _, _ in quarters):
                return None
            quarters.append((fiscal, inherited, float(row["net_income_avg"])))
    quarters.sort()
    if len(quarters) != 4:
        return None
    if len({currency for _, currency, _ in quarters}) != 1:
        return None
    fiscal_dates = [date.fromisoformat(fiscal) for fiscal, _, _ in quarters]
    if any(not 60 <= (later - earlier).days <= 120
           for earlier, later in zip(fiscal_dates, fiscal_dates[1:])):
        return None
    rate = _fx_asof_sql(conn, quarters[0][1], valuation_date,
                        max_staleness_days)
    if rate is None:
        return None
    return sum(value for _, _, value in quarters) * rate


def _ttm_income_sql(
    conn: sqlite3.Connection, symbol: str, valuation_date: str,
    max_staleness_days: int = DEFAULT_MARKET_CAP_STALENESS_DAYS,
) -> Optional[float]:
    """Sum four visible quarters straight out of SQL, or decline.

    Deliberately simple: it only reconciles the easy majority (four clean,
    continuous, already-accepted quarters in one reporting currency) and
    returns None rather than guessing on restatements or malformed rows. A
    mismatch on a case it does accept is a real finding.
    """
    rows = _rows(
        conn,
        "SELECT date, period, accepted_date, reported_currency, net_income "
        "FROM income_quarterly WHERE symbol = ? AND date <= ? "
        "AND period LIKE 'Q%' AND net_income IS NOT NULL "
        "AND accepted_date IS NOT NULL AND substr(accepted_date,1,10) < ? "
        "ORDER BY date DESC LIMIT 5",
        [symbol, valuation_date, valuation_date])
    if len({str(row["date"]) for row in rows}) < 4:
        return None
    selected = rows[:4]
    currencies = {str(row["reported_currency"] or "").upper()
                  for row in selected}
    if len(currencies) != 1:
        return None
    fiscal = sorted(date.fromisoformat(str(row["date"])) for row in selected)
    if any(not 60 <= (later - earlier).days <= 120
           for earlier, later in zip(fiscal, fiscal[1:])):
        return None
    rate = _fx_asof_sql(conn, next(iter(currencies)), valuation_date,
                        max_staleness_days)
    if rate is None:
        return None
    return sum(float(row["net_income"]) for row in selected) * rate


def _raw_source_reconciliation(
    conn: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    conventions: Mapping[str, Optional[str]],
    groups: Mapping[str, Sequence[str]],
    max_staleness_days: int = DEFAULT_MARKET_CAP_STALENESS_DAYS,
) -> Dict[str, Any]:
    errors: List[str] = []
    reconciled_mcap = 0
    reconciled_income = 0
    declined_income = 0
    reconciled_hindsight = 0
    declined_hindsight = 0
    sanity_cache: Dict[str, Dict[str, str]] = {}
    for row in rows:
        valuation_date = str(row["valuation_date"])
        prefix = f"{row['basket']}:{valuation_date}"
        payload = row["members_json"] if isinstance(
            row["members_json"], Mapping) else {}
        row_snapshot = payload.get("consensus_snapshot_date")
        for member in _member_list(row):
            symbol = str(member.get("symbol") or "").upper()
            if member.get("market_cap") is None:
                continue
            secondaries = [str(value).upper()
                           for value in groups.get(symbol, [])]
            observation = _market_cap_asof_sql(conn, symbol, valuation_date)
            if observation is None:
                errors.append(f"{prefix}:{symbol}:no_raw_market_cap_at_or_before")
                continue
            expected = float(observation["market_cap"])
            if secondaries and conventions.get(symbol) == "split_across_classes":
                for secondary in secondaries:
                    part = _market_cap_asof_sql(conn, secondary, valuation_date)
                    if part is None:
                        expected = None
                        break
                    expected += float(part["market_cap"])
            if expected is None:
                errors.append(
                    f"{prefix}:{symbol}:share_class_component_missing_in_raw")
                continue
            if not _close(member["market_cap"], expected, tolerance=1e-6):
                errors.append(
                    f"{prefix}:{symbol}:market_cap_disagrees_with_raw_source")
                continue
            status = _sanity_status_sql(conn, symbol, str(observation["date"]),
                                        sanity_cache)
            if status is not None and not accepted_market_cap_status(status):
                errors.append(f"{prefix}:{symbol}:published_on_{status}_market_cap")
                continue
            reconciled_mcap += 1
            if member.get("ttm_net_income_usd") is None:
                continue
            recomputed = _ttm_income_sql(conn, symbol, valuation_date,
                                         max_staleness_days)
            if recomputed is None:
                declined_income += 1
            elif not _close(member["ttm_net_income_usd"], recomputed,
                            tolerance=1e-6):
                errors.append(
                    f"{prefix}:{symbol}:ttm_income_disagrees_with_raw_source")
            else:
                reconciled_income += 1
        # The ex-post line gets its own rebuild. Without it a member's
        # hindsight income can be moved by two orders of magnitude and every
        # other check still passes, because they all read the same tampered
        # members_json.
        for member in _member_list(row):
            symbol = str(member.get("symbol") or "").upper()
            if member.get("hindsight_ntm_net_income_usd") is None:
                continue
            evidence_errors = _hindsight_evidence_errors(member, prefix)
            if evidence_errors:
                errors.extend(evidence_errors)
                continue
            recomputed = _hindsight_income_sql(
                conn, symbol, valuation_date, member["hindsight_window"],
                member.get("hindsight_snapshot_date") or row_snapshot,
                max_staleness_days)
            if recomputed is None:
                declined_hindsight += 1
                continue
            if not _close(member["hindsight_ntm_net_income_usd"], recomputed,
                          tolerance=1e-6):
                errors.append(
                    f"{prefix}:{symbol}:"
                    "hindsight_income_disagrees_with_raw_source")
                continue
            reconciled_hindsight += 1
    # A sample that contains hindsight rows but reconciles none of them has
    # not checked anything: every path declined, and "no errors" is then a
    # statement about the checker, not about the data.
    if declined_hindsight and reconciled_hindsight == 0:
        errors.append(
            f"{rows[0]['basket'] if rows else '?'}:"
            f"no_hindsight_income_reconciled:{declined_hindsight}_declined")
    return {
        "errors": errors,
        "reconciled_market_caps": reconciled_mcap,
        "reconciled_ttm_incomes": reconciled_income,
        "declined_ttm_incomes": declined_income,
        "reconciled_hindsight_incomes": reconciled_hindsight,
        "declined_hindsight_incomes": declined_hindsight,
    }


# ---------------------------------------------------------------------------
# expected weekly denominator
# ---------------------------------------------------------------------------

def _uncertified_row_errors(
    basket: str, conn: sqlite3.Connection,
    events: Sequence[Mapping[str, Any]],
) -> List[str]:
    """Rows no completed run ever claimed responsibility for.

    The frozen week set makes the denominator immutable, but it only speaks
    about the window it covers. A run that failed after writing may have left
    rows *outside* that window -- an earlier `--as-of`, a longer `--years` --
    where a check scoped to the certifying window never even loads them. Every
    stored row must fall inside the window of some run that completed;
    anything else is data certified by nothing.

    Windows come from completed runs only, and only from runs that did not
    also record a failure, so an earlier run that finished its own five years
    keeps its rows legitimate while a self-contradicting one lends nothing.
    """
    valid = set(_valid_completed_runs(events))
    windows = [(str(row["expected_from_date"]), str(row["expected_to_date"]))
               for row in events
               if row["event_kind"] == "run_completed"
               and str(row["run_id"]) in valid]
    errors = []
    for row in _rows(conn, "SELECT valuation_date FROM "
                           "basket_weekly_pe_history WHERE basket = ? "
                           "ORDER BY valuation_date", [basket]):
        valuation_date = str(row["valuation_date"])
        if not any(start <= valuation_date <= end for start, end in windows):
            errors.append(
                f"{basket}:{valuation_date}:row_outside_every_certified_window")
    return errors


def _week_key(day: str) -> Tuple[int, int]:
    return date.fromisoformat(day).isocalendar()[:2]


def _denominator_errors(
    basket: str, expected_weeks: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> List[str]:
    """Stored rows against the week set the run froze in its manifest.

    The comparison runs both ways. A missing week fails, and so does a surplus
    row: letting one pass because it is absent from the expected set would
    make the frozen denominator a floor instead of an equality.

    The expectation is never rebuilt from daily_price or the disclosure table:
    deleting a week's rows *and* that week's prices would otherwise erase both
    the data and the evidence that it was ever owed.

    There is deliberately no `history_available_from` exemption here. A
    basket's documented pre-history gap is expressed once, in the frozen
    floor, and `_manifest_errors` rejects a frozen set that reaches below
    either that floor or the config bound. By the time a week is in the frozen
    set it is owed, full stop -- an exemption at comparison time would be a
    second place for a gap to be declared, and the only thing it could still
    excuse is a week the run itself said it would publish.
    """
    errors: List[str] = []
    expected_keys = {_week_key(week_end): week_end
                     for week_end in expected_weeks}
    observed: Dict[Tuple[int, int], List[str]] = {}
    for row in rows:
        observed.setdefault(
            _week_key(str(row["valuation_date"])), []).append(
                str(row["valuation_date"]))
    for key, days in sorted(observed.items()):
        if len(days) > 1:
            errors.append(f"{basket}:{key}:more_than_one_row_in_one_week")
        if key not in expected_keys:
            errors.append(f"{basket}:{key}:row_outside_the_expected_range")
            continue
        if days[0] > expected_keys[key]:
            errors.append(f"{basket}:{days[0]}:after_its_weeks_last_trading_day")
    for key, week_end in sorted(expected_keys.items()):
        if key in observed:
            continue
        errors.append(f"{basket}:{week_end}:expected_week_has_no_row")
    return errors


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------

def _source_alias_binding_errors(conn, basket, aliases):
    """Independently bind exact disclosed securities to their financial issuer."""
    errors, checked = [], set()
    for row in _rows(conn, "SELECT * FROM fmp_fund_disclosure_holdings WHERE basket_symbol=?", [basket]):
        raw = str(row.get("raw_symbol") or row.get("symbol") or "").upper()
        rule = aliases.get(raw)
        if not rule:
            continue
        if (row.get("cusip") != rule["cusip"] or row.get("isin") != rule["isin"]
                or row.get("alias_symbol") != rule["symbol"] or row.get("alias_mode") != rule["mode"]):
            errors.append(f"{basket}:{raw}:stale_alias_or_security_mismatch")
        target, expected = rule["symbol"], rule.get("issuer_cik")
        if expected and target not in checked:
            values = _rows(conn, "SELECT DISTINCT cik FROM income_quarterly WHERE symbol=?", [target])
            if any(str(value.get("cik") or "").zfill(10) != expected for value in values):
                errors.append(f"{basket}:{raw}:{target}:income_issuer_mismatch")
            checked.add(target)
    return errors


def verify_database(
    conn: sqlite3.Connection,
    baskets: Sequence[str] = ("SPY", "QQQ", "SOXX"),
    as_of: Optional[str] = None,
    years: int = 5,
    sample: int = DEFAULT_SAMPLE,
    config_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    if as_of is None:
        raise ValueError("--as-of is required to pin the expected window")
    requested = _window(as_of, years)
    missing = sorted(table for table in REQUIRED_TABLES
                     if not _table_exists(conn, table))
    if missing:
        raise ValueError(f"required tables missing: {', '.join(missing)}")
    columns = {row["name"] for row in _rows(
        conn, "SELECT name FROM pragma_table_info('basket_weekly_pe_history')")}
    if "run_id" not in columns:
        # Defence in depth for the schema migration: without the ownership
        # column every row is unattributable and certification is meaningless.
        raise ValueError(
            "basket_weekly_pe_history has no run_id column; the database "
            "predates row-level ownership and must be migrated before its "
            "contents can be verified")
    config_root = Path(config_dir or CONFIG_DIR)
    share_config = default_share_class_config(config_root)
    basket_configs = load_index_pe_basket_configs(config_root)
    issuer_overrides = load_issuer_overrides(config_root)
    alias_path = config_root / "soxx_symbol_aliases.json"
    if not alias_path.exists():
        alias_path = config_root.parent / "soxx_symbol_aliases.json"
    aliases = load_soxx_symbol_aliases(alias_path) if alias_path.exists() else {}
    binding_errors = [error for basket in baskets
                      for error in _source_alias_binding_errors(conn, basket, aliases)]

    probe = _connection_is_read_only(conn)
    checks = [
        _check("read_only_connection", probe["refused"],
               {"write_probe_refused": probe["refused"],
                "sqlite_error": probe["error"],
                "mode": "ro + query_only + single read transaction"}),
        _check("no_holding_weighted_columns",
               not (columns & set(FORBIDDEN_COLUMNS)),
               {"forbidden_present": sorted(columns & set(FORBIDDEN_COLUMNS))}),
        _check("fx_allowlist_covers_basket_currencies",
               {"EUR", "TWD", "CNY"} <= set(USD_PER_UNIT_BOUNDS),
               sorted(USD_PER_UNIT_BOUNDS)),
    ]

    manifest_errors: List[str] = []
    provenance_errors: List[str] = []
    denominator_errors: List[str] = []
    tier_errors: List[str] = []
    evidence_errors: List[str] = []
    kernel_errors: List[str] = []
    json_errors: List[str] = []
    version_errors: List[str] = []
    identity_errors: List[str] = []
    integrity_errors: List[str] = []
    identity_totals = {"members_with_identity": 0, "members_without_identity": 0}
    per_basket: Dict[str, Any] = {}
    reconciliation = {"errors": [], "reconciled_market_caps": 0,
                      "reconciled_ttm_incomes": 0, "declined_ttm_incomes": 0,
                      "reconciled_hindsight_incomes": 0,
                      "declined_hindsight_incomes": 0}

    for basket in baskets:
        if basket not in basket_configs:
            manifest_errors.append(f"{basket}:not_a_configured_basket")
            continue
        events = _rows(
            conn, "SELECT rowid AS manifest_rowid, * FROM "
                  "basket_pe_backfill_runs WHERE basket = ? ORDER BY rowid",
            [basket])
        run_id = _latest_run(events)
        provenance_errors.extend(
            _refresh_provenance_errors(basket, events, run_id))
        started = next((row for row in events
                        if row["run_id"] == run_id
                        and row["event_kind"] == "run_started"), None)
        window = ((started["expected_from_date"], started["expected_to_date"])
                  if started else requested)
        observed_calendar = [row["date"] for row in _rows(
            conn, "SELECT date FROM daily_price WHERE symbol = ? "
                  "AND date BETWEEN ? AND ? ORDER BY date",
            [basket, window[0], window[1]])]
        earliest = conn.execute(
            "SELECT MIN(composition_available_date) AS first "
            "FROM fmp_fund_disclosure_holdings WHERE basket_symbol = ?",
            [basket]).fetchone()
        earliest_disclosure = (str(earliest["first"])
                               if earliest and earliest["first"] else None)
        manifest_errors.extend(_manifest_errors(
            basket, events, run_id, requested,
            weekly_calendar_hash(observed_calendar), observed_calendar,
            earliest_disclosure,
            basket_configs[basket].get("history_available_from")))
        raw_rows = _rows(
            conn, "SELECT * FROM basket_weekly_pe_history WHERE basket = ? "
                  "AND valuation_date BETWEEN ? AND ? ORDER BY valuation_date",
            [basket, window[0], window[1]])
        rows, decode_errors = _decode_rows(raw_rows)
        json_errors.extend(decode_errors)
        if run_id is None:
            # With no certifying run every row is trivially uncertified, and
            # listing them buries the reason under its own consequences. The
            # root cause is already reported by manifest_denominator.
            integrity_errors.append(
                f"{basket}:per_row_checks_suppressed_no_certifying_run")
        else:
            integrity_errors.extend(_result_integrity_errors(
                basket, conn, events, run_id, (window[0], window[1])))
        versions = sorted({str(row["methodology_version"]) for row in rows})
        if versions not in ([], [WEEKLY_METHODOLOGY_VERSION]):
            version_errors.append(f"{basket}:{versions}")
        expected_weeks = list(_payload(started).get("expected_weeks") or [])
        if run_id is None:
            denominator_errors.append(
                f"{basket}:denominator_checks_suppressed_no_certifying_run")
        else:
            denominator_errors.extend(
                _denominator_errors(basket, expected_weeks, rows))
            denominator_errors.extend(
                _uncertified_row_errors(basket, conn, events))
        tier_errors.extend(_tier_consistency_errors(rows))
        staleness = int(basket_configs[basket].get(
            "market_cap_staleness_days", DEFAULT_MARKET_CAP_STALENESS_DAYS))
        evidence_errors.extend(_evidence_errors(rows, staleness))
        evidence_errors.extend(_membership_errors(
            basket, rows, _disclosed_membership(conn, basket)))
        identity = _company_identity_check(
            basket, rows, _company_identities(conn, basket, issuer_overrides))
        identity_errors.extend(identity["errors"])
        for key in identity_totals:
            identity_totals[key] += identity[key]
        # Arithmetic over already-materialised members is cheap, so every row
        # gets it; only the raw-source rebuild, which re-reads whole symbol
        # histories, is sampled.
        kernel_errors.extend(_kernel_reconciliation_errors(rows))
        sampled = [rows[index] for index in _sample_indexes(len(rows), sample)]
        outcome = _raw_source_reconciliation(
            conn, sampled, share_config["conventions"], share_config["groups"],
            staleness)
        reconciliation["errors"].extend(outcome["errors"])
        for key in ("reconciled_market_caps", "reconciled_ttm_incomes",
                    "declined_ttm_incomes", "reconciled_hindsight_incomes",
                    "declined_hindsight_incomes"):
            reconciliation[key] += outcome[key]
        per_basket[basket] = {
            "manifest_run_id": run_id,
            "manifest_window": list(window),
            "expected_weeks": len(expected_weeks),
            "composition_floor": _payload(started).get("composition_floor"),
            "history_available_from":
                basket_configs[basket].get("history_available_from"),
            "rows": len(rows),
            "date_range": [rows[0]["valuation_date"], rows[-1]["valuation_date"]]
                          if rows else [],
            "published_ttm": sum(row["ttm_pe_gaap"] is not None for row in rows),
            "published_hindsight": sum(
                row["hindsight_ntm_pe_gaap"] is not None for row in rows),
            "quality_tiers": sorted({str(row["quality_tier"]) for row in rows}),
            "market_cap_staleness_days": staleness,
            "sampled": [row["valuation_date"] for row in sampled],
            "raw_source_reconciliation": outcome,
        }

    duplicates = _rows(
        conn, "SELECT basket, valuation_date, COUNT(*) n "
              "FROM basket_weekly_pe_history GROUP BY basket, valuation_date "
              "HAVING n > 1")

    checks.extend([
        _check("security_alias_bindings", not binding_errors, binding_errors),
        _check("manifest_denominator", not manifest_errors, manifest_errors),
        _check("forced_refresh_provenance", not provenance_errors,
               provenance_errors),
        _check("expected_weekly_denominator", not denominator_errors,
               denominator_errors),
        _check("manifest_result_integrity", not integrity_errors,
               integrity_errors),
        _check("quality_tier_and_gate_consistency", not tier_errors, tier_errors),
        _check("company_identity_uniqueness", not identity_errors,
               {**identity_totals, "errors": identity_errors}),
        _check("materialised_evidence", not evidence_errors, evidence_errors),
        _check("aggregate_reconciliation", not kernel_errors, kernel_errors),
        _check("raw_source_spot_check",
               not reconciliation["errors"]
               and (reconciliation["reconciled_market_caps"] > 0
                    or not any(item["rows"] for item in per_basket.values())),
               {key: value for key, value in reconciliation.items()}),
        _check("methodology_version", not version_errors,
               {"expected": WEEKLY_METHODOLOGY_VERSION,
                "errors": version_errors}),
        _check("duplicates_and_json", not duplicates and not json_errors,
               {"duplicate_pk": len(duplicates), "invalid_json": json_errors}),
    ])

    return {
        "as_of": as_of,
        "requested_window": list(requested),
        "baskets": per_basket,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = connect_readonly(args.db)
        # One read transaction for every check, so an active writer cannot make
        # two stages disagree about what the database contained.
        conn.execute("BEGIN")
        report = verify_database(conn, args.baskets, args.as_of, args.years,
                                 args.sample, args.config_dir)
        conn.rollback()
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True,
                         default=str))
        return 0 if report["passed"] else 1
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
