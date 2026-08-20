"""Security master bootstrap CLI (R1) — breaks the resolver bootstrap deadlock.

`src/data/universe_resolver.py`'s `current_base_universe()` (T7) fail-louds
when `security_master` (SM) is empty. But SM itself is built by classifying
FMP profile payloads for a symbol list — and if that classification routed
through the resolver, nothing could ever bootstrap. This script breaks the
cycle: it builds SM directly from a RAW three-source symbol union, with zero
resolver involvement:

    1. current Extended raw list (`extended_universe_manager.get_extended_symbols`)
    2. any historical day's market_cap >= $10B in `historical_market_cap`
       (covers names that have since dropped out of the current pool)
    3. the delisted overlay (`delisted_universe_manager`) — covers names
       that have since been delisted entirely

R2-P1-1: sourcing (2)+(3) in addition to (1) is deliberate — a bootstrap
built only from today's Extended list would bake survivorship bias into
`approximate_members_as_of()`'s identity backstop (T3) from day one.

Ends by writing the FIRST `extended_membership` snapshot (R4-P1-1 cold
start: current raw list intersect eligible, as_of=today) so
`current_base_universe()` has a value the first time anything reads it.

Identity 状态契约 (R3-P1-2, frozen — shared verbatim by this bootstrap,
`entrant_bootstrap` (T17), and reconcile phase 0 (T12); do not change
without updating all three):

    event                 | SM write                 | coverage(identity) write
    --------------------- | ------------------------- | -------------------------
    network/HTTP failure  | none                       | fetch_failed + backoff
    200 + empty profile   | reason=missing_profile     | provider_empty + TTL
    identity conflict     | reason=identity_conflict   | identity_blocked
    resolved ok           | reason=ok (or a blocked    | ok
                           | value: etf/fund/...)       |

`missing_profile` is never a coverage(identity) status (SM-only value).

CLI:
    python scripts/bootstrap_security_master.py [--dry-run] [--limit N] [--current-only]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data import delisted_universe_manager  # noqa: E402
from src.data.extended_universe_manager import get_extended_symbols  # noqa: E402
from src.data.fmp_client import FMPClient  # noqa: E402
from src.data.market_store import MarketStore  # noqa: E402
from src.data.security_master import classify_security  # noqa: E402

SHARE_CLASS_OVERRIDES_PATH = PROJECT_ROOT / "config" / "share_class_overrides.json"
REPORT_PATH = PROJECT_ROOT / "data" / "extended_universe" / "bootstrap_report.json"
HISTORICAL_MCAP_THRESHOLD_USD = 1e10


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, obj: Any) -> None:
    """tmp 文件 + os.replace —— 报告写入不产生半写文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def _load_overrides(path: Optional[Path] = None) -> Dict[str, str]:
    p = path or SHARE_CLASS_OVERRIDES_PATH
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _historical_union_symbols(
    store: MarketStore, threshold_usd: float = HISTORICAL_MCAP_THRESHOLD_USD
) -> List[str]:
    """Source 2 (R2-P1-1): any historical day's market_cap >= threshold.

    Reads directly off the store's own connection (not injectable) per the
    brief's interface — this always reflects the live `store` passed to
    `run_bootstrap`, unlike `raw_loader`/`delisted_loader` which are
    externally-sourced symbol lists.
    """
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM historical_market_cap WHERE market_cap >= ?",
        (threshold_usd,),
    ).fetchall()
    return sorted(r[0] for r in rows)


def _write_company_profiles(
    store: MarketStore, profiles_by_symbol: Dict[str, dict], updated_at: str
) -> int:
    conn = store._get_conn()
    with conn:
        for symbol, profile in profiles_by_symbol.items():
            conn.execute(
                "INSERT OR REPLACE INTO company_profile (symbol, payload, updated_at) "
                "VALUES (?, ?, ?)",
                (symbol, json.dumps(profile, default=str), updated_at),
            )
    return len(profiles_by_symbol)


# ---------------------------------------------------------------------------
# Reusable per-symbol identity kernel. T17's `entrant_bootstrap` imports and
# reuses this UNCHANGED — keep it free of bootstrap-specific assumptions
# (no knowledge of the raw three-source union or of other symbols in a run).
# ---------------------------------------------------------------------------

