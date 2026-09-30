#!/usr/bin/env python3
"""Score prosperity M4 input packets with the M6 kernel under every M5 scheme (read-only on market.db).

Acceptance CLI: every parameter package bootstraps (no frozen packages before M8).
Writes board-<scheme>-<as_of>.jsonl ({"factor_row", "scored"} per member),
summary-<as_of>.json and summary-<as_of>.md.
Exit codes: 0 ok; 2 future-dated input found (nothing scored); 3 per-symbol build errors;
4 invalid arguments (unknown scheme, live without --observed-at, or as_of before it).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from terminal.prosperity.factors import compute_factor_row  # noqa: E402
from terminal.prosperity.inputs import build_packets  # noqa: E402
from terminal.prosperity.kernel import score_board  # noqa: E402
from terminal.prosperity.packet import packet_leaks  # noqa: E402
from terminal.prosperity.schemes import SCHEMES, SCORING_FACTORS, scheme_hash  # noqa: E402
from terminal.prosperity.version import code_version  # noqa: E402

DEFAULT_NAMED = ("NVDA", "MSFT", "AAPL", "COST", "JPM", "V", "KLAC", "ANET", "COIN", "FER", "TSM", "BHP", "CRWV")
TOP_N = 40


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--as-of", required=True)
    p.add_argument("--mode", choices=("live", "replay"), default="replay")
    p.add_argument("--observed-at", help="observation date of the database contents (required for live)")
    p.add_argument("--db", default=str(PROJECT_ROOT / "data" / "market.db"))
    p.add_argument("--out-dir", required=True)
    p.add_argument("--schemes", help="comma-separated scheme ids (default: all)")
    p.add_argument("--symbols", help="comma-separated named cases (the whole membership is always scored)")
    p.add_argument("--no-beta", action="store_true")
    return p.parse_args(argv)


def _counts(items) -> Dict[str, int]:
    return dict(Counter(items).most_common())


def _nested(pairs) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Counter] = defaultdict(Counter)
    for factor, reason in pairs:
        out[factor][reason] += 1
    return {f: dict(c.most_common()) for f, c in sorted(out.items())}


def _scheme_summary(board, rows_by_symbol) -> Dict[str, Any]:
    ranked = [r for r in board.rows if r.status == "ranked"]
    scored = [r for r in board.rows if r.status in ("ranked", "observe")]
    top = ranked[:TOP_N]
    sectors = Counter(rows_by_symbol[r.symbol].sector or "unknown" for r in top)
    share = sectors.most_common(1)[0] if sectors else (None, 0)
    return {
        "scheme_hash": board.scheme_hash, "counts": dict(board.counts),
        "grades": _counts(r.grade for r in ranked),
        "demotion_counts": _counts(d for r in ranked for d in r.demotions),
        "badge_counts": _counts(b for r in board.rows for b in r.badges),
        "observe_reasons": _counts(r.observe_reason for r in board.rows if r.status == "observe"),
        "factor_coverage": {f: sum(r.values.get(f) is not None for r in scored)
                            for f, _ in SCHEMES[board.scheme_id].weights},
        "missing_reason_counts": _nested((f, why) for r in scored for f, why in r.missing.items()),
        "top": [{"rank": r.rank, "symbol": r.symbol, "sector": rows_by_symbol[r.symbol].sector, "grade": r.grade,
                 "score": round(r.score, 2), "family_scores": {k: None if v is None else round(v, 1)
                                                               for k, v in r.family_scores.items()},
                 "badges": list(r.badges)} for r in top],
        "top_sector_share": {"sector": share[0], "share": round(share[1] / len(top), 3) if top else None},
    }


def _named_case(row, packet, boards) -> Dict[str, Any]:
    return {
        "listing_days": row.listing_days, "labels": list(row.labels), "packet_flags": list(row.packet_flags),
        "current_fiscal": row.current_fiscal, "sector": row.sector, "industry": row.industry,
        "values": dict(row.values), "missing": dict(row.missing), "aux": dict(row.aux),
        "surprise_source": packet.consensus.pre_announce.source,
        "schemes": {sid: {"status": s.status, "observe_reason": s.observe_reason, "grade": s.grade,
                          "score": s.score, "rank": s.rank, "within": s.within, "coverage": s.coverage,
                          "demotions": list(s.demotions), "badges": list(s.badges),
                          "values": dict(s.values), "missing": dict(s.missing)}
                    for sid, b in boards.items() for s in b.rows if s.symbol == row.symbol},
    }


def _markdown(s: Dict[str, Any]) -> str:
    lines = [f"# 景气 M6 打分验收 {s['as_of']}（{s['mode']}）", "",
             f"- 数据库：`{s['db_path']}`", f"- code_version：`{s['code_version']}`",
             f"- 成员 {s['members_resolved']}，建包 {s['packets_built']}，单股错误 {len(s['errors'])}，"
             f"泄漏 {s['future_leak_count']}，参数自举 {s['params_bootstrap']}，耗时 {s['seconds']}s", "",
             "## 各方案", "", "| 方案 | hash | 排名/观察/剔除 | STRICT/FULL/BELOW | 降级原因 | pe_redflag | 前40最大行业 |",
             "|---|---|---|---|---|---|---|"]
    for sid, x in s["schemes"].items():
        c, g = x["counts"], x["grades"]
        top = x["top_sector_share"]
        lines.append(f"| {sid} | `{x['scheme_hash']}` | {c['ranked']}/{c['observe']}/{c['excluded']} | "
                     f"{g.get('STRICT', 0)}/{g.get('FULL', 0)}/{g.get('BELOW', 0)} | {x['demotion_counts']} | "
                     f"{x['badge_counts'].get('pe_redflag', 0)} | {top['sector']} {top['share']} |")
    lines += ["", "## 观察原因", ""] + [f"- {sid}: {x['observe_reasons']}" for sid, x in s["schemes"].items()]
    lines += ["", "## 因子覆盖（原始 / F1 方案口径）", "", "| 因子 | 原始 | F1 | 主要缺失原因（原始） |", "|---|---|---|---|"]
    f1 = s["schemes"].get("F1", {}).get("factor_coverage", {})
    for f in SCORING_FACTORS:
        reasons = dict(list(s["raw_missing_reason_counts"].get(f, {}).items())[:3])
        lines.append(f"| {f} | {s['raw_factor_coverage'][f]} | {f1.get(f, '—')} | {reasons} |")
    if "F1" in s["schemes"]:
        lines += ["", f"## F1 前 {TOP_N}", "", "| # | 代码 | 行业 | 评级 | 分数 | 徽章 |", "|---|---|---|---|---|---|"]
        for t in s["schemes"]["F1"]["top"]:
            lines.append(f"| {t['rank']} | {t['symbol']} | {t['sector']} | {t['grade']} | {t['score']} | "
                         f"{', '.join(t['badges'])} |")
    lines += ["", "## 点名样本", "", "| 代码 | 上市天数 | F1 状态/评级/分数 | F1 降级 | F1 徽章 | 标签 |",
              "|---|---|---|---|---|---|"]
    for sym, c in sorted(s["named_cases"].items()):
        f1c = c["schemes"].get("F1", {})
        score = None if f1c.get("score") is None else round(f1c["score"], 1)
        lines.append(f"| {sym} | {c['listing_days']} | {f1c.get('status')}/{f1c.get('grade')}/{score} | "
                     f"{', '.join(f1c.get('demotions', []))} | {', '.join(f1c.get('badges', []))} | "
                     f"{', '.join(c['labels'])} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    started = time.perf_counter()
    args = parse_args(argv)
    try:
        # Python 3.11+ also parses 20260929; the raw string would then slip past every date comparison
        for value in (args.as_of, args.observed_at):
            if value is not None and date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"dates must be YYYY-MM-DD, got {value}")
        if args.mode == "live":
            if not args.observed_at or args.as_of < args.observed_at:
                raise ValueError("live mode needs --observed-at on or before --as-of")
        scheme_ids = [s.strip() for s in args.schemes.split(",")] if args.schemes else list(SCHEMES)
        unknown = [s for s in scheme_ids if s not in SCHEMES]
        if unknown:
            raise ValueError(f"unknown scheme(s) {unknown}")
    except ValueError as exc:
        print(f"invalid arguments: {exc}", file=sys.stderr)
        return 4
    version = code_version()
    store = MarketStore(db_path=Path(args.db), read_only=True)
    packets, meta = build_packets(store, args.as_of, mode=args.mode,
                                  observed_at=args.observed_at if args.mode == "live" else None,
                                  with_beta=not args.no_beta)
    leaks = [(p.symbol, found) for p in packets if (found := packet_leaks(p))]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {
        "as_of": args.as_of, "mode": args.mode, "db_path": args.db, "observed_at": args.observed_at,
        "code_version": version, "members_resolved": meta["members_resolved"], "packets_built": len(packets),
        "future_leak_count": sum(len(f) for _, f in leaks),
        "future_leak_examples": [f"{s}: {m}" for s, f in leaks for m in f][:20], "errors": dict(meta["errors"]),
    }
    if leaks:           # fail closed before scoring anything
        summary["seconds"] = round(time.perf_counter() - started, 1)
        (out / f"summary-{args.as_of}.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
        print(f"{summary['future_leak_count']} future-dated inputs; nothing scored -> {out}")
        return 2
    rows, by_packet = [], {}
    for p in packets:
        try:
            rows.append(compute_factor_row(p))
            by_packet[p.symbol] = p
        except Exception as exc:   # isolate one bad packet; surfaced via errors and exit 3
            summary["errors"][p.symbol] = f"factor_row {type(exc).__name__}: {exc}"
    rows_by_symbol = {r.symbol: r for r in rows}
    boards = {sid: score_board(rows, SCHEMES[sid], frozen=None, as_of=args.as_of, code_version=version)
              for sid in scheme_ids}
    for sid, board in boards.items():
        with open(out / f"board-{sid}-{args.as_of}.jsonl", "w") as fh:
            for s in board.rows:
                fh.write(json.dumps({"factor_row": asdict(rows_by_symbol[s.symbol]), "scored": asdict(s)},
                                    sort_keys=True) + "\n")
    named = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_NAMED
    retro = sorted(r.symbol for r in rows if "eps_split_retrospective" in r.labels)
    summary.update({
        "params_bootstrap": all(b.params_bootstrap for b in boards.values()),
        "raw_factor_coverage": {f: sum(r.values[f] is not None for r in rows) for f in SCORING_FACTORS},
        "raw_missing_reason_counts": _nested((f, why) for r in rows for f, why in r.missing.items()),
        "raw_label_counts": _counts(lab for r in rows for lab in r.labels),
        "eps_split_retrospective_symbols": retro,                                   # D-9
        "surprise_source_counts": _counts(by_packet[r.symbol].consensus.pre_announce.source for r in rows
                                          if r.values["surprise"] is not None),
        "schemes": {sid: _scheme_summary(b, rows_by_symbol) for sid, b in boards.items()},
        "scheme_hashes": {sid: scheme_hash(SCHEMES[sid]) for sid in scheme_ids},
        "named_cases": {r.symbol: _named_case(r, by_packet[r.symbol], boards) for r in rows if r.symbol in named},
        "named_not_members": sorted(set(named) - {p.symbol for p in packets}),
        "seconds": round(time.perf_counter() - started, 1),
    })
    (out / f"summary-{args.as_of}.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
    (out / f"summary-{args.as_of}.md").write_text(_markdown(summary))
    print(f"{len(rows)} rows scored under {', '.join(scheme_ids)}; {len(summary['errors'])} errors -> {out}")
    return 3 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
