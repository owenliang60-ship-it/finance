"""Prosperity D9 history: frozen backfill targets + read-only coverage report.

Subcommands:
    targets  — union of the as-of $10B+ members at every quarter end → JSON,
               the frozen `--targets-file` for the backfill runners.
    report   — per quarter end: three-table window pass rate (gate ≥95%),
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
    split_suspects,
    street_eps_depth,
    three_table_ok,
)
from scripts.backfill_extended_fundamentals import (  # noqa: E402
    ASOF_WINDOW_TABLES,
    DAYS_PER_QUARTER,
    DEFAULT_ASOF_QUARTERS,
    STATEMENT_DATASETS,
)
from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS  # noqa: E402

DEFAULT_TARGETS_PATH = PROJECT_ROOT / "data" / "prosperity" / "d9_targets.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "prosperity"
THREE_TABLE_GATE = 0.95
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


# historical_market_cap is censored at its global start: a first row there
# says nothing about when the company listed.
HMCAP_CENSOR_SLACK_DAYS = 7


def _hmcap_global_start(store: MarketStore) -> Optional[str]:
    return store._get_conn().execute(
        "SELECT MIN(date) FROM historical_market_cap").fetchone()[0]


class _SymbolData:
    """Everything the report reads for one symbol, loaded once."""

    def __init__(self, store: MarketStore, symbol: str,
                 hmcap_start: Optional[str] = None):
        conn = store._get_conn()
        self.tables = {
            t: [dict(r) for r in conn.execute(
                "SELECT date, accepted_date, filing_date FROM " + t
                + " WHERE symbol = ? ORDER BY date", (symbol,)).fetchall()]
            for t in ASOF_WINDOW_TABLES}
        self.income = self.tables[ASOF_WINDOW_TABLES[0]]
        self.earnings = store.get_fmp_earnings(symbol)
        self.splits = store.get_stock_splits(symbol)
        self.listed_after, self.listed_by = self._listing_evidence(
            conn, symbol, hmcap_start)

    @staticmethod
    def _listing_evidence(conn, symbol: str, hmcap_start: Optional[str]):
        """(listed_after, listed_by): known lower / upper bounds on listing.

        ipoDate (profile) is the lower bound unless market-cap history predates
        it (then the ipoDate is wrong); without a profile, a first market cap
        clearly after the table's global start stands in. Upper bound: the
        earliest of the two.
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
        if ipo:
            listed_after = ipo if not first or first >= ipo else None
        elif first and hmcap_start and (date.fromisoformat(first) - date.fromisoformat(
                hmcap_start)).days > HMCAP_CENSOR_SLACK_DAYS:
            listed_after = first
        else:
            listed_after = None
        return listed_after, listed_by

    def known_dates(self, rows, as_of: str) -> List[str]:
        out = []
        for r in rows:
            known = (r.get("accepted_date") or "")[:10] or (r.get("filing_date") or "")[:10]
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
                 run_ids: List[str]) -> Dict[str, Any]:
    by_qe = targets["by_quarter_end"]
    jobs = _job_status(store, run_ids)
    hmcap_start = _hmcap_global_start(store)
    cache: Dict[str, _SymbolData] = {}
    quarters, gaps3, gaps_eps = [], [], []
    dup_fiscal: Dict[str, List[str]] = {}
    splits_out: Dict[tuple, Dict[str, Any]] = {}
    window_span = DEFAULT_ASOF_QUARTERS * DAYS_PER_QUARTER + FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
    eps_span = SUE_MIN_QUARTERS * DAYS_PER_QUARTER + FUNDAMENTAL_QUARTER_GAP_MAX_DAYS

    for qe in sorted(by_qe):
        members = by_qe[qe]
        window_start = (date.fromisoformat(qe) - timedelta(days=window_span)).isoformat()
        eps_start = (date.fromisoformat(qe) - timedelta(days=eps_span)).isoformat()
        ok3 = depth_ok = depth_full = sue = dsue = 0
        mapping: Dict[str, int] = {}
        reasons3: Dict[str, int] = {}
        reasons_eps: Dict[str, int] = {}
        arrivals = []
        for sym in members:
            data = cache.get(sym) or cache.setdefault(sym, _SymbolData(store, sym, hmcap_start))
            if three_table_ok(store, sym, qe):
                ok3 += 1
            else:
                reason = gap_reason(data.any_table_known(qe), jobs.get(sym), window_start,
                                    data.listed_after, data.listed_by)
                reasons3[reason] = reasons3.get(reason, 0) + 1
                gaps3.append({"quarter_end": qe, "symbol": sym, "reason": reason,
                              "inherent": reason in INHERENT_GAP_REASONS})

            known = [r for r in data.earnings
                     if r["announce_date"][:10] <= qe and r.get("eps_actual") is not None]
            for r in known:
                m = r.get("match_method") or "none"
                mapping[m] = mapping.get(m, 0) + 1
            depth = street_eps_depth(data.earnings, qe, data.current_fiscal(qe))
            depth_ok += depth["depth_ok"]
            depth_full += depth["depth_full"]
            sue += depth["sue_ok"]
            dsue += depth["dsue_ok"]
            if depth["dup_fiscal"]:
                dup_fiscal[sym] = sorted(set(dup_fiscal.get(sym, []) + depth["dup_fiscal"]))
            for sp in split_suspects(data.earnings, data.splits, qe):
                splits_out[(sym, sp["split_date"])] = dict(sp, symbol=sym)
            if not depth["sue_ok"]:
                unmapped = [r for r in known if not r.get("fiscal_date")]
                reason = eps_gap_reason(depth, data.known_dates(data.income, qe), unmapped,
                                        jobs.get(sym), eps_start,
                                        data.listed_after, data.listed_by)
                reasons_eps[reason] = reasons_eps.get(reason, 0) + 1
                gaps_eps.append({"quarter_end": qe, "symbol": sym, "reason": reason,
                                 "consecutive": depth["consecutive"],
                                 "sue_missing": depth["sue_missing"],
                                 "inherent": reason in INHERENT_GAP_REASONS})
            arrivals.append(arrival_day(data.tables, qe))

        n = len(members)
        pct3 = (ok3 / n) if n else 0.0
        quarters.append({
            "quarter_end": qe, "members": n,
            "three_table_ok": ok3, "three_table_pct": round(pct3, 4),
            "three_table_pass": bool(n) and pct3 >= THREE_TABLE_GATE,
            "depth_ok": depth_ok, "depth_full": depth_full, "sue_ok": sue, "dsue_ok": dsue,
            "sue_ok_pct": round(sue / n, 4) if n else None,
            "mapping": mapping, "three_table_gap_reasons": reasons3,
            "street_eps_gap_reasons": reasons_eps,
            "freeze": freeze_coverage(arrivals),
        })

    fixable_eps = sorted({g["symbol"] for g in gaps_eps if not g["inherent"]})
    fixable_three = sorted({g["symbol"] for g in gaps3 if not g["inherent"]})
    return {
        "quarter_ends": quarters,
        "gaps": {"three_table": gaps3, "street_eps": gaps_eps},
        "dup_fiscal": [{"symbol": s, "fiscal_dates": f} for s, f in sorted(dup_fiscal.items())],
        "split_suspects": [splits_out[k] for k in sorted(splits_out)],
        "summary": {
            "three_table_all_pass": all(q["three_table_pass"] for q in quarters),
            "three_table_fixable_symbols": fixable_three,
            "eps_fixable_symbols": fixable_eps,
            "split_coverage_symbols": sum(1 for d in cache.values() if d.splits),
            "symbols_seen": len(cache),
        },
        "street_eps_threshold": "pending_boss",
        "definitions": {
            "three_table_gate": "has_asof_window (8 contiguous quarters, all three tables, "
                                "known by accepted_date/filing_date) >= 95% of members",
            "street_eps_depth": "consecutive mapped quarters announced by qe (fiscal dates "
                                "<=20d apart are one quarter) reaching the newest quarter "
                                "all three statements had by qe: depth >= 11, full 13",
            "sue": "north-star SUE actually computable at the newest quarter: date-paired "
                   "YoY, sigma over the 8 previous YoY changes (not demeaned), >= 6 obs, "
                   "sigma != 0; dSUE also at the quarter before",
            "short_history": "only with listing evidence (profile ipoDate, or first market "
                             "cap clearly after the table start) later than the window "
                             "start; otherwise vendor_short / history_depth_unknown (fixable)",
            "freeze_season": "fiscal quarter ending in (prev_qe+7d, qe+7d]; arrival = the "
                             "day the last of its three statements was known "
                             "(accepted_date, fallback filing_date) minus qe",
        },
    }


