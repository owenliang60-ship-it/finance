"""Fundamental coverage verifier (T18, R9) — Stop F acceptance gate.

Four independently-thresholded metrics, each with an EXPLICIT denominator
(MF-4) rather than "however many rows we happen to have collected":

  1. Three-table coverage  >= 95%  numerator: symbol has >=1 row in ALL of
     income_quarterly / balance_sheet_quarterly / cash_flow_quarterly.
     denominator: current_base_universe() (T7, R3-P1-1).
  2. 8Q continuity          >= 95%  numerator: `has_asof_window` (T10's
     backfill runner) returns True — the last 8 fiscal quarters have no gap
     wider than FUNDAMENTAL_QUARTER_GAP_MAX_DAYS across all three tables.
     denominator: current_base_universe().
     `has_asof_window` and its gap constant are IMPORTED, not reimplemented —
     its own docstring already declares it the SSOT "shared with T18's
     continuity check" (config/settings.py comment, same wording).
  3. Profile coverage       >= 98%  numerator: company_profile row exists.
     denominator: current_base_universe().
  4. Forward coverage       >= 95%  numerator (D2): the latest weekly
     fmp_estimates snapshot has >=4 distinct future fiscal quarters with a
     non-null eps_avg (consensus EPS). denominator (D1): "confirmed analyst
     coverage" — see `_forward_d1` below for the frozen classification order.

Every symbol missing from a metric's numerator must carry an explicit
attribution:
  - Metrics 1-3: a coverage_status six-state status (ok / not_applicable /
    provider_empty / fetch_failed / stale / identity_blocked, per
    MarketStore._COVERAGE_STATUSES) for at least one of the metric's
    relevant dataset tables. A missing symbol with NO coverage_status row at
    all for any relevant table is an unexplained gap — the verifier itself
    FAILs on this (a condition distinct from, and independent of, "metric
    percentage below threshold"; see `unattributed_gaps` in the report).
  - Metric 4: the D1/D2 classification IS the attribution (`fetch_failed`
    this run vs. `insufficient_quarters` on an otherwise-successful fetch).
    coverage_status does not track the forward/fmp_estimates domain at all
    (its dataset whitelist is the T8-kernel tables — income_quarterly /
    balance_sheet_quarterly / cash_flow_quarterly / company_profile /
    identity — never fmp_estimates), so there is nothing to look up there;
    every D1 member's classification is already known by construction.

Forward classification order (frozen, R3-m3 + R4-P2-3) — Step 0 recon:
`scripts/update_fmp_forward.py`'s `ForwardRunSummary` distinguishes
`quarter_failed` (the endpoint call raised, returned None, or normalized to
zero rows for a non-empty payload — `fmp_client.py`'s `"fetch_failed"`
transport-failure status) from `quarter_empty` (endpoint call succeeded and
legitimately returned nothing — commented in that module as "valid []，非传输
错误", i.e. NOT a failure). Only `quarter_failed` is this verifier's failure
bucket. Each attempt dict inside `fmp_forward_runs.summary_json["attempts"]`
carries its own `quarter_failed` list (`_attempt_detail`,
market_store.py:1132 area / update_fmp_forward.py `_build_run_evidence`);
unlike `quarter_empty` and `earnings_failed`, `quarter_failed` is NOT merged
into the cumulative `run_state` across resume attempts. This verifier
therefore unions `quarter_failed` across every attempt recorded on the
latest `status='complete'` weekly manifest row — conservative on purpose: a
symbol that later succeeds is independently picked up via "ever had a row"
in `_forward_d1`, so lingering in the bucket too long is harmless, while
dropping a symbol that is still genuinely failing (because a later resume
attempt didn't happen to re-target it) is the actual risk this guards
against.

  ① fetch_failed bucket (from the manifest) is classified FIRST: those
    symbols are forced into D1 (the denominator) — a collection outage must
    never masquerade as "confirmed no analyst coverage" — but they are never
    counted as covered by D2 for the current period (they simply have no
    fresh snapshot row to match).
  ② ONLY THEN are symbols with zero fmp_estimates rows EVER (any
    snapshot_date, any kind) classified `not_applicable` and excluded from
    D1 entirely. This ordering is what breaks the self-certification loop
    (MF-4): without it, "denominator = whatever we successfully fetched"
    would let a persistent fetch outage silently shrink the denominator
    instead of showing up as a miss.

Read-only. Zero writes: a plain `MarketStore()` handle, enforced by
discipline (only getter methods and `has_asof_window`/`current_base_universe`
are ever called) rather than by a DB-level read-only connection — matching
`scripts/reconcile_fundamentals.py`'s report-only path and
`scripts/verify_broad_data.py`; `current_base_universe`/`has_asof_window`/
`store.get_coverage` all require a `MarketStore` instance, not a raw
`sqlite3` connection, so verify_fmp_forward.py's stricter `mode=ro` URI
approach isn't available here without reimplementing those helpers.

CLI:
    python scripts/verify_fundamental_coverage.py [--json]

Exit codes: 0 all four metrics pass threshold AND every missing symbol is
attributed; 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS  # noqa: E402
from scripts.backfill_extended_fundamentals import (  # noqa: E402
    ASOF_WINDOW_TABLES,
    DEFAULT_ASOF_QUARTERS,
    has_asof_window,
)
from src.data.fmp_forward_ingestion import parse_forward_run_evidence  # noqa: E402
from src.data.market_store import MarketStore  # noqa: E402
from src.data.universe_resolver import current_base_universe  # noqa: E402

# The three T8-kernel current statement tables. Reused verbatim from the
# backfill runner (never redefined) so a dataset->table rename cannot desync
# the two checks that must agree on what "8Q continuity" even means.
STATEMENT_TABLES = ASOF_WINDOW_TABLES
PROFILE_TABLE = "company_profile"

THREE_TABLE_THRESHOLD_PCT = 95.0
CONTINUITY_THRESHOLD_PCT = 95.0
PROFILE_THRESHOLD_PCT = 98.0
FORWARD_THRESHOLD_PCT = 95.0

FORWARD_D1_FRESH_WINDOW_DAYS = 180
FORWARD_D2_MIN_FUTURE_QUARTERS = 4  # mirrors verify_fmp_forward.py's REQUIRED_FUTURE_QUARTERS


# ---------------------------------------------------------------------------
# Small read-only helpers
# ---------------------------------------------------------------------------

def _symbols_with_rows(conn, table: str) -> Set[str]:
    # `table` is always one of the fixed constants above, never external
    # input — same posture as data_health.py's _check_extended_coverage.
    rows = conn.execute(f"SELECT DISTINCT symbol FROM {table}").fetchall()
    return {r[0] for r in rows}


def _pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(100.0 * numerator / denominator, 2)


# ---------------------------------------------------------------------------
# Forward metric (D1/D2, frozen classification order)
# ---------------------------------------------------------------------------

def _forward_fetch_failed_bucket(conn) -> Tuple[Set[str], List[str]]:
    """Union of `quarter_failed` across every attempt in the latest
    status=complete weekly fmp_forward_runs manifest row.

    Returns (bucket, structural_failures). An empty bucket with no
    structural_failures is a legitimate outcome (no complete weekly run has
    ever landed yet, e.g. before the forward pipeline's first successful
    week) — that is not itself a verifier defect, it just means D1 gets no
    help from rule ① this run. A present-but-unparseable manifest row IS a
    defect: fail closed (mirrors verify_fmp_forward.py's own posture) rather
    than silently treating it as "nobody failed".
    """
    row = conn.execute(
        "SELECT summary_json FROM fmp_forward_runs "
        "WHERE run_kind = 'weekly' AND status = 'complete' "
        "ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return set(), []
    try:
        payload = parse_forward_run_evidence(row[0])
    except ValueError as exc:
        return set(), [
            f"latest complete weekly fmp_forward_runs manifest summary_json "
            f"invalid: {exc} — forward D1 failure bucket cannot be trusted"
        ]
    bucket: Set[str] = set()
    for attempt in payload.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        quarter_failed = attempt.get("quarter_failed")
        if isinstance(quarter_failed, list):
            bucket.update(s for s in quarter_failed if isinstance(s, str))
    return bucket, []


def _forward_d1(conn, universe_set: Set[str], fetch_failed_bucket: Set[str],
                today: date) -> Tuple[Set[str], Set[str]]:
    """Returns (d1_set, not_applicable_set) — frozen order ① then ②."""
    ever_rows = conn.execute("SELECT DISTINCT symbol FROM fmp_estimates").fetchall()
    ever_covered = {r[0] for r in ever_rows}

    window_start = (today - timedelta(days=FORWARD_D1_FRESH_WINDOW_DAYS)).isoformat()
    fresh_rows = conn.execute(
        "SELECT DISTINCT symbol FROM fmp_estimates "
        "WHERE eps_avg IS NOT NULL AND period_type = 'Q' AND snapshot_date >= ?",
        (window_start,),
    ).fetchall()
    fresh = {r[0] for r in fresh_rows}

    # ① fetch_failed forces D1 inclusion regardless of the literal 180-day
    # freshness test — a collection outage is inconclusive, not "no coverage".
    d1 = {s for s in universe_set if s in fetch_failed_bucket or s in fresh}
    # ② only symbols NOT already in D1 (i.e. never rescued by rule ①) and
    # that have literally never had a row are "no analyst coverage".
    not_applicable = {s for s in universe_set - d1 if s not in ever_covered}
    return d1, not_applicable


def _forward_d2(conn, d1: Set[str]) -> Tuple[Set[str], Optional[str]]:
    """Returns (covered_set, latest_weekly_snapshot_date)."""
    row = conn.execute(
        "SELECT MAX(snapshot_date) FROM fmp_estimates WHERE snapshot_kind = 'weekly'"
    ).fetchone()
    latest = row[0] if row else None
    if not latest:
        return set(), None
    rows = conn.execute(
        "SELECT symbol, COUNT(DISTINCT fiscal_date) AS n FROM fmp_estimates "
        "WHERE snapshot_date = ? AND snapshot_kind = 'weekly' AND period_type = 'Q' "
        "AND fiscal_date >= ? AND eps_avg IS NOT NULL GROUP BY symbol",
        (latest, latest),
    ).fetchall()
    counts = {r[0]: r[1] for r in rows}
    covered = {s for s in d1 if counts.get(s, 0) >= FORWARD_D2_MIN_FUTURE_QUARTERS}
    return covered, latest


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def verify(store: MarketStore, *,
          today_fn: Callable[[], date] = date.today) -> Tuple[int, Dict[str, Any]]:
    """Pure(ish)-function core: all I/O is through `store`, `today_fn` is
    injected for deterministic tests. Returns (exit_code, report)."""
    today = today_fn()
    as_of = today.isoformat()

    try:
        universe = current_base_universe(store)
    except RuntimeError as exc:
        return 1, {
            "ok": False, "as_of": as_of, "denominator": 0,
            "metrics": {}, "unattributed_gaps": [], "failures": [str(exc)],
        }

    conn = store._get_conn()
    universe_set = set(universe)
    denominator = len(universe_set)

    coverage_by_table = {t: store.get_coverage(t) for t in STATEMENT_TABLES}
    coverage_profile = store.get_coverage(PROFILE_TABLE)
    per_table_symbols = {t: _symbols_with_rows(conn, t) for t in STATEMENT_TABLES}
    profile_symbols = _symbols_with_rows(conn, PROFILE_TABLE)

    failures: List[str] = []
    unattributed: List[Dict[str, str]] = []
    metrics: Dict[str, Any] = {}

    # ---- Metric 1: three-table coverage ----
    covered_3t = set(universe_set)
    for t in STATEMENT_TABLES:
        covered_3t &= per_table_symbols[t]
    missing_3t = sorted(universe_set - covered_3t)
    missing_3t_detail = []
    for sym in missing_3t:
        missing_tables = [t for t in STATEMENT_TABLES if sym not in per_table_symbols[t]]
        attribution = {t: coverage_by_table[t].get(sym) for t in missing_tables}
        if all(v is None for v in attribution.values()):
            unattributed.append({"metric": "three_table", "symbol": sym})
        missing_3t_detail.append({
            "symbol": sym, "missing_tables": missing_tables, "attribution": attribution,
        })
    pct_3t = _pct(len(covered_3t), denominator)
    ok_3t = denominator > 0 and pct_3t >= THREE_TABLE_THRESHOLD_PCT
    metrics["three_table"] = {
        "covered": len(covered_3t), "denominator": denominator, "pct": pct_3t,
        "threshold": THREE_TABLE_THRESHOLD_PCT, "ok": ok_3t, "missing": missing_3t_detail,
    }
    if not ok_3t:
        failures.append(
            f"three-table coverage {pct_3t}% < {THREE_TABLE_THRESHOLD_PCT}% "
            f"({len(covered_3t)}/{denominator})")

    # ---- Metric 2: 8Q continuity ----
    covered_8q = {
        s for s in universe_set
        if has_asof_window(store, s, as_of, quarters=DEFAULT_ASOF_QUARTERS)
    }
    missing_8q = sorted(universe_set - covered_8q)
    missing_8q_detail = []
    for sym in missing_8q:
        attribution = {t: coverage_by_table[t].get(sym) for t in STATEMENT_TABLES}
        if all(v is None for v in attribution.values()):
            unattributed.append({"metric": "continuity_8q", "symbol": sym})
        missing_8q_detail.append({"symbol": sym, "attribution": attribution})
    pct_8q = _pct(len(covered_8q), denominator)
    ok_8q = denominator > 0 and pct_8q >= CONTINUITY_THRESHOLD_PCT
    metrics["continuity_8q"] = {
        "covered": len(covered_8q), "denominator": denominator, "pct": pct_8q,
        "threshold": CONTINUITY_THRESHOLD_PCT, "ok": ok_8q,
        "gap_max_days": FUNDAMENTAL_QUARTER_GAP_MAX_DAYS,
        "quarters_required": DEFAULT_ASOF_QUARTERS,
        "missing": missing_8q_detail,
    }
    if not ok_8q:
        failures.append(
            f"8Q continuity {pct_8q}% < {CONTINUITY_THRESHOLD_PCT}% "
            f"({len(covered_8q)}/{denominator})")

    # ---- Metric 3: profile coverage ----
    covered_profile = universe_set & profile_symbols
    missing_profile = sorted(universe_set - covered_profile)
    missing_profile_detail = []
    for sym in missing_profile:
        status = coverage_profile.get(sym)
        if status is None:
            unattributed.append({"metric": "profile", "symbol": sym})
        missing_profile_detail.append({"symbol": sym, "attribution": {PROFILE_TABLE: status}})
    pct_profile = _pct(len(covered_profile), denominator)
    ok_profile = denominator > 0 and pct_profile >= PROFILE_THRESHOLD_PCT
    metrics["profile"] = {
        "covered": len(covered_profile), "denominator": denominator, "pct": pct_profile,
        "threshold": PROFILE_THRESHOLD_PCT, "ok": ok_profile, "missing": missing_profile_detail,
    }
    if not ok_profile:
        failures.append(
            f"profile coverage {pct_profile}% < {PROFILE_THRESHOLD_PCT}% "
            f"({len(covered_profile)}/{denominator})")

    # ---- Metric 4: forward coverage (D2/D1) ----
    fetch_failed_bucket, bucket_failures = _forward_fetch_failed_bucket(conn)
    failures.extend(bucket_failures)
    d1, not_applicable = _forward_d1(conn, universe_set, fetch_failed_bucket, today)
    covered_d2, latest_snapshot = _forward_d2(conn, d1)
    excluded_other = sorted(universe_set - d1 - not_applicable)
    missing_forward = sorted(d1 - covered_d2)
    missing_forward_detail = [
        {"symbol": sym,
         "reason": "fetch_failed" if sym in fetch_failed_bucket else "insufficient_quarters"}
        for sym in missing_forward
    ]
    d1_n = len(d1)
    pct_forward = _pct(len(covered_d2), d1_n)
    ok_forward = d1_n > 0 and pct_forward >= FORWARD_THRESHOLD_PCT and not bucket_failures
    metrics["forward"] = {
        "d1": d1_n, "d2": len(covered_d2), "pct": pct_forward,
        "threshold": FORWARD_THRESHOLD_PCT, "ok": ok_forward,
        "latest_weekly_snapshot": latest_snapshot,
        "not_applicable": sorted(not_applicable),
        "fetch_failed_bucket": sorted(fetch_failed_bucket & universe_set),
        "excluded_other": excluded_other,
        "missing": missing_forward_detail,
    }
    if not bucket_failures and not ok_forward:
        failures.append(
            f"forward coverage {pct_forward}% < {FORWARD_THRESHOLD_PCT}% "
            f"(D2={len(covered_d2)}/D1={d1_n})")

    if unattributed:
        failures.append(
            f"{len(unattributed)} missing symbol(s) have no coverage_status "
            f"attribution — refusing to pass silently: {unattributed[:10]}")

    ok = not failures
    report = {
        "ok": ok, "as_of": as_of, "denominator": denominator,
        "metrics": metrics, "unattributed_gaps": unattributed, "failures": failures,
    }
    return (0 if ok else 1), report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stop F acceptance gate (R9): four-metric fundamental "
                    "coverage verifier with explicit denominators. Read-only.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit the full report as JSON instead of the human summary.")
    return parser.parse_args(argv)


def _print_report(report: Dict[str, Any]) -> None:
    status = "PASS" if report["ok"] else "FAIL"
    print(f"[{status}] fundamental coverage @ {report['as_of']} "
          f"(base universe = {report['denominator']})")
    for name, m in report.get("metrics", {}).items():
        marker = "OK" if m.get("ok") else "FAIL"
        if name == "forward":
            print(f"  [{marker}] {name}: D2/D1 {m['d2']}/{m['d1']} "
                  f"({m['pct']}%, threshold {m['threshold']}%) "
                  f"not_applicable={len(m['not_applicable'])} "
                  f"fetch_failed={len(m['fetch_failed_bucket'])}")
        else:
            print(f"  [{marker}] {name}: {m['covered']}/{m['denominator']} "
                  f"({m['pct']}%, threshold {m['threshold']}%)")
        if m.get("missing"):
            names = [x["symbol"] for x in m["missing"]]
            print(f"      missing (top 20): {names[:20]}")
    if report.get("unattributed_gaps"):
        print(f"  UNATTRIBUTED GAPS: {report['unattributed_gaps'][:20]}")
    for f_msg in report.get("failures", []):
        print(f"  FAIL: {f_msg}")


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    store = MarketStore()
    try:
        rc, report = verify(store)
    finally:
        store.close()
    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_report(report)
    return rc


if __name__ == "__main__":
    sys.exit(main())
