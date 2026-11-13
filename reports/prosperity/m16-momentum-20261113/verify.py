"""Independent check of the M16 momentum JSON. Standard library only; imports no repo code.

Run:  /usr/bin/python3 -I verify.py --json momentum.json --market-db market.db --db prosperity.db \
          --code-version <v> --as-of YYYY-MM-DD

Recomputes, from raw daily_price rows and the rule text (docs/runbooks/momentum-ls-01.md §2), every
scored row of both windows: return, ER, OLS slope and R² of log price, drawdown, dollar volume,
whole-pool percentiles, score, long gate and rank. Also checks that scored ∪ unavailable is exactly
the F1 live weekly board's member list. Split decisions are not re-judged: the listed adjustments
are applied as given and printed, with the split-excluded symbols, for a human to read.
Exit 0: no difference. Exit 1: at least one difference (each printed).
"""
import argparse
import json
import math
import sqlite3
import sys

WINDOWS = {'7w': 35, '14w': 70}
WEIGHTS = {'absolute_return': 0.45, 'er': 0.125, 'r_squared': 0.125, 'drawdown': 0.05, 'quote_volume': 0.25}
HALF = 1_000_000_000.0
LONG_DRAWDOWN_LIMIT = 0.30
NUMERIC = ('score', 'return', 'er', 'r_squared', 'slope', 'drawdown', 'dollar_volume')
SPLIT_REASONS = ('split_unresolved', 'split_suspect')


def ro(path):
    return sqlite3.connect('file:' + path + '?mode=ro', uri=True)


def metrics(closes):
    logs = [math.log(c) for c in closes]
    path = math.fsum(abs(b - a) for a, b in zip(logs, logs[1:]))
    n = len(logs)
    mx, my = (n - 1) / 2.0, math.fsum(logs) / n
    sxx = math.fsum((i - mx) ** 2 for i in range(n))
    sxy = math.fsum((i - mx) * (y - my) for i, y in enumerate(logs))
    syy = math.fsum((y - my) ** 2 for y in logs)
    flat = path == 0.0 or syy == 0.0
    return {'return': closes[-1] / closes[0] - 1.0,
            'er': 0.0 if path == 0.0 else min(1.0, abs(logs[-1] - logs[0]) / path),
            'slope': 0.0 if flat else sxy / sxx,
            'r_squared': 0.0 if flat else min(1.0, sxy * sxy / (sxx * syy)),
            'drawdown': 1.0 - closes[-1] / max(closes)}


