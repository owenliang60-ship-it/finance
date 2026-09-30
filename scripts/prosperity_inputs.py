#!/usr/bin/env python3
"""Build prosperity M4 point-in-time input packets (read-only on market.db).

Writes inputs-<as_of>.jsonl, summary-<as_of>.json and summary-<as_of>.md.
Exit codes: 0 ok; 2 future-dated input found; 3 per-symbol build errors;
4 invalid arguments (e.g. live without --observed-at, or as_of before it).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from terminal.prosperity.inputs import build_packets  # noqa: E402
from terminal.prosperity.packet import packet_leaks, packet_to_dict  # noqa: E402

DEFAULT_NAMED = ("KLAC", "ANET", "ORLY", "APH", "MNST", "DELL", "FTV", "LH", "BKNG", "CMG",
                 "BHP", "FER", "ASML", "WLK", "NVDA", "YPF", "TSM")
MIN_QUARTERS, MIN_EPS = 8, 13


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--as-of", required=True)
    p.add_argument("--mode", choices=("live", "replay"), default="replay")
    p.add_argument("--observed-at", help="observation date of the database contents (required for live)")
    p.add_argument("--db", default=str(PROJECT_ROOT / "data" / "market.db"))
    p.add_argument("--out-dir", required=True)
    p.add_argument("--symbols", help="comma-separated members to build; also the named cases")
    p.add_argument("--no-beta", action="store_true")
    return p.parse_args(argv)


def _named_case(p) -> Dict[str, Any]:
    last = p.quarters[-1] if p.quarters else None
    return {
        "membership_basis": p.membership_basis, "current_fiscal": p.current_fiscal,
        "quarters": len(p.quarters), "eps_window": len(p.eps),
        "eps": [[e.fiscal_date, e.eps_actual, list(e.labels)] for e in p.eps],
        "eps_labels": {e.fiscal_date: list(e.labels) for e in p.eps if e.labels},
        "nulled": {q.fiscal_date: list(q.nulled) for q in p.quarters if q.nulled},
        "last_quarter": None if last is None else {"fiscal_date": last.fiscal_date, "available_on": last.available_on,
                                                   "availability_basis": last.availability_basis,
                                                   "labels": list(last.labels)},
        "ntm": {"value": p.consensus.ntm.value, "basis": p.consensus.ntm.basis,
                "quarters": [list(q) for q in p.consensus.ntm.quarters],
                "missing_reason": p.consensus.ntm.missing_reason},
        "flags": list(p.flags), "pit": dict(p.pit), "listing_date": p.listing_date,
    }


def _summary(args, packets, meta, leaks) -> Dict[str, Any]:
    quality, eps_labels, flags = Counter(), Counter(), Counter()
    for p in packets:
        flags.update(p.flags)
        for q in p.quarters:
            quality.update(q.labels)
            quality.update(n.split(":", 1)[1] for n in q.nulled)
        for e in p.eps:
            eps_labels.update(e.labels)
    complete = lambda qs: all("balance_unavailable" not in q.labels and "cashflow_unavailable" not in q.labels
                              for q in qs)
    named = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_NAMED
    flat = [(p.symbol, msg) for p, msgs in leaks for msg in msgs]
    return {
        "as_of": args.as_of, "mode": args.mode, "db_path": args.db, "observed_at": args.observed_at,
        "members_resolved": meta["members_resolved"], "packets_built": len(packets),
        "membership_basis": meta["membership_basis"],
        "with_current_fiscal": sum(p.current_fiscal is not None for p in packets),
        "quarters_ge_8": sum(len(p.quarters) >= MIN_QUARTERS and complete(p.quarters[-MIN_QUARTERS:]) for p in packets),
        "eps_window_ge_13": sum(len(p.eps) >= MIN_EPS for p in packets),
        "ntm_available": sum(p.consensus.ntm.value is not None for p in packets),
        "pre_announce_available": sum(p.consensus.pre_announce.value is not None for p in packets),
        "revision_available": sum(p.consensus.revision.delta_eps is not None for p in packets),
        "unit_unverified": sum("unit_unverified" in p.flags for p in packets),
        "listing_date_known": sum(p.listing_date is not None for p in packets),
        "flag_counts": dict(flags.most_common()), "eps_label_counts": dict(eps_labels.most_common()),
        "quality_code_counts": dict(quality.most_common()),
        "pit_counts": dict(Counter(p.pit_basis for p in packets)),
        "future_leak_count": len(flat), "future_leak_examples": [f"{s}: {m}" for s, m in flat[:20]],
        "errors": meta["errors"], "requested_not_members": meta["requested_not_members"], "load_seconds": meta["load_seconds"], "build_seconds": meta["build_seconds"],
        "named_cases": {p.symbol: _named_case(p) for p in packets if p.symbol in named},
    }


def _markdown(s: Dict[str, Any]) -> str:
    lines = [f"# 景气 M4 输入包 {s['as_of']}（{s['mode']}）", "", f"- 数据库：`{s['db_path']}`", ""]
    lines += ["| 指标 | 数量 |", "|---|---|"]
    for key in ("members_resolved", "packets_built", "with_current_fiscal", "quarters_ge_8", "eps_window_ge_13",
                "ntm_available", "pre_announce_available", "revision_available", "unit_unverified",
                "listing_date_known", "future_leak_count"):
        lines.append(f"| {key} | {s[key]} |")
    lines += ["", f"- 单股错误：{len(s['errors'])}", f"- 请求但当期不是成员：{s['requested_not_members'] or '无'}", f"- 点时等级：{s['pit_counts']}",
              f"- EPS 标签：{s['eps_label_counts']}", f"- 质量码：{s['quality_code_counts']}", "",
              "## 点名样本", "", "| 代码 | 当前财季 | 季度 | EPS 窗口 | NTM | flags |", "|---|---|---|---|---|---|"]
    for sym, c in sorted(s["named_cases"].items()):
        ntm = c["ntm"]["basis"] or c["ntm"]["missing_reason"]
        lines.append(f"| {sym} | {c['current_fiscal']} | {c['quarters']} | {c['eps_window']} | {ntm} | "
                     f"{', '.join(c['flags'])} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        date.fromisoformat(args.as_of)
        if args.mode == "live":
            if not args.observed_at or args.as_of < date.fromisoformat(args.observed_at).isoformat():
                raise ValueError("live mode needs --observed-at on or before --as-of")
    except ValueError as exc:
        print(f"invalid arguments: {exc}", file=sys.stderr)
        return 4
    store = MarketStore(db_path=Path(args.db), read_only=True)
    packets, meta = build_packets(store, args.as_of, mode=args.mode,
                                  observed_at=args.observed_at if args.mode == "live" else None,
                                  symbols=args.symbols.split(",") if args.symbols else None,
                                  with_beta=not args.no_beta)
    leaks = [(p, found) for p in packets if (found := packet_leaks(p))]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"inputs-{args.as_of}.jsonl", "w") as fh:
        for p in packets:
            fh.write(json.dumps(packet_to_dict(p), sort_keys=True) + "\n")
    summary = _summary(args, packets, meta, leaks)
    (out / f"summary-{args.as_of}.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    (out / f"summary-{args.as_of}.md").write_text(_markdown(summary))
    print(f"{len(packets)} packets, {summary['future_leak_count']} leaks, {len(meta['errors'])} errors -> {out}")
    if leaks:
        return 2
    return 3 if meta["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
