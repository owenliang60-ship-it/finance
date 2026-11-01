"""Independent check of the 2026-10-31 pool (plan Task 4 Step 3).

sqlite3 only; imports nothing from src/ or terminal/. Trading days come from SPY's price rows,
not from the "half of members" rule used in production, so the two calendars cross-check.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--market-db', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--pool', type=Path, required=True)
    p.add_argument('--as-of', default='2026-10-31')
    a = p.parse_args()
    pool = json.loads(a.pool.read_text())
    pdb = sqlite3.connect(a.db.resolve().as_uri() + '?mode=ro', uri=True)
    mdb = sqlite3.connect(a.market_db.resolve().as_uri() + '?mode=ro', uri=True)
    source = pool.get('source') or {}
    board = pdb.execute("SELECT board_id, members_json FROM board WHERE board_type='weekly' AND scheme_id='F1' "
                        "AND mode='live' AND as_of=? AND code_version=? AND content_hash=?",
                        (a.as_of, source.get('code_version'), source.get('content_hash'))).fetchall()
    assert len(board) == 1, board
    board_id, members = board[0][0], json.loads(board[0][1])
    window = [r[0] for r in mdb.execute(
        "SELECT date FROM daily_price WHERE symbol='SPY' AND date<=? ORDER BY date DESC LIMIT 30", (a.as_of,))][::-1]
    marks = ','.join('?' * len(members))
    dv = {s: v for s, v, n in mdb.execute(
        f"SELECT symbol, AVG(close*volume), COUNT(*) FROM daily_price WHERE date BETWEEN ? AND ? "
        f"AND symbol IN ({marks}) AND close>0 AND volume>=0 GROUP BY symbol", [window[0], window[-1], *members])
        if v is not None and n >= 5}
    dv_rank = {s: i for i, s in enumerate(sorted(dv, key=lambda s: (-dv[s], s)), 1)}
    rows = pdb.execute("SELECT symbol, rank, grade FROM score WHERE board_id=? AND status='ranked' "
                       "AND grade IN ('STRICT','FULL') ORDER BY rank", (board_id,)).fetchall()
    expected = [(s, r, g, dv_rank[s]) for s, r, g in rows if dv_rank.get(s, 10**9) <= 100]
    actual = [(m['symbol'], m['rank'], m['grade'], m['dollar_volume_rank']) for m in pool['members']]
    lines = [f'as_of {a.as_of}  board_id {board_id}  members {len(members)}',
             f'SPY window {window[0]} .. {window[-1]} ({len(window)} days); pool window {pool["coverage"]["trading_window"]}',
             f'expected {len(expected)} (STRICT {sum(g == "STRICT" for _, _, g, _ in expected)}); pool {len(actual)}']
    diffs = [f'  expected {e}  pool {p}' for e, p in zip(expected, actual) if e != p]
    if len(expected) != len(actual):
        diffs.append(f'  length differs: expected {len(expected)} pool {len(actual)}')
    if [window[0], window[-1]] != pool['coverage']['trading_window']:
        diffs.append('  trading window differs')
    lines.append(f'differences: {len(diffs)}')
    lines.extend(diffs)
    lines.extend(f'  #{r:>3} {s:<6} {g:<6} dv#{d}' for s, r, g, d in expected)
    print('\n'.join(lines))
    return 1 if diffs else 0


if __name__ == '__main__':
    sys.exit(main())