def main():
    ap = argparse.ArgumentParser()
    for name in ('--json', '--market-db', '--db', '--code-version', '--as-of'):
        ap.add_argument(name, required=True)
    args = ap.parse_args()
    with open(args.json, encoding='utf-8') as handle:
        payload = json.load(handle)
    diffs, notes = [], []

    def diff(*parts):
        diffs.append(' '.join(str(p) for p in parts))

    # --- members: straight from the board header --------------------------------------------------
    rows = ro(args.db).execute(
        "SELECT members_json FROM board WHERE board_type='weekly' AND mode='live' AND scheme_id='F1' "
        'AND as_of=? AND code_version=?', (args.as_of, args.code_version)).fetchall()
    if len(rows) != 1:
        print('no unique F1 live weekly board for', args.as_of, args.code_version)
        return 1
    members = sorted(json.loads(rows[0][0]))
    if payload.get('as_of') != args.as_of:
        diff('as_of', payload.get('as_of'))
    if payload.get('universe') != {'count': len(members), 'symbols': members}:
        diff('universe is not the board member list')
    if payload.get('weights') != WEIGHTS or payload.get('scoring') != 'v4':
        diff('weights/scoring', payload.get('weights'), payload.get('scoring'))
    if sorted(payload.get('boards', {})) != sorted(WINDOWS):
        print('boards missing:', sorted(payload.get('boards', {})))
        return 1

    # --- calendar and raw prices --------------------------------------------------------------------
    market = ro(args.market_db)
    calendar = [r[0] for r in market.execute(
        "SELECT date FROM daily_price WHERE symbol='SPY' AND close > 0 AND date <= ? ORDER BY date", (args.as_of,))]
    longest = max(WINDOWS.values())
    if len(calendar) < longest + 1:
        print('calendar too short')
        return 1
    if payload.get('price_date') != calendar[-1]:
        diff('price_date', payload.get('price_date'), calendar[-1])
    raw = {}
    for symbol, day, close, volume in market.execute(
            'SELECT symbol, date, close, volume FROM daily_price WHERE date >= ? AND date <= ?',
            (calendar[-longest - 1], calendar[-1])):
        raw.setdefault(symbol, {})[day] = (close, volume)

    checked = unavailable_total = 0
    for label, days in WINDOWS.items():
        board = payload['boards'][label]
        window = calendar[-days - 1:]
        if board.get('days') != days or board.get('window') != [window[0], window[-1]]:
            diff(label, 'window', board.get('window'), [window[0], window[-1]])
        scored, unavailable = board['scored'], board['unavailable']
        names = [r['symbol'] for r in scored]
        if len(set(names)) != len(names):
            diff(label, 'duplicate scored symbols')
        if set(names) & set(unavailable):
            diff(label, 'scored and unavailable overlap', sorted(set(names) & set(unavailable)))
        if sorted(set(names) | set(unavailable)) != members:
            missing = sorted(set(members) - set(names) - set(unavailable))
            extra = sorted((set(names) | set(unavailable)) - set(members))
            diff(label, 'scored ∪ unavailable != board members; missing', missing, 'extra', extra)
        factors = {}
        for adj in board['adjustments']:
            factors.setdefault(adj['symbol'], []).append((adj['date'], adj['factor']))
            notes.append('%s adjustment applied as listed: %s %s ÷%s' % (label, adj['symbol'], adj['date'], adj['factor']))

        # recompute every scored row
        mine = {}
        for row in scored:
            symbol = row['symbol']
            for key in NUMERIC:                                  # finiteness first: NaN passes any tolerance test
                value = row.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    diff(label, symbol, key, 'not a finite number:', value)
            points = raw.get(symbol, {})
            if any(day not in points or points[day][0] is None or points[day][0] <= 0 for day in window):
                diff(label, symbol, 'scored but a window close is missing')
                continue
            closes = []
            for day in window:
                close = points[day][0]
                for when, factor in factors.get(symbol, ()):
                    if day < when:
                        close /= factor
                closes.append(close)
            jumps = [(window[i + 1], closes[i + 1] / closes[i]) for i in range(days)
                     if not 0.55 <= closes[i + 1] / closes[i] <= 1.8]
            for day, ratio in jumps:
                notes.append('%s scored series has a large one-day move: %s %s ×%.3f' % (label, symbol, day, ratio))
            volumes = [points[day] for day in window[1:]]
            if any(v is None or v < 0 for _, v in volumes):
                diff(label, symbol, 'scored but a window volume is missing')
                continue
            m = metrics(closes)
            m['dollar_volume'] = math.fsum(c * v for c, v in volumes) / days
            m['absolute_return'] = abs(m['return'])
            mine[symbol] = m
        n = len(mine)
        for symbol, m in mine.items():
            score = 0.0
            for key in ('absolute_return', 'er', 'r_squared'):
                score += WEIGHTS[key] * 100.0 * sum(o[key] <= m[key] for o in mine.values()) / n
            score += WEIGHTS['drawdown'] * 100.0 * sum(o['drawdown'] >= m['drawdown'] for o in mine.values()) / n
            score += WEIGHTS['quote_volume'] * 100.0 * m['dollar_volume'] / (m['dollar_volume'] + HALF)
            m['score'] = score
            m['eligible'] = m['return'] > 0 and m['slope'] > 0 and m['drawdown'] <= LONG_DRAWDOWN_LIMIT
        order = sorted((s for s in mine if mine[s]['eligible']), key=lambda s: (-mine[s]['score'], s))
        rank = {s: i for i, s in enumerate(order, 1)}
        for row in scored:
            symbol = row['symbol']
            if symbol not in mine:
                continue
            checked += 1
            m = mine[symbol]
            for key in NUMERIC:
                value = row.get(key)
                if (isinstance(value, (int, float)) and math.isfinite(value)
                        and not math.isclose(value, m[key], rel_tol=1e-9, abs_tol=1e-9)):
                    diff(label, symbol, key, value, 'recomputed', m[key])
            if row.get('eligible') is not m['eligible']:
                diff(label, symbol, 'eligible', row.get('eligible'), 'recomputed', m['eligible'])
            if row.get('rank') != rank.get(symbol):
                diff(label, symbol, 'rank', row.get('rank'), 'recomputed', rank.get(symbol))
        stated = {r['symbol']: r['score'] for r in scored}
        if names != sorted(names, key=lambda s: (-stated[s], s)):
            diff(label, 'scored rows are not in (-score, symbol) order')

        # unavailable: re-check the reasons that do not need a split judgement
        for symbol, reason in sorted(unavailable.items()):
            unavailable_total += 1
            points = raw.get(symbol, {})
            gap = any(day not in points or points[day][0] is None or points[day][0] <= 0 for day in window)
            if reason == 'no_price':
                if market.execute('SELECT 1 FROM daily_price WHERE symbol=? AND close > 0 LIMIT 1', (symbol,)).fetchone():
                    diff(label, symbol, 'no_price but the symbol has prices')
            elif reason == 'price_gap':
                if not gap:
                    diff(label, symbol, 'price_gap but every window close exists')
            elif reason == 'volume_missing':
                if gap or not any(points[day][1] is None or points[day][1] < 0 for day in window[1:]):
                    diff(label, symbol, 'volume_missing but closes and volumes are complete')
            elif reason in SPLIT_REASONS:
                notes.append('%s excluded for a split, not re-judged: %s (%s)' % (label, symbol, reason))
            else:
                diff(label, symbol, 'unknown reason', reason)

    for line in notes:
        print('note:', line)
    for line in diffs[:60]:
        print('DIFF:', line)
    print('checked %d scored rows, %d unavailable; differences: %d' % (checked, unavailable_total, len(diffs)))
    return 1 if diffs else 0


if __name__ == '__main__':
    sys.exit(main())
