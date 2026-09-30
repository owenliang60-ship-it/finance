"""M7 S1 independent spot check (plan Task 6 Step 5): sqlite3 and csv only, no engine or M7 import.

For five (board, symbol) pairs: find our quarter on its own (the income_quarterly date
closest to the site's latest_q, within 20 days) and check the tool aligned to the same one;
check each pick's pass/fail label against the tool's result; then recompute the site-basis
revenue YoY and gross-margin YoY by position ([i-4], no day-count adjustment) and compare
with the tool's value in the private rows.csv.
Writes spot_check.json next to this file with our values only (never the site's).
Usage: python spot_check.py <rows.csv> <market.db> <site fixture json>
"""
from datetime import date
import csv
import json
import sqlite3
import sys
from pathlib import Path

PICKS = [  # three pairs where every factor matched, two with a failed factor
    ("2025-06-30", "NVDA", "pass"), ("2023-12-31", "MSFT", "pass"), ("2024-06-30", "AMZN", "pass"),
    ("2021-03-31", "MA", "fail"), ("2025-09-30", "HOOD", "fail"),
]
FACTORS = ("revenue_yoy", "gm_yoy")


def _gap(a: str, b: str) -> int:
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def main(rows_path: str, db_path: str, fixture_path: str) -> int:
    latest = {(b["asof"][:10], x["symbol"]): x["latest_q"][:10]
              for b in json.load(open(fixture_path))["boards"] for x in b["rows"]}
    tool = {}
    for r in csv.DictReader(open(rows_path)):
        if r["basis"] == "site":
            tool[(r["board"], r["symbol"], r["factor"])] = r
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out, all_ok = [], True
    for board, sym, kind in PICKS:
        fiscal = tool[(board, sym, FACTORS[0])]["fiscal_date"]
        failed = [f for (b, s, f), r in tool.items() if b == board and s == sym and r["counted"] == "True"
                  and r["passed"] == "False"]
        dates = [d[:10] for (d,) in conn.execute("SELECT date FROM income_quarterly WHERE symbol = ?", (sym,))]
        near = sorted((_gap(d, latest[(board, sym)]), d) for d in dates if _gap(d, latest[(board, sym)]) <= 20)
        aligned_ok = bool(near) and near[0][1] == fiscal
        label_ok = (kind == "pass") == (not failed)
        all_ok &= aligned_ok and label_ok
        rows = conn.execute("SELECT date, revenue, gross_profit FROM income_quarterly WHERE symbol = ? "
                            "AND date <= ? ORDER BY date", (sym, fiscal)).fetchall()
        cur, base = rows[-1], rows[-5]
        recomputed = {"revenue_yoy": (cur[1] / base[1] - 1) * 100,
                      "gm_yoy": cur[2] / cur[1] * 100 - base[2] / base[1] * 100}
        for f in FACTORS:
            ours = tool[(board, sym, f)]["our_value"]
            ours = float(ours) if ours else None
            ok = ours is not None and abs(ours - recomputed[f]) < 1e-6
            all_ok &= ok
            out.append({"board": board, "symbol": sym, "kind": kind, "fiscal_date": fiscal,
                        "base_quarter": base[0][:10], "factor": f, "tool_value": ours,
                        "recomputed": recomputed[f], "match": ok, "aligned_independently": aligned_ok,
                        "label_consistent": label_ok, "failed_factors": failed})
    Path(__file__).with_name("spot_check.json").write_text(json.dumps(out, indent=2))
    print(f"{sum(o['match'] for o in out)}/{len(out)} values match; "
          f"{sum(o['aligned_independently'] for o in out[::2])}/{len(PICKS)} quarters aligned independently; "
          f"{sum(o['label_consistent'] for o in out[::2])}/{len(PICKS)} labels consistent")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