def _render_md(doc: Dict[str, Any]) -> str:
    lines = ["# 景气引擎 D9 覆盖率报告", "",
             "生成 {} · 代码 {} · run_id {}".format(doc["generated_at"], doc["code_sha"],
                                                  ", ".join(doc["run_ids"]) or "—"), "",
             "street EPS 数值门槛：**待 Boss 定**（本报告只给数字）。", "",
             "## 逐季末", "",
             "| 季末 | 成员 | 三表合格 | 门槛 | EPS 深度≥11 | 深度≥13 | SUE 可算 | ΔSUE 可算 "
             "| 三表到达 第60天 | 第80天 | 首次≥95% |",
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
    lines += ["", "## 缺口原因（逐季末计数）", "",
              "`short_history`（有上市证据的历史不足）与 `sue_degenerate`（EPS 序列本身使 σ 无定义）"
              "不可补；其余都是采集不足 / 映射缺失 / 失败 / 历史深度待查，要回 P2 补。", "",
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
              "- 同财季重复：{} 只".format(len(doc["dup_fiscal"])),
              "- 拆股口径可疑：{} 条（拆股数据只覆盖 {} / {} 只）".format(
                  len(doc["split_suspects"]), s["split_coverage_symbols"], s["symbols_seen"]),
              "", "逐只明细见同名 JSON。", "", "## 口径", ""]
    lines += ["- {}: {}".format(k, v) for k, v in doc["definitions"].items()]
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    # A street EPS progress file is bound to its targets file's sha; rewriting
    # a frozen list would strand that progress.
    if args.eps_targets_out and Path(args.eps_targets_out).exists():
        print("report: {} already exists — frozen target lists are never "
              "overwritten; pick a new path (exit 2)".format(args.eps_targets_out))
        return 2
    targets = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    store = _open_store()
    doc = build_report(store, targets, args.run_id or [])
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
    r.add_argument("--targets", required=True, help="targets JSON from `targets`")
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