def resolve_identity_for_symbol(
    symbol: str, *, client, store: MarketStore, dry_run: bool = False
) -> Dict[str, Any]:
    """Fetch one symbol's profile and apply the frozen Identity 状态契约.

    Writes `coverage(identity)` for every fetch outcome, and writes
    `security_master` immediately for the two outcomes that are fully
    single-symbol-decidable: no SM write on network failure; reason=
    missing_profile on a 200-empty profile. A 200 + non-empty profile is
    only *classified* here (`classify_security`), NOT written to SM yet —
    whether it ends up eligible/etf/fund/secondary_share_class/
    identity_conflict depends on cross-symbol CIK grouping
    (`resolve_share_classes`), which this function deliberately has no
    knowledge of. The caller collects every "ok" result, runs the grouping
    step once over the full batch, and performs the final SM write (plus
    the coverage override to identity_blocked for any conflict losers).

    Returns {"symbol", "fetch_status", "profile", "record"}:
      fetch_status ∈ {"fetch_failed", "provider_empty", "ok"}
      profile / record: only set when fetch_status == "ok".
    """
    now_iso = _now_iso()
    data, status = client.get_dataset_with_status("profile", symbol)

    if status == "fetch_failed":
        if not dry_run:
            store.upsert_coverage_status([{
                "symbol": symbol, "dataset": "identity", "status": "fetch_failed",
                "detail": "profile fetch failed", "updated_at": now_iso,
            }])
        return {"symbol": symbol, "fetch_status": "fetch_failed", "profile": None, "record": None}

    if status == "provider_empty":
        if not dry_run:
            store.upsert_coverage_status([{
                "symbol": symbol, "dataset": "identity", "status": "provider_empty",
                "detail": "profile 200 + empty payload", "updated_at": now_iso,
            }])
            store.upsert_security_master([{
                "symbol": symbol, "cik": None, "company_name": None, "exchange": None,
                "is_etf": 0, "is_fund": 0, "is_adr": 0, "share_class_of": None,
                "eligible": 0, "reason": "missing_profile", "updated_at": now_iso,
            }])
        return {"symbol": symbol, "fetch_status": "provider_empty", "profile": None, "record": None}

    # status == "ok"
    raw = data[0] if isinstance(data, list) and data else {}
    profile = dict(raw) if isinstance(raw, dict) else {}
    profile.setdefault("symbol", symbol)
    record = classify_security(profile)
    if not dry_run:
        store.upsert_coverage_status([{
            "symbol": symbol, "dataset": "identity", "status": "ok",
            "detail": None, "updated_at": now_iso,
        }])
    return {"symbol": symbol, "fetch_status": "ok", "profile": profile, "record": record}


def _segment_report(
    current: set, historical: set, delisted: set, reason_for: Dict[str, str]
) -> Dict[str, Any]:
    """Partition the raw union into 3 mutually exclusive segments, source
    precedence current > historical > delisted (a symbol qualifying via
    multiple sources is reported once, under its highest-precedence one).
    """
    historical_only = historical - current
    delisted_only = delisted - current - historical
    segments = {
        "current": sorted(current),
        "historical_only": sorted(historical_only),
        "delisted": sorted(delisted_only),
    }
    counts: Dict[str, Dict[str, int]] = {}
    for name, syms in segments.items():
        bucket: Dict[str, int] = {}
        for s in syms:
            r = reason_for.get(s, "fetch_failed")
            bucket[r] = bucket.get(r, 0) + 1
        counts[name] = bucket
    return {"segments": segments, "segment_reason_counts": counts}


def _print_report(report: Dict[str, Any]) -> None:
    print("bootstrap denominator report (as_of={}, current_only={}, dry_run={})".format(
        report.get("as_of"), report.get("current_only"), report.get("dry_run")))
    print("  union_total={} eligible={}".format(
        report.get("union_total"), report.get("eligible_total")))
    for name, counts in report.get("segment_reason_counts", {}).items():
        seg_size = len(report.get("segments", {}).get(name, []))
        counts_str = ", ".join("{}={}".format(k, v) for k, v in sorted(counts.items()))
        print("  [{}] n={} {}".format(name, seg_size, counts_str))
    if report.get("fetch_failed"):
        print("  fetch_failed (pending retry, not in SM): {}".format(len(report["fetch_failed"])))
    if report.get("needs_review"):
        preview = ", ".join(report["needs_review"][:20])
        print("  needs_review_primary (Boss override candidates): {} -> {}".format(
            len(report["needs_review"]), preview))
    if "membership_initialized" in report:
        print("  membership snapshot initialized: {} symbols".format(
            report["membership_initialized"]))
    print("  result={}".format(report.get("result")))


