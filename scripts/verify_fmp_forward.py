"""FMP forward 数据层只读 verifier（fail-closed）。

用法:
    python scripts/verify_fmp_forward.py --stage data --snapshot-date YYYY-MM-DD
    python scripts/verify_fmp_forward.py --stage data --run-kind backfill --snapshot-date YYYY-MM-DD
    python scripts/verify_fmp_forward.py --stage full --snapshot-date YYYY-MM-DD
    python scripts/verify_fmp_forward.py --stage data --snapshot-date YYYY-MM-DD --json

原则:
- market.db 只以 mode=ro 打开，绝不创建/改写。
- 分母 SSOT 是 fmp_forward_runs 的不可变 manifest；绝不用当前池文件重建历史分母。
- missing 三分类（round-5）是纯报告拆分：denominator 与 90% gate 不受影响。
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.fmp_forward_ingestion import parse_forward_run_evidence

BASKETS = ("SPY", "QQQ", "SOX", "IGV", "XLF")
FULL_BASKETS = ("SPY", "QQQ", "SOX", "MAGS", "IGV", "XLF")
REQUIRED_FUTURE_QUARTERS = 4
RECENT_ACTUAL_WINDOW_DAYS = 120
CRITICAL_FAILURE_RATE = 0.20  # 镜像 writer 的 run-wide earnings gate（round-8）

# 只剥离显式类别后缀；绝不模糊合并无关公司
_CLASS_SUFFIX_RE = re.compile(r"\s+(?:CLASS|CL)\s+[A-Z]$", re.IGNORECASE)


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only verifier for the FMP forward data layer")
    parser.add_argument("--stage", choices=["data", "full"], default="data")
    parser.add_argument("--run-kind", choices=["weekly", "backfill"],
                        default="weekly")
    parser.add_argument("--snapshot-date", type=_iso_date, required=True)
    parser.add_argument("--min-quarter-coverage-pct", type=float, default=90.0)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_share_class_groups() -> Dict[str, List[str]]:
    path = PROJECT_ROOT / "config" / "baskets" / "share_class_groups.json"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _previous_completed_weekly_empty(conn, snapshot_date: str) -> Tuple[bool, set]:
    """上一个 complete weekly run 的 run_state.quarter_empty 集合。"""
    row = conn.execute(
        "SELECT summary_json FROM fmp_forward_runs "
        "WHERE run_kind = 'weekly' AND status = 'complete' "
        "AND snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1",
        [snapshot_date],
    ).fetchone()
    if not row:
        return False, set()
    try:
        state = parse_forward_run_evidence(row["summary_json"])
        return True, set(state["run_state"]["quarter_empty"])
    except ValueError:
        return True, set()


def verify_run(db_path: Path, data_root: Path, snapshot_date: str,
               run_kind: str = "weekly", stage: str = "data",
               min_quarter_coverage_pct: float = 90.0) -> Tuple[int, Dict]:
    report: Dict = {
        "ok": False,
        "snapshot_date": snapshot_date,
        "run_kind": run_kind,
        "universe": {"expected": 0, "covered_4q": 0, "pct": 0.0, "missing": [],
                     "known_structural_missing": [],
                     "structural_candidates": [],
                     "unexpected_missing": []},
        "holdings": {b: {} for b in BASKETS},
        "earnings": {"rows": 0, "matched": 0, "unmatched": 0,
                     "recent_actual": 0},
        "estimates": {"weekly_rows": 0, "backfill_rows": 0},
        "warnings": [],
        "failures": [],
    }
    failures = report["failures"]
    warnings = report["warnings"]

    db_path = Path(db_path)
    if not db_path.exists():
        failures.append(f"market.db not found: {db_path}")
        return 1, report

    conn = _connect_ro(db_path)
    try:
        # ---- Manifest（分母 SSOT，fail closed）----
        run = conn.execute(
            "SELECT * FROM fmp_forward_runs "
            "WHERE snapshot_date = ? AND run_kind = ?",
            [snapshot_date, run_kind],
        ).fetchone()
        if not run:
            failures.append(
                f"run manifest missing for ({snapshot_date}, {run_kind}); "
                "refusing to reconstruct a historical denominator")
            return 1, report
        try:
            universe = json.loads(run["target_universe_json"])
        except (TypeError, ValueError):
            failures.append("run manifest target_universe_json unparseable")
            return 1, report
        if not isinstance(universe, list) or not universe:
            failures.append("run manifest universe empty/invalid")
            return 1, report
        if len(universe) != run["target_count"]:
            failures.append(
                f"manifest target_count={run['target_count']} != "
                f"universe json length={len(universe)}")
            return 1, report

        # 状态裁决（round-8 P1）：failed/planned run 不可被独立 verifier 洗白；
        # 只有 running（编排器接线路径）与 complete（事后复核）可验
        if run["status"] not in ("running", "complete"):
            failures.append(
                f"manifest status={run['status']!r} is not verifiable; "
                "a failed/planned run cannot PASS verification")
            return 1, report

        # summary_json 是 run-wide 证据链；JSON 与 schema 共用 writer 的 SSOT。
        try:
            run_state = parse_forward_run_evidence(run["summary_json"])
        except ValueError as exc:
            failures.append(
                f"manifest summary_json invalid: {exc} — fail closed")
            return 1, report
        inner_state = run_state["run_state"]
        current_empty = set(inner_state["quarter_empty"])

        # 镜像 writer 的 run-wide earnings gate（round-8 P1）：unresolved
        # earnings 超阈值的 run 不允许 PASS
        unresolved_earnings = sorted(inner_state["earnings_failed"])
        earnings_unresolved_rate = (len(unresolved_earnings)
                                    / run["target_count"]
                                    if run["target_count"] else 1.0)
        if earnings_unresolved_rate > CRITICAL_FAILURE_RATE:
            failures.append(
                f"run-wide unresolved earnings failures "
                f"{len(unresolved_earnings)}/{run['target_count']} "
                f"({earnings_unresolved_rate:.0%}) > "
                f"{CRITICAL_FAILURE_RATE:.0%} — mirrors writer gate")

        # ---- 4Q coverage（只计本 kind、本 snapshot、未来季、非空 eps_avg）----
        rows = conn.execute(
            "SELECT symbol, COUNT(DISTINCT fiscal_date) AS n "
            "FROM fmp_estimates "
            "WHERE snapshot_date = ? AND snapshot_kind = ? "
            "AND period_type = 'Q' AND fiscal_date >= ? "
            "AND eps_avg IS NOT NULL GROUP BY symbol",
            [snapshot_date, run_kind, snapshot_date],
        ).fetchall()
        counts = {r["symbol"]: r["n"] for r in rows}
        universe_set = set(universe)
        covered = {s for s in universe_set
                   if counts.get(s, 0) >= REQUIRED_FUTURE_QUARTERS}
        missing = sorted(universe_set - covered)
        pct = round(100.0 * len(covered) / len(universe_set), 2)

        has_prev, prev_empty = _previous_completed_weekly_empty(
            conn, snapshot_date)
        known_structural = sorted(current_empty & prev_empty)
        candidates = sorted(current_empty - prev_empty)
        unexpected = sorted(set(missing) - current_empty)

        report["universe"] = {
            "expected": len(universe_set),
            "covered_4q": len(covered),
            "pct": pct,
            "missing": missing,
            "known_structural_missing": known_structural,
            "structural_candidates": candidates,
            "unexpected_missing": unexpected,
        }
        if pct < min_quarter_coverage_pct:
            failures.append(
                f"4Q coverage {pct}% < {min_quarter_coverage_pct}% "
                f"({len(covered)}/{len(universe_set)})")
        if has_prev and current_empty != prev_empty:
            warnings.append(
                "structural valid-empty set drift vs previous completed "
                f"weekly run: prev={sorted(prev_empty)} "
                f"now={sorted(current_empty)}")

        # ---- Holdings：5 篮子快照都必须有行 ----
        share_groups = _load_share_class_groups()
        grouped_pairs = set()
        for primary, secondaries in share_groups.items():
            for sec in secondaries:
                grouped_pairs.add(frozenset((primary, sec)))
        included_names: List[Tuple[str, str]] = []
        for basket in BASKETS:
            h_rows = conn.execute(
                "SELECT * FROM fmp_etf_holdings_snapshot "
                "WHERE basket = ? AND snapshot_date = ?",
                [basket, snapshot_date],
            ).fetchall()
            blank = sum(1 for r in h_rows if not (r["raw_asset"] or "").strip())
            reasons: Dict[str, int] = {}
            for r in h_rows:
                if r["filter_reason"]:
                    reasons[r["filter_reason"]] = reasons.get(
                        r["filter_reason"], 0) + 1
            report["holdings"][basket] = {
                "rows": len(h_rows),
                "included": sum(1 for r in h_rows if r["included"] == 1),
                "blank_assets": blank,
                "filter_reasons": reasons,
            }
            if not h_rows:
                failures.append(
                    f"holdings snapshot empty for basket {basket} "
                    f"@ {snapshot_date}")
            elif not any(r["included"] == 1 for r in h_rows):
                # 全 malformed payload 也会留下非零审计行；有效性看 included
                failures.append(
                    f"holdings snapshot for basket {basket} has zero valid "
                    f"included rows @ {snapshot_date}")
            for reason in ("foreign_listing_unmapped", "unrecognized_asset"):
                if reasons.get(reason):
                    warnings.append(
                        f"{basket}: {reasons[reason]} rows {reason} "
                        f"@ {snapshot_date}")
            for r in h_rows:
                if r["included"] == 1 and r["symbol"] and r["name"]:
                    included_names.append((r["symbol"], r["name"]))

        # included 撞名检测：只剥显式类别后缀，配置外的撞名 → 警示
        by_name: Dict[str, set] = {}
        for symbol, name in included_names:
            normalized = _CLASS_SUFFIX_RE.sub("", name.upper()).strip()
            by_name.setdefault(normalized, set()).add(symbol)
        for normalized, symbols in by_name.items():
            if len(symbols) < 2:
                continue
            pair_covered = all(
                any(frozenset((a, b)) <= group or frozenset((a, b)) == group
                    for group in grouped_pairs)
                for a in symbols for b in symbols if a < b)
            if not pair_covered:
                warnings.append(
                    f"issuer name collision among included rows not in "
                    f"share_class_groups: {sorted(symbols)} ({normalized})")

        # ---- Earnings 统计 ----
        e = conn.execute(
            "SELECT COUNT(*) AS rows, "
            "SUM(CASE WHEN match_method = 'estimates_window' THEN 1 ELSE 0 END)"
            " AS matched, "
            "SUM(CASE WHEN match_method = 'none' THEN 1 ELSE 0 END)"
            " AS unmatched, "
            "SUM(CASE WHEN eps_actual IS NOT NULL AND announce_date >= ? "
            "THEN 1 ELSE 0 END) AS recent_actual "
            "FROM fmp_earnings",
            [(date.fromisoformat(snapshot_date)
              - timedelta(days=RECENT_ACTUAL_WINDOW_DAYS)).isoformat()],
        ).fetchone()
        report["earnings"] = {
            "rows": e["rows"] or 0, "matched": e["matched"] or 0,
            "unmatched": e["unmatched"] or 0,
            "recent_actual": e["recent_actual"] or 0,
            "unresolved_run_wide": unresolved_earnings,
        }

        # ---- Estimates 体量 + backfill 深度 ----
        for kind_key, kind in (("weekly_rows", "weekly"),
                               ("backfill_rows", "backfill")):
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM fmp_estimates "
                "WHERE snapshot_date = ? AND snapshot_kind = ?",
                [snapshot_date, kind],
            ).fetchone()
            report["estimates"][kind_key] = row["n"] or 0
        if run_kind == "backfill":
            depth = conn.execute(
                "SELECT MIN(fiscal_date) AS lo, MAX(fiscal_date) AS hi, "
                "COUNT(*) AS n, COUNT(DISTINCT symbol) AS syms "
                "FROM fmp_estimates "
                "WHERE snapshot_date = ? AND snapshot_kind = 'backfill'",
                [snapshot_date],
            ).fetchone()
            report["estimates"]["min_fiscal_date"] = depth["lo"]
            report["estimates"]["max_fiscal_date"] = depth["hi"]
            report["estimates"]["rows_per_symbol"] = round(
                depth["n"] / depth["syms"], 1) if depth["syms"] else 0.0

        # ---- Stage full：Phase 2 契约（6 篮子 + 合法 JSON）----
        if stage == "full":
            b_rows = conn.execute(
                "SELECT basket, members_json FROM fmp_basket_valuation "
                "WHERE snapshot_date = ?",
                [snapshot_date],
            ).fetchall()
            present = {r["basket"] for r in b_rows}
            missing_baskets = sorted(set(FULL_BASKETS) - present)
            if missing_baskets:
                failures.append(
                    f"stage=full requires six basket valuation rows; "
                    f"missing: {missing_baskets}")
            for r in b_rows:
                try:
                    json.loads(r["members_json"] or "null")
                except ValueError:
                    failures.append(
                        f"basket {r['basket']} members_json invalid JSON")
    finally:
        conn.close()

    report["ok"] = not failures
    return (0 if report["ok"] else 1), report


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.data_root:
        db_path = args.data_root / "market.db"
        data_root = args.data_root
    else:
        from config.settings import MARKET_DB_PATH
        db_path = Path(MARKET_DB_PATH)
        data_root = db_path.parent
    rc, report = verify_run(db_path, data_root, args.snapshot_date,
                            run_kind=args.run_kind, stage=args.stage,
                            min_quarter_coverage_pct=args.min_quarter_coverage_pct)
    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        status = "PASS" if report["ok"] else "FAIL"
        uni = report["universe"]
        print(f"[{status}] {args.run_kind} {args.snapshot_date} "
              f"coverage {uni['covered_4q']}/{uni['expected']} ({uni['pct']}%)")
        for f_msg in report["failures"]:
            print(f"  FAIL: {f_msg}")
        for w in report["warnings"]:
            print(f"  WARN: {w}")
        if uni["missing"]:
            print(f"  missing (top 20): {uni['missing'][:20]}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
