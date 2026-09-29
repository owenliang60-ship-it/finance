"""Prosperity D9 history: frozen backfill targets + read-only coverage report.

Subcommands:
    targets  — union of the as-of $10B+ members at every quarter end → JSON,
               the frozen `--targets-file` for the backfill runners.
    report   — per quarter end: three-table window pass rate (gate ≥90%),
               street EPS SUE computability, mapping sources, gap reasons
               (inherent vs fixable), duplicate fiscals, split suspects and
               the day-60 / 95% / day-80 freeze-parameter recheck.

Read-only: opens market.db with `MarketStore(read_only=True)`; writes only
the output files it is told to.

CLI:
    python scripts/verify_prosperity_history.py targets \\
        [--start 2021-09-30] [--end 2026-06-30] [--out data/prosperity/d9_targets.json]
    python scripts/verify_prosperity_history.py report --targets F \\
        [--run-id ID ...] [--out-dir reports/prosperity] [--eps-targets-out F]

Exit codes (report): 0 every quarter end passes the three-table gate, else 1.
Street EPS has no numeric gate yet (Boss decides after reading the report).
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import tempfile
from contextlib import contextmanager
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_history import (  # noqa: E402
    INHERENT_GAP_REASONS,
    SUE_MIN_QUARTERS,
    arrival_day,
    eps_gap_reason,
    freeze_coverage,
    gap_reason,
    members_by_quarter_end,
    quarter_ends,
    street_eps_depth,
    three_table_ok,
)
from scripts.backfill_extended_fundamentals import (  # noqa: E402
    ASOF_WINDOW_TABLES,
    DAYS_PER_QUARTER,
    DEFAULT_ASOF_QUARTERS,
    STATEMENT_DATASETS,
)
from src.data.prosperity_quality import statement_availability, statement_known_on
from scripts.backfill_extended_fundamentals import has_contiguous_window
from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS  # noqa: E402

DEFAULT_TARGETS_PATH = PROJECT_ROOT / "data" / "prosperity" / "d9_targets.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "prosperity"
THREE_TABLE_GATE = 0.90
# Best outcome across runs wins per dataset; the worst dataset stands for the symbol.
JOB_RANK = {"done": 0, "provider_empty": 1, "skipped": 1, "fetch_failed": 2,
            "in_progress": 3, "pending": 3}


def _open_store() -> MarketStore:
    return MarketStore(read_only=True)


def _code_sha() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def cmd_targets(args) -> int:
    qes = quarter_ends(args.start, args.end)
    store = _open_store()
    by_qe = members_by_quarter_end(store, qes)
    union = sorted({s for members in by_qe.values() for s in members})
    doc = {
        "symbols": union,
        "quarter_ends": qes,
        "by_quarter_end": by_qe,
        "source": "approximate_members_as_of (10d mcap freshness + symbol_aliases)",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_sha": _code_sha(),
    }
    _write_json(Path(args.out), doc)
    sizes = [len(v) for v in by_qe.values()]
    print("targets: {} symbols over {} quarter ends (per-quarter {}..{}) -> {}".format(
        len(union), len(qes), min(sizes), max(sizes), args.out))
    return 0 if union else 2


class _SymbolData:
    """Everything the report reads for one symbol, loaded once."""

    def __init__(self, store: MarketStore, symbol: str, observed_at=None):
        self.observed_at = observed_at
        self._public_dates = {}
        conn = store._get_conn()
        self.tables = {
            t: [dict(r) for r in conn.execute(
                "SELECT * FROM " + t
                + " WHERE symbol = ? ORDER BY date", (symbol,)).fetchall()]
            for t in ASOF_WINDOW_TABLES}
        self.income = self.tables[ASOF_WINDOW_TABLES[0]]
        self.earnings = store.get_fmp_earnings(symbol)
        self.splits = store.get_stock_splits(symbol)
        self.listed_after, self.listed_by = self._listing_evidence(conn, symbol)

    @staticmethod
    def _listing_evidence(conn, symbol: str):
        """(listed_after, listed_by): known lower / upper bounds on listing.

        Only the profile ipoDate proves "not listed before" — and not when
        market-cap history predates it (then the ipoDate is wrong). A first
        market-cap row only proves "listed by then" (vendor coverage can start
        late), so it is an upper bound, never a lower one.
        """
        row = conn.execute("SELECT payload FROM company_profile WHERE symbol = ?",
                           (symbol,)).fetchone()
        ipo = None
        if row:
            try:
                ipo = (json.loads(row[0]).get("ipoDate") or "")[:10] or None
            except (TypeError, ValueError):
                ipo = None
        first = conn.execute("SELECT MIN(date) FROM historical_market_cap WHERE symbol = ?",
                             (symbol,)).fetchone()[0]
        listed_by = min((v for v in (ipo, first) if v), default=None)
        listed_after = ipo if ipo and (not first or first >= ipo) else None
        return listed_after, listed_by

    def public_availability(self, row):
        key = (row["date"], row.get("accepted_date"), row.get("filing_date"))
        if key not in self._public_dates:
            self._public_dates[key] = statement_availability(row, earnings_rows=self.earnings)
        return self._public_dates[key]

    def known_dates(self, rows, as_of: str) -> List[str]:
        out = []
        for r in rows:
            known = (statement_known_on(r, observed_at=self.observed_at) if self.observed_at
                     else self.public_availability(r)["public_available_at"])
            if r["date"][:10] <= as_of and known and known <= as_of:
                out.append(r["date"][:10])
        return out

    def any_table_known(self, as_of: str) -> List[str]:
        return sorted({d for rows in self.tables.values()
                       for d in self.known_dates(rows, as_of)})

    def current_fiscal(self, as_of: str) -> Optional[str]:
        """Newest fiscal quarter all three statements had reached by as_of."""
        common = set.intersection(*(set(self.known_dates(rows, as_of))
                                    for rows in self.tables.values()))
        return max(common) if common else None


def _job_status(store: MarketStore, run_ids: List[str]) -> Dict[str, str]:
    """symbol -> worst dataset status (best across runs per dataset)."""
    if not run_ids:
        return {}
    conn = store._get_conn()
    best: Dict[tuple, str] = {}
    marks = ",".join("?" * len(run_ids))
    for r in conn.execute("SELECT symbol, dataset, status FROM fundamental_backfill_jobs "
                          "WHERE run_id IN (" + marks + ")", run_ids).fetchall():
        if r["dataset"] not in STATEMENT_DATASETS:
            continue
        key = (r["symbol"], r["dataset"])
        if key not in best or JOB_RANK[r["status"]] < JOB_RANK[best[key]]:
            best[key] = r["status"]
    worst: Dict[str, str] = {}
    for (sym, _), status in best.items():
        if sym not in worst or JOB_RANK[status] > JOB_RANK[worst[sym]]:
            worst[sym] = status
    return worst


def build_report(store: MarketStore, targets: Dict[str, Any],
                 run_ids: List[str], *, observed_at: Optional[str] = None) -> Dict[str, Any]:
    by_qe = targets["by_quarter_end"]
    if observed_at and any(q < observed_at for q in by_qe):
        raise ValueError("as_of precedes snapshot observation")
    jobs = _job_status(store, run_ids)
    cache: Dict[str, _SymbolData] = {}
    quarters, gaps3, gaps_eps, quality = [], [], [], []
    dup_fiscal: Dict[str, List[str]] = {}
    splits_out: Dict[tuple, Dict[str, Any]] = {}
    window_span = DEFAULT_ASOF_QUARTERS * DAYS_PER_QUARTER + FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
    eps_span = SUE_MIN_QUARTERS * DAYS_PER_QUARTER + FUNDAMENTAL_QUARTER_GAP_MAX_DAYS

    for qe in sorted(by_qe):
        members = by_qe[qe]
        window_start = (date.fromisoformat(qe) - timedelta(days=window_span)).isoformat()
        eps_start = (date.fromisoformat(qe) - timedelta(days=eps_span)).isoformat()
        ok3 = raw_ok3 = joint = depth_ok = depth_full = sue = dsue = 0
        mapping: Dict[str, int] = {}
        reasons3: Dict[str, int] = {}
        reasons_eps: Dict[str, int] = {}
        arrivals = []
        for sym in members:
            if sym not in cache:
                cache[sym] = _SymbolData(store, sym, observed_at)
            data = cache[sym]
            raw_ok3 += three_table_ok(store, sym, qe)
            table_ok = all(has_contiguous_window(data.known_dates(rows, qe), qe)
                           for rows in data.tables.values())
            unknown_times = [{"table": t, "fiscal_date": r["date"],
                              "reason": "statement_availability_unknown"}
                             for t, rows in data.tables.items() for r in rows
                             if window_start < r["date"] <= qe
                             and data.public_availability(r)["public_available_at"] is None]
            if table_ok:
                ok3 += 1
            else:
                reason = gap_reason(data.any_table_known(qe), jobs.get(sym), window_start,
                                    data.listed_after, data.listed_by)
                if unknown_times and not observed_at:
                    reason = "statement_availability_unknown"
                reasons3[reason] = reasons3.get(reason, 0) + 1
                gaps3.append({"quarter_end": qe, "symbol": sym, "reason": reason,
                              "inherent": reason in INHERENT_GAP_REASONS,
                              "requires_review": reason == "statement_availability_unknown"})

            known = [r for r in data.earnings
                     if r["announce_date"][:10] <= qe and r.get("eps_actual") is not None]
            for r in known:
                m = r.get("match_method") or "none"
                mapping[m] = mapping.get(m, 0) + 1
            anchor = data.current_fiscal(qe)
            depth = street_eps_depth(data.earnings, qe, anchor,
                                     income_rows=data.income, splits=data.splits)
            if anchor is None:
                # No statement anchor is not permission to rank stale EPS alone.
                depth.update(sue_ok=False, dsue_ok=False, depth_ok=False, depth_full=False,
                             sue_missing="statement_availability_unknown" if unknown_times
                             else "missing_statement_anchor")
                depth["dsue_missing"] = depth["sue_missing"]
            joint += bool(table_ok and depth["sue_ok"])
            quality.append({"as_of": qe, "symbol": sym,
                            "statement_issues": unknown_times,
                            "public_date_floor_adjustments": [dict(table=t, fiscal_date=r["date"],
                                **data.public_availability(r)) for t, rows in data.tables.items()
                                for r in rows if window_start < r["date"] <= qe and
                                "statement_date_before_earnings" in data.public_availability(r)["issues"]],
                            "eps_issues": depth["quality_issues"],
                            "split_check_status": depth["split_check"]["status"],
                            "split_paired_quarters": depth["split_check"]["paired_quarters"]})
            depth_ok += depth["depth_ok"]
            depth_full += depth["depth_full"]
            sue += depth["sue_ok"]
            dsue += depth["dsue_ok"]
            if depth["dup_fiscal"]:
                dup_fiscal[sym] = sorted(set(dup_fiscal.get(sym, []) + depth["dup_fiscal"]))
            for sp in depth["split_check"]["issues"]:
                splits_out[(sym, sp["boundary_fiscal"], sp["split_date"])] = dict(sp, symbol=sym)
            if not depth["sue_ok"]:
                unmapped = [r for r in known if not r.get("fiscal_date")]
                reason = eps_gap_reason(depth, data.known_dates(data.income, qe), unmapped,
                                        jobs.get(sym), eps_start,
                                        data.listed_after, data.listed_by)
                if anchor is None:
                    reason = depth["sue_missing"]
                reasons_eps[reason] = reasons_eps.get(reason, 0) + 1
                gaps_eps.append({"quarter_end": qe, "symbol": sym, "reason": reason,
                                 "consecutive": depth["consecutive"],
                                 "sue_missing": depth["sue_missing"],
                                 "inherent": reason in INHERENT_GAP_REASONS,
                                 "requires_review": reason.startswith("eps_") or reason == "statement_availability_unknown"})
            if observed_at is None:
                arrivals.append(arrival_day(data.tables, qe, earnings_rows=data.earnings))

        n = len(members)
        pct3 = (ok3 / n) if n else 0.0
        quarters.append({
            "quarter_end": qe, "members": n,
            "three_table_ok": ok3, "three_table_pct": round(pct3, 4),
            "raw_three_table_ok": raw_ok3,
            "raw_three_table_pct": round(raw_ok3 / n, 4) if n else None,
            "joint_three_table_sue_ok": joint,
            "three_table_pass": bool(n) and pct3 >= THREE_TABLE_GATE,
            "depth_ok": depth_ok, "depth_full": depth_full, "sue_ok": sue, "dsue_ok": dsue,
            "sue_ok_pct": round(sue / n, 4) if n else None,
            "mapping": mapping, "three_table_gap_reasons": reasons3,
            "street_eps_gap_reasons": reasons_eps,
            "freeze": freeze_coverage(arrivals),
        })

    fixable_eps = sorted({g["symbol"] for g in gaps_eps if not g["inherent"] and not g["requires_review"]})
    fixable_three = sorted({g["symbol"] for g in gaps3 if not g["inherent"] and not g["requires_review"]})
    return {
        "input_mode": "observed_snapshot" if observed_at else "historical_public",
        "snapshot_observed_at": observed_at,
        "quality_issues": quality,
        "minimum_three_table_coverage": THREE_TABLE_GATE,
        "quarter_ends": quarters,
        "gaps": {"three_table": gaps3, "street_eps": gaps_eps},
        "dup_fiscal": [{"symbol": s, "fiscal_dates": f} for s, f in sorted(dup_fiscal.items())],
        "split_suspects": [splits_out[k] for k in sorted(splits_out)],
        "summary": {
            "three_table_all_pass": all(q["three_table_pass"] for q in quarters),
            "three_table_fixable_symbols": fixable_three,
            "eps_fixable_symbols": fixable_eps,
            "eps_review_symbols": sorted({g["symbol"] for g in gaps_eps if g["requires_review"]}),
            "three_table_review_symbols": sorted({g["symbol"] for g in gaps3 if g["requires_review"]}),
            "split_coverage_symbols": sum(1 for d in cache.values() if d.splits),
            "symbols_seen": len(cache),
        },
        "street_eps_threshold": "pending_boss",
        "definitions": {
            "three_table_gate": "has_asof_window (8 contiguous quarters, all three tables, "
                                "known by validated public date (historical) or archived observation (current)) >= {:.0%} of members".format(THREE_TABLE_GATE),
            "street_eps_depth": "consecutive mapped quarters announced by qe (fiscal dates "
                                "<=20d apart are one quarter) reaching the newest quarter "
                                "all three statements had by qe: depth >= 11, full 13",
            "sue": "north-star SUE actually computable at the EPS quarter aligned with "
                   "the three statements' current quarter: date-paired YoY numerator, "
                   "sigma = standard deviation of the 8 previous YoY changes, >= 6 obs, "
                   "finite and != 0; dSUE also at the quarter before",
            "short_history": "only with a profile ipoDate later than the window start "
                             "(and not contradicted by earlier market caps); otherwise "
                             "vendor_short / history_depth_unknown (fixable)",
            "quality": "conflicting EPS and mixed split-basis dependencies are unavailable; split checks are retrospective diagnostics, not proof of clean values or strict PIT",
            "freeze_season": "fiscal quarter ending in (prev_qe+7d, qe+7d]; arrival = the "
                             "day the last of its three statements was known "
                             "(validated accepted_date, fallback filing_date; placeholders unknown) minus qe; not evaluated for current observation mode",
        },
    }


def _render_md(doc: Dict[str, Any]) -> str:
    lines = ["# 景气引擎 D9 覆盖率报告", "",
             "生成 {} · 代码 {} · run_id {}".format(doc["generated_at"], doc["code_sha"],
                                                  ", ".join(doc["run_ids"]) or "—"), "",
             "street EPS 数值门槛：**待 Boss 定**（本报告只给数字）。", "",
             "输入模式：{}；归档观测日：{}。".format(doc.get("input_mode"), doc.get("snapshot_observed_at") or "—"), "",
             "三表覆盖率门槛：**{:.0%}**。".format(doc["minimum_three_table_coverage"]), "",
             "## 逐截面", "",
             "| 季末 | 成员 | 三表合格 | 门槛 | EPS 深度≥11 | 深度≥13 | SUE 可算 | ΔSUE 可算 "
             "| 三表到达 第60天 | 第80天 | 财报季就绪首次≥95% |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for q in doc["quarter_ends"]:
        n = q["members"] or 1
        f = q["freeze"]
        lines.append("| {} | {} | {:.1%} | {} | {:.1%} | {:.1%} | {:.1%} | {:.1%} "
                     "| {} | {} | {} |".format(
            q["quarter_end"], q["members"], q["three_table_pct"],
            "PASS" if q["three_table_pass"] else "FAIL",
            q["depth_ok"] / n, q["depth_full"] / n, q["sue_ok"] / n, q["dsue_ok"] / n,
            "—" if f["cov_d60"] is None else "{:.1%}".format(f["cov_d60"]),
            "—" if f["cov_d80"] is None else "{:.1%}".format(f["cov_d80"]),
            f["first_day_ge95"] if f["first_day_ge95"] is not None else "未达"))
    lines += ["", "## 原始计数与质量后可用性", "",
              "拆股检查为启发式：未发现疑似不代表已证实口径正确；缺元数据单列未知。", "",
              "| 截止日 | 原始齐全率（旧日期规则） | 当前模式三表可用率 | 三表与SUE均可用 |", "|---|---|---|---|"]
    for q in doc["quarter_ends"]:
        lines.append("| {} | {:.1%} | {:.1%} | {}/{} |".format(
            q["quarter_end"], q["raw_three_table_pct"] or 0, q["three_table_pct"],
            q["joint_three_table_sue_ok"], q["members"]))
    lines += ["", "## 缺口原因（逐季末计数）", "",
              "`short_history`（有上市证据的历史不足）与 `sue_degenerate`（EPS 序列本身使 σ 无定义）"
              "属于结构性缺失；质量冲突/日期未知应先核实，不能直接通过重复补数解决。", "",
              "| 季末 | 三表缺口 | street EPS 缺口 | 映射来源 |", "|---|---|---|---|"]
    for q in doc["quarter_ends"]:
        fmt = lambda d: ", ".join("{} {}".format(k, v) for k, v in sorted(d.items())) or "—"
        lines.append("| {} | {} | {} | {} |".format(
            q["quarter_end"], fmt(q["three_table_gap_reasons"]),
            fmt(q["street_eps_gap_reasons"]), fmt(q["mapping"])))
    s = doc["summary"]
    lines += ["", "## 待补名单", "",
              "- 三表可补：{} 只".format(len(s["three_table_fixable_symbols"])),
              "- street EPS 可补：{} 只".format(len(s["eps_fixable_symbols"])),
              "- EPS需核实（不进入自动补数清单）：{} 只".format(len(s["eps_review_symbols"])),
              "- 同财季重复：{} 只".format(len(doc["dup_fiscal"])),
              "- 拆股口径可疑：{} 条（拆股数据只覆盖 {} / {} 只）".format(
                  len(doc["split_suspects"]), s["split_coverage_symbols"], s["symbols_seen"]),
              "", "逐只明细见同名 JSON。", "", "## 口径", ""]
    lines += ["- {}: {}".format(k, v) for k, v in doc["definitions"].items()]
    return "\n".join(lines) + "\n"


@contextmanager
def _report_input(args):
    if not args.snapshot_manifest:
        if args.as_of or not args.targets:
            raise ValueError("historical mode requires --targets; current mode requires --snapshot-manifest")
        store = MarketStore(Path(args.db_path), read_only=True) if args.db_path else _open_store()
        try:
            yield store, None, None
        finally:
            store.close()
        return
    if not (args.db_path and args.as_of) or args.targets:
        raise ValueError("snapshot mode requires --db-path and --as-of, without --targets")
    manifest = json.loads(Path(args.snapshot_manifest).read_text())
    observed_at = datetime.strptime(manifest["created_at"], "%Y%m%dT%H%M%SZ").date().isoformat()
    if date.fromisoformat(args.as_of).isoformat() < observed_at:
        raise ValueError("as_of precedes snapshot observation")
    # Hash exactly the private bytes SQLite will read. Never let a source WAL
    # or a concurrent rename replace the contents certified by the manifest.
    with tempfile.TemporaryDirectory(prefix="prosperity-snapshot-") as tmp:
        private = Path(tmp) / "snapshot.db"
        digest = hashlib.sha256()
        with Path(args.db_path).open("rb") as source, private.open("xb") as dest:
            for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(block)
                dest.write(block)
        snapshot_sha = digest.hexdigest()
        if snapshot_sha != manifest["snapshot_sha256"]:
            raise ValueError("snapshot SHA mismatch")
        store = MarketStore(private, read_only=True)
        conn = sqlite3.connect(private.as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        store._local.conn = conn
        try:
            yield store, observed_at, snapshot_sha
        finally:
            store.close()


def cmd_report(args) -> int:
    if args.eps_targets_out and Path(args.eps_targets_out).exists():
        print("report: {} already exists — frozen target lists are never overwritten; pick a new path (exit 2)".format(args.eps_targets_out))
        return 2
    with _report_input(args) as (store, observed_at, snapshot_sha):
        return _write_report(args, store, observed_at, snapshot_sha)


def _write_report(args, store, observed_at, snapshot_sha):
    if observed_at:
        from src.data.universe_resolver import current_base_universe
        symbols = current_base_universe(store)
        targets = {"by_quarter_end": {args.as_of: symbols}}
    else:
        targets = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    doc = build_report(store, targets, args.run_id or [], observed_at=observed_at)
    doc["snapshot_sha256"] = snapshot_sha
    doc.update({
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_sha": _code_sha(), "run_ids": args.run_id or [],
        "targets_file": str(args.targets),
    })
    out_dir = Path(args.out_dir)
    stem = "d9-coverage-{}".format(args.date or date.today().isoformat())
    _write_json(out_dir / (stem + ".json"), doc)
    (out_dir / (stem + ".md")).write_text(_render_md(doc), encoding="utf-8")
    if args.eps_targets_out:
        _write_json(Path(args.eps_targets_out), {
            "symbols": doc["summary"]["eps_fixable_symbols"],
            "source": "d9 report street EPS fixable gaps", "report": stem})
    failing = [q["quarter_end"] for q in doc["quarter_ends"] if not q["three_table_pass"]]
    print("report: {} quarter ends, three-table gate FAIL at {}; street EPS fixable {} "
          "-> {}".format(len(doc["quarter_ends"]), failing or "none",
                         len(doc["summary"]["eps_fixable_symbols"]), out_dir / stem))
    return 0 if not failing else 1


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("targets", help="freeze the quarter-end member union")
    t.add_argument("--start", default="2021-09-30")
    t.add_argument("--end", default="2026-06-30")
    t.add_argument("--out", default=str(DEFAULT_TARGETS_PATH))
    t.set_defaults(func=cmd_targets)
    r = sub.add_parser("report", help="read-only D9 coverage report")
    r.add_argument("--targets", help="historical targets JSON from `targets`")
    r.add_argument("--db-path", help="explicit immutable/read-only database")
    r.add_argument("--snapshot-manifest", help="archive manifest with created_at and snapshot_sha256")
    r.add_argument("--as-of", help="current snapshot cutoff; must not precede archived observation")
    r.add_argument("--run-id", action="append", default=None,
                   help="backfill run id(s) whose job ledger explains gaps")
    r.add_argument("--out-dir", default=str(DEFAULT_REPORT_DIR))
    r.add_argument("--date", default=None, help="report file date (default today)")
    r.add_argument("--eps-targets-out", default=None,
                   help="also write fixable street EPS gaps as a NEW targets file "
                        "(refuses to overwrite)")
    r.set_defaults(func=cmd_report)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