def run_bootstrap(
    *,
    store: MarketStore,
    client,
    raw_loader: Callable[[], List[str]],
    delisted_loader: Optional[Callable[[], List[str]]] = None,
    overrides: Optional[Dict[str, str]] = None,
    current_only: bool = False,
    limit: Optional[int] = None,
    dry_run: bool = False,
    as_of: Optional[str] = None,
    report_path: Optional[Path] = None,
) -> int:
    """Pure(ish)-function bootstrap core — every I/O dependency is injected
    so tests never touch the network or data/. The CLI wrapper (`main`)
    assembles real dependencies (live FMPClient, live MarketStore,
    `extended_universe_manager.get_extended_symbols`,
    `delisted_universe_manager.get_delisted_large_cap_symbols`).

    Returns 0 on success, 2 on either fail-loud condition (raw union empty,
    or zero eligible securities after classification).
    """
    overrides = overrides or {}
    as_of = as_of or date.today().isoformat()
    # report_path stays None unless the caller explicitly wants a file written
    # (the CLI wrapper passes REPORT_PATH) — a pure-function default must
    # never reach into the real repo's data/ directory (tests never pass
    # this, and must never touch real paths as a side effect).

    current_symbols = {s.upper() for s in raw_loader()}

    if current_only:
        historical_symbols: set = set()
        delisted_symbols: set = set()
    else:
        historical_symbols = set(_historical_union_symbols(store))
        delisted_symbols = {s.upper() for s in (delisted_loader() if delisted_loader else [])}

    union_symbols = sorted(current_symbols | historical_symbols | delisted_symbols)

    if not union_symbols:
        print("bootstrap: raw three-source union is EMPTY — refusing to build "
              "an empty security_master denominator (exit 2)")
        return 2

    if limit is not None:
        union_symbols = union_symbols[:limit]

    ok_results: List[Dict[str, Any]] = []
    fetch_failed_symbols: List[str] = []
    provider_empty_symbols: List[str] = []
    profiles_by_symbol: Dict[str, dict] = {}

    for symbol in union_symbols:
        outcome = resolve_identity_for_symbol(symbol, client=client, store=store, dry_run=dry_run)
        if outcome["fetch_status"] == "fetch_failed":
            fetch_failed_symbols.append(symbol)
        elif outcome["fetch_status"] == "provider_empty":
            provider_empty_symbols.append(symbol)
        else:
            ok_results.append(outcome)
            profiles_by_symbol[symbol] = outcome["profile"]

    records = [r["record"] for r in ok_results]
    # A rerun can fetch only one member of an existing share-class group. Close
    # each candidate CIK over current SM incumbents before deciding a primary;
    # otherwise a partial retry can create two eligible primaries.
    from src.data.entrant_bootstrap import resolve_share_classes_with_incumbents
    resolved_records, _ = resolve_share_classes_with_incumbents(
        records, profiles_by_symbol, store=store, overrides=overrides)

    reason_for: Dict[str, str] = {s: "missing_profile" for s in provider_empty_symbols}

    if not dry_run:
        sm_rows = [{**asdict(rec), "updated_at": _now_iso()} for rec in resolved_records]
        if sm_rows:
            store.upsert_security_master(sm_rows)

        conflict_symbols = [rec.symbol for rec in resolved_records if rec.reason == "identity_conflict"]
        if conflict_symbols:
            now_iso = _now_iso()
            store.upsert_coverage_status([
                {"symbol": s, "dataset": "identity", "status": "identity_blocked",
                 "detail": "same CIK, different company name across share classes",
                 "updated_at": now_iso}
                for s in conflict_symbols
            ])

        if profiles_by_symbol:
            _write_company_profiles(store, profiles_by_symbol, _now_iso())

    for rec in resolved_records:
        reason_for[rec.symbol] = rec.reason

    report = _segment_report(current_symbols, historical_symbols, delisted_symbols, reason_for)
    report["as_of"] = as_of
    report["dry_run"] = dry_run
    report["current_only"] = current_only
    report["union_total"] = len(union_symbols)
    report["fetch_failed"] = sorted(fetch_failed_symbols)

    if dry_run:
        needs_review = sorted(rec.symbol for rec in resolved_records
                              if rec.reason == "needs_review_primary")
        eligible_count = sum(1 for rec in resolved_records if rec.eligible)
    else:
        try:
            eligibility = store.get_security_eligibility()
        except RuntimeError:
            eligibility = {}
        needs_review = store.get_needs_review_symbols()
        eligible_count = sum(1 for v in eligibility.values() if v)

    report["needs_review"] = needs_review
    report["eligible_total"] = eligible_count

    if eligible_count == 0:
        report["result"] = "FAIL_EMPTY_ELIGIBLE"
        _print_report(report)
        if report_path is not None:
            _atomic_write_json(report_path, report)
        print("bootstrap: zero eligible securities after classification — refusing to "
              "publish an empty denominator (exit 2)")
        return 2

    if not dry_run:
        eligible_symbols = {s for s, v in eligibility.items() if v}
        membership_symbols = sorted(current_symbols & eligible_symbols)
        snapshot = store.record_membership_snapshot(membership_symbols, as_of=as_of)
        report["membership_initialized"] = len(membership_symbols)
        report["membership_entered"] = snapshot["entered"]
        report["membership_exited"] = snapshot["exited"]

    report["result"] = "OK"
    _print_report(report)
    if report_path is not None:
        _atomic_write_json(report_path, report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Security master bootstrap — breaks the resolver bootstrap deadlock "
                     "(raw three-source union -> profile -> classify -> SM, no resolver)."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + classify but write nothing to the store.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the first N symbols of the union (smoke testing).")
    parser.add_argument("--current-only", action="store_true",
                        help="Skip the historical/delisted sources — source 1 only "
                             "(weekly incremental reuse).")
    args = parser.parse_args()

    store = MarketStore()
    client = FMPClient()
    overrides = _load_overrides()

    rc = run_bootstrap(
        store=store,
        client=client,
        raw_loader=get_extended_symbols,
        delisted_loader=delisted_universe_manager.get_delisted_large_cap_symbols,
        overrides=overrides,
        current_only=args.current_only,
        limit=args.limit,
        dry_run=args.dry_run,
        report_path=REPORT_PATH,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
