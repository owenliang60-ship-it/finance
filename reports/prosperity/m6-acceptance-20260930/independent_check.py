#!/usr/bin/env python3
"""Independent recompute of four M6 factors from raw market.db rows (acceptance item 5).

Deliberately imports nothing from terminal.prosperity or src.data.prosperity_*: only
sqlite3 and statistics. North-star formulas: revenue YoY paired by date (365 ± 20 days)
with revenue × 91 / days since the previous income row; GM and FCF-margin YoY from the
three tables; SUE = (E_c − E_b) / stdev of up to 8 prior YoY changes (≥ 6), street EPS.
Exit 1 when any compared value differs by more than 1e-6.
"""
import argparse
import json
import sqlite3
import statistics
import sys
from datetime import date

SYMBOLS = ("NVDA", "MSFT", "AAPL", "COST", "JPM")
COMPARE = {"JPM": ("revenue_yoy", "gm_yoy")}          # bank: margin factors are exempt / placeholder
FACTORS = ("revenue_yoy", "gm_yoy", "fcf_margin_yoy", "eps_sue")
TOL = 1e-6


def d(s):
    return date.fromisoformat(s[:10])


def year_base(dates, i):
    """dates oldest first; nearest earlier entry 365 ± 20 days back."""
    for j in range(i - 1, -1, -1):
        gap = (d(dates[i]) - d(dates[j])).days
        if abs(gap - 365) <= 20:
            return j
        if gap > 385:
            return None
    return None


def statements(conn, sym, as_of):
    inc = conn.execute("SELECT date, revenue, gross_profit FROM income_quarterly WHERE symbol=? AND date<=? "
                       "ORDER BY date", (sym, as_of)).fetchall()
    cf = {r[0]: r[1:] for r in conn.execute("SELECT date, free_cash_flow, capital_expenditure FROM cash_flow_quarterly "
                                              "WHERE symbol=? AND date<=?", (sym, as_of))}
    bs = {r[0] for r in conn.execute("SELECT date FROM balance_sheet_quarterly WHERE symbol=? AND date<=?", (sym, as_of))}
    current = max(r[0] for r in inc if r[0] in cf and r[0] in bs)
    rows = [r for r in inc if r[0] <= current]
    return rows, cf, current


def statement_values(rows, cf):
    dates = [r[0] for r in rows]
    c = len(rows) - 1
    b = year_base(dates, c)
    days = lambda k: (d(dates[k]) - d(dates[k - 1])).days
    rev = lambda k: rows[k][1] * 91 / days(k)
    out = {"current": dates[c], "base": dates[b], "days_current": days(c), "days_base": days(b)}
    out["revenue_yoy"] = (rev(c) / rev(b) - 1) * 100
    gm = lambda k: rows[k][2] / rows[k][1] * 100
    out["gm_yoy"] = gm(c) - gm(b)
    fcf = lambda k: cf[dates[k]][0] / rows[k][1] * 100
    out["fcf_margin_yoy"] = fcf(c) - fcf(b)
    out["fcf_raw"] = {dates[k]: cf[dates[k]] for k in (c, b)}
    return out


def eps_sue(conn, sym, as_of, current):
    rows = conn.execute("SELECT fiscal_date, announce_date, eps_actual FROM fmp_earnings WHERE symbol=? "
                        "AND announce_date IS NOT NULL AND substr(announce_date,1,10)<=? AND eps_actual IS NOT NULL "
                        "AND fiscal_date IS NOT NULL AND match_method != 'none' ORDER BY fiscal_date DESC",
                        (sym, as_of)).fetchall()
    quarters = []                         # newest first, one per fiscal quarter; a conflicting quarter is None
    for f, _, eps in rows:
        if quarters and (d(quarters[-1][0]) - d(f)).days <= 20:
            if quarters[-1][1] is not None and abs(quarters[-1][1] - eps) > 1e-12:
                quarters[-1] = (quarters[-1][0], None)
            continue
        quarters.append((f[:10], eps))
    anchor = next((i for i, (f, _) in enumerate(quarters) if abs((d(f) - d(current)).days) <= 20), None)
    if anchor is None:
        return None, "no EPS quarter at the current fiscal", quarters[:6]
    dates = [f for f, _ in reversed(quarters)]
    vals = [e for _, e in reversed(quarters)]
    idx = len(quarters) - 1 - anchor

    used = set()

    def yoy(k):
        if k < 0:
            return None
        b = year_base(dates, k)
        if b is None:
            return None
        used.update((k, b))
        return vals[k] - vals[b] if vals[k] is not None and vals[b] is not None else None

    if vals[idx] is None:
        return None, "current quarter conflicting", []
    cur = yoy(idx)
    if cur is None:
        gaps = [(dates[idx], dates[j], (d(dates[idx]) - d(dates[j])).days) for j in range(max(0, idx - 5), idx)]
        return None, "no year-ago EPS within 365±20 days", gaps
    prior = [yoy(k) for k in range(idx - 1, idx - 9, -1)]
    # a blank (conflicting) quarter anywhere the formula reads blanks SUE; never drop it and re-form σ
    if any(vals[k] is None for k in used | set(range(max(0, idx - 8), idx + 1))):
        return None, "conflicting quarter inside the SUE window", []
    prior = [v for v in prior if v is not None]
    if len(prior) < 6:
        return None, f"only {len(prior)} prior YoY changes", []
    return cur / statistics.stdev(prior), None, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--as-of", default="2026-09-29")
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro&immutable=1", uri=True)
    board = {}
    for line in open(args.board):
        r = json.loads(line)
        board[r["factor_row"]["symbol"]] = r["factor_row"]
    report, bad = {}, []
    for sym in SYMBOLS:
        rows, cf, current = statements(conn, sym, args.as_of)
        mine = statement_values(rows, cf)
        mine["eps_sue"], why, detail = eps_sue(conn, sym, args.as_of, current)
        cli = board[sym]["values"]
        entry = {"current_fiscal": current, "cli_current_fiscal": board[sym]["current_fiscal"],
                 "base": mine["base"], "days": [mine["days_current"], mine["days_base"]], "factors": {}}
        for f in FACTORS:
            a, b = mine[f], cli.get(f)
            compared = f in COMPARE.get(sym, FACTORS)
            if a is None or b is None:
                ok = a is None and b is None
                diff = None
            else:
                diff = abs(a - b)
                ok = diff <= TOL
            entry["factors"][f] = {"independent": a, "cli": b, "cli_missing": board[sym]["missing"].get(f),
                                   "abs_diff": diff, "compared": compared, "match": ok}
            if f == "eps_sue" and why:
                entry["factors"][f]["independent_reason"] = why
                entry["factors"][f]["detail"] = detail
            if f == "fcf_margin_yoy":
                entry["factors"][f]["raw"] = mine["fcf_raw"]
            if compared and not ok:
                bad.append(f"{sym}.{f}: independent {a} vs cli {b}")
        report[sym] = entry
    print(json.dumps(report, indent=2, default=str))
    if bad:
        print("MISMATCH:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    print("all compared values within 1e-6", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
