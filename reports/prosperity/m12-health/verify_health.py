#!/usr/bin/env python3
"""M12 A7a / A7b: independent recomputation of the F1 health report (standard library + numpy only).

Does not import src/, terminal/ or backtest/. Re-implements, from the plan text:
- trading calendar = dates on which at least half of the board universe has a price (M10 rule;
  production uses SPY dates) — the script asserts both calendars agree on every window it uses;
- three-state split resolution (plan §8.3 rule 2), with its own code;
- window returns, unknown reasons, baselines, five tiers, Q1−Q5, Spearman IC, ICIR, overlap lag,
  Newey-West intervals, 12-1 momentum Top 40, overlap, HHI, turnover, BH over the fixed family.
A7b: a second split classifier based on the whole event-window close ratio, plus a sensitivity run.

Usage: verify_health.py --db prosperity.db --market-db market.db --summary summary.json --out DIR
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
from pathlib import Path

import numpy as np

SCHEME = 'F1'
H_DAYS = 63
TOP = 40
SLACK = 5
W = 5
TOL = 1e-9
INTERVAL_KEYS = ('t', 'p', 'se', 'lo', 'hi')     # second review #3: intervals and raw p are recomputed


def connect(path):
    return sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)


# ------------------------------------------------------------------ boards (raw SQL)

def load_boards(pdb, code_version, scheme_hash):
    out = []
    for bid, as_of, frozen_at in pdb.execute(
            "SELECT board_id, as_of, frozen_at FROM board WHERE board_type='quarter_final' AND scheme_hash=? "
            "AND code_version=? ORDER BY as_of", (scheme_hash, code_version)):
        rows = []
        for sym, status, grade, rank in pdb.execute(
                'SELECT symbol, status, grade, rank FROM score WHERE board_id=? ORDER BY row_index', (bid,)):
            rows.append({'symbol': sym, 'status': status, 'grade': grade, 'rank': rank})
        sectors = dict(pdb.execute('SELECT symbol, sector FROM stock_period WHERE board_id=?', (bid,)))
        for r in rows:
            r['sector'] = sectors.get(r['symbol']) or '未知'
        out.append({'as_of': as_of, 'frozen_at': frozen_at, 'rows': rows})
    return out


# ------------------------------------------------------------------ prices

def load_prices(mdb, symbols):
    closes, splits = {}, {}
    syms = sorted(symbols)
    for i in range(0, len(syms), 400):
        b = syms[i:i + 400]
        q = ','.join('?' * len(b))
        for s, d, c in mdb.execute(f'SELECT symbol,date,close FROM daily_price WHERE symbol IN ({q}) AND close>0 '
                                   'ORDER BY symbol,date', b):
            closes.setdefault(s, ([], []))
            closes[s][0].append(d)
            closes[s][1].append(c)
        for s, d, n, den in mdb.execute(f'SELECT symbol,date,numerator,denominator FROM fmp_stock_splits '
                                        f'WHERE symbol IN ({q})', b):
            splits.setdefault(s, []).append((d, n / den))
    return {s: (d, np.array(c, float)) for s, (d, c) in closes.items()}, splits


def half_member_calendar(mdb, members):
    """Dates where at least half of `members` have a price row (M10's rule)."""
    syms = sorted(members)
    counts = {}
    for i in range(0, len(syms), 400):
        b = syms[i:i + 400]
        q = ','.join('?' * len(b))
        for d, n in mdb.execute(f'SELECT date, COUNT(*) FROM daily_price WHERE symbol IN ({q}) AND close>0 '
                                'GROUP BY date', b):
            counts[d] = counts.get(d, 0) + n
    return sorted(d for d, n in counts.items() if 2 * n >= len(syms))


def classify_step(dates, raw, lo, hi, f):
    """Plan §8.3 rule 2, own implementation: one big step that matches f."""
    idx = [j for j in range(1, len(dates)) if lo <= dates[j] <= hi]
    if not idx:
        return 'unresolved', None
    d = abs(math.log(f))
    big = [j for j in idx if abs(math.log(raw[j] / raw[j - 1])) >= d / 2]
    if not big:
        return 'already_adjusted', None
    if max(f, 1 / f) >= 1.2 and len(big) == 1 and abs(math.log(raw[big[0]] / raw[big[0] - 1] * f)) < min(0.15, d / 2):
        return 'adjusted', big[0]
    return 'unresolved', None


def classify_window(dates, raw, lo, hi, f):
    """A7b: whole-window close ratio R = last close in window / last close before it; no per-day threshold."""
    a = bisect.bisect_left(dates, lo) - 1
    b = bisect.bisect_right(dates, hi) - 1
    if a < 0 or b <= a:
        return 'unresolved', None
    R = raw[b] / raw[a]
    d = abs(math.log(f))
    if abs(math.log(R)) < d / 3:
        return 'already_adjusted', None
    if max(f, 1 / f) >= 1.2 and abs(math.log(R * f)) < d / 3:
        jumps = [(abs(math.log(raw[j] / raw[j - 1] * f)), j) for j in range(a + 1, b + 1)]
        return 'adjusted', min(jumps)[1]
    return 'unresolved', None


class Book:
    def __init__(self, closes, splits, cal, classifier, override=None):
        self.cal = cal
        self.series, self.unres, self.events = {}, {}, {}
        for s, (dates, raw) in closes.items():
            adj = raw.copy()
            done = []
            for fd, f in sorted(splits.get(s, ())):
                if f == 1 or fd < cal[0] or fd > cal[-1]:
                    continue
                k = bisect.bisect_left(cal, fd)
                lo, hi = cal[max(0, k - W)], cal[min(len(cal) - 1, k + W)]
                state, j = classifier(dates, raw, lo, hi, f)
                if override and (s, fd) in override:
                    state, j = override[(s, fd)]
                if state == 'adjusted' and any(j == jj for jj, _ in done):
                    state, j = 'already_adjusted', None      # one jump, one adjustment (second review #4)
                self.events[(s, fd)] = (f, state, j, lo, hi)
                if state == 'adjusted':
                    done.append((j, f))
                elif state == 'unresolved':
                    self.unres.setdefault(s, []).append((lo, hi))
            for j, f in sorted(done, reverse=True):
                adj[:j] /= f
            suspects = []
            for j in range(1, len(dates)):
                r = adj[j] / adj[j - 1]
                if 0.55 <= r <= 1.8:
                    continue
                if any(abs(r * k - 1) < 0.03 or abs(r / k - 1) < 0.03 for k in (2, 3, 4, 5, 8, 10, 15, 20)):
                    suspects.append(dates[j])
            self.series[s] = (dates, adj, suspects)

    def close_at(self, s, i):
        if s not in self.series:
            return None
        dates, adj, _ = self.series[s]
        j = bisect.bisect_right(dates, self.cal[i]) - 1
        return float(adj[j]) if j >= 0 and dates[j] >= self.cal[max(0, i - SLACK)] else None

    def ret(self, s, a, b):
        if s not in self.series:
            return None, 'no_price'
        dates, adj, suspects = self.series[s]
        j = bisect.bisect_left(dates, self.cal[a])
        if j >= len(dates) or dates[j] != self.cal[a]:
            return None, 'no_start_price'
        if dates[-1] < self.cal[b]:
            return None, 'ended_early'
        end = self.close_at(s, b)
        if end is None:
            return None, 'price_gap'
        if any(lo <= self.cal[b] and hi > self.cal[a] for lo, hi in self.unres.get(s, ())):
            return None, 'split_unresolved'
        if any(self.cal[a] < d <= self.cal[b] for d in suspects):
            return None, 'split_suspect'
        return end / float(adj[j]) - 1, 'ok'

    def blocked(self, s, a, b):
        _, _, suspects = self.series.get(s, (None, None, []))
        return (any(lo <= self.cal[b] and hi > self.cal[a] for lo, hi in self.unres.get(s, ()))
                or any(self.cal[a] < d <= self.cal[b] for d in suspects))


# ------------------------------------------------------------------ statistics (own code)

def rankdata(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    ranks = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x, y):
    rx, ry = np.array(rankdata(x)), np.array(rankdata(y))
    rx, ry = rx - rx.mean(), ry - ry.mean()
    return float((rx @ ry) / math.sqrt((rx @ rx) * (ry @ ry)))


def nw_t(xs, lag):
    x = np.array(xs, float)
    n = len(x)
    lag = max(0, min(lag, n - 1))
    c = x - x.mean()
    v = c @ c / n
    for k in range(1, lag + 1):
        v += 2 * (1 - k / (lag + 1)) * (c[k:] @ c[:-k]) / n
    if v <= 1e-12:
        return 0.0
    return float(x.mean() / math.sqrt(v / n))


def _betacf(a, b, x):
    """Continued fraction for the regularized incomplete beta (Numerical Recipes §6.4)."""
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t, df):
    return betai(df / 2, 0.5, df / (df + t * t))


def t_quantile_975(df):
    """Solve P(|T| > q) = 0.05 by bisection on our own CDF."""
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > 0.05:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def full_stat(xs, lag):
    """n, mean, lag, and for n >= 5: t, p, se, lo, hi — the production `interval` contract."""
    stat = {'n': len(xs), 'mean': mean(xs), 'lag': lag}
    if len(xs) >= 5:
        tval = nw_t(xs, lag)
        stat['t'] = tval
        stat['p'] = 1.0 if tval == 0 else t_two_sided_p(tval, len(xs) - 1)
        if tval != 0:
            se = stat['mean'] / tval
            half = t_quantile_975(len(xs) - 1) * abs(se)
            stat.update(se=se, lo=stat['mean'] - half, hi=stat['mean'] + half)
    return stat


def lag_of(windows):
    ws = sorted(windows)
    return max((sum(1 for s, _ in ws[i + 1:] if s < e) for i, (_, e) in enumerate(ws)), default=0)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def bh(ps):
    m = len(ps)
    full = [1.0 if p is None else p for p in ps]
    order = sorted(range(m), key=lambda i: full[i])
    adj = [0.0] * m
    run = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        run = min(run, full[i] * m / rank)
        adj[i] = run
    return [None if p is None else a for p, a in zip(ps, adj)]


# ------------------------------------------------------------------ recompute

def recompute(boards, book):
    cal = book.cal
    out = {'periods': {}, 'tiers': {}, 'spread': {}, 'ic': {}, 'unknown': set(), 'lags': {}}
    tops, moms = [], []
    for b in boards:
        ranked = sorted((r for r in b['rows'] if r['status'] == 'ranked'), key=lambda r: r['rank'])
        tops.append([r['symbol'] for r in ranked if r['grade'] in ('STRICT', 'FULL')][:TOP])
        t0 = bisect.bisect_right(cal, b['as_of']) - 1
        if t0 < 252:
            moms.append(None)
        else:
            sc = []
            for r in b['rows']:
                if r['status'] == 'excluded':
                    continue
                x, y = book.close_at(r['symbol'], t0 - 252), book.close_at(r['symbol'], t0 - 21)
                if x is None or y is None or book.blocked(r['symbol'], t0 - 252, t0):
                    continue
                sc.append((-(y / x - 1), r['symbol']))
            moms.append([s for _, s in sorted(sc)[:TOP]])
    for h in (1, 2, 3):
        rows = []
        for bi, b in enumerate(boards):
            day = b['frozen_at'][:10]
            a = bisect.bisect_right(cal, day)
            if a >= len(cal) or a + H_DAYS * h >= len(cal):
                continue
            e = a + H_DAYS * h
            uni = [r for r in b['rows'] if r['status'] != 'excluded']
            ranked = sorted((r for r in uni if r['status'] == 'ranked'), key=lambda r: r['rank'])
            rets = {r['symbol']: book.ret(r['symbol'], a, e) for r in uni}
            base = mean(v for v, _ in rets.values())
            n = len(ranked)
            tier = {r['symbol']: i * 5 // n for i, r in enumerate(ranked)}
            tex = [None if base is None or mean(rets[s][0] for s, t in tier.items() if t == k) is None
                   else mean(rets[s][0] for s, t in tier.items() if t == k) - base for k in range(5)]
            spread = None if tex[0] is None or tex[4] is None else tex[0] - tex[4]
            valid = [(r['rank'], rets[r['symbol']][0]) for r in ranked if rets[r['symbol']][0] is not None]
            ic = (spearman([-k for k, _ in valid], [v for _, v in valid])
                  if len(valid) >= 10 and len({v for _, v in valid}) > 1 else None)
            for r in ranked:
                if rets[r['symbol']][0] is None:
                    out['unknown'].add((r['symbol'], b['as_of'], h, rets[r['symbol']][1]))
            rows.append({'as_of': b['as_of'], 'start': cal[a], 'end': cal[e], 'a': a, 'e': e, 'baseline': base,
                         'tier_excess': tex, 'spread': spread, 'ic': ic,
                         'returns': rets, 'bi': bi})
        out['periods'][h] = rows
        for name, getter in (('spread', lambda r: r['spread']), ('ic', lambda r: r['ic'])):
            used = [r for r in rows if getter(r) is not None]
            lag = lag_of([(r['a'], r['e']) for r in used])
            xs = [getter(r) for r in used]
            stat = full_stat(xs, lag)
            if name == 'ic' and len(xs) >= 5:
                sd = float(np.std(xs, ddof=1))
                stat['sd'] = sd
                stat['icir'] = stat['mean'] / sd if sd else None
            out[name][h] = stat
        out['tiers'][h] = [mean(r['tier_excess'][k] for r in rows) for k in range(5)]
        tier_stats = []
        for k in range(5):
            used = [r for r in rows if r['tier_excess'][k] is not None]
            tier_stats.append(full_stat([r['tier_excess'][k] for r in used], lag_of([(r['a'], r['e']) for r in used])))
        out.setdefault('tier_stats', {})[h] = tier_stats
        out['lags'][h] = lag_of([(r['a'], r['e']) for r in rows])
    out['tops'], out['moms'] = tops, moms
    out['overlap'] = [(boards[i]['as_of'], len(set(tops[i]) & set(moms[i])))
                      for i in range(len(boards)) if moms[i] is not None]
    hhi = []
    for i, b in enumerate(boards):
        sec = {r['symbol']: r['sector'] for r in b['rows']}
        cnt = {}
        for s in tops[i]:
            cnt[sec[s]] = cnt.get(sec[s], 0) + 1
        hhi.append(sum((c / len(tops[i])) ** 2 for c in cnt.values()) if tops[i] else None)
    out['hhi'] = hhi
    out['turnover'] = [1 - len(set(tops[i - 1]) & set(tops[i])) / len(set(tops[i - 1])) for i in range(1, len(tops))]
    return out


# ------------------------------------------------------------------ compare

class Diff:
    def __init__(self):
        self.lines, self.checked = [], 0

    def num(self, label, ours, theirs):
        self.checked += 1
        if ours is None or theirs is None:
            if ours is not theirs and not (ours is None and theirs is None):
                self.lines.append(f'{label}: ours={ours} summary={theirs}')
            return
        if abs(ours - theirs) > TOL:
            self.lines.append(f'{label}: ours={ours!r} summary={theirs!r} diff={ours - theirs:.3e}')

    def eq(self, label, ours, theirs):
        self.checked += 1
        if ours != theirs:
            self.lines.append(f'{label}: ours={ours!r} summary={theirs!r}')


def compare(ours, rep, boards, book, prod_events):
    d = Diff()
    tiers, ic, ov, conc, turn = (rep['chapters'][k] for k in ('分层前瞻收益', 'IC', '与价格动量的重叠度', '行业集中度', '换手率'))
    for h in (1, 2, 3):
        mine = {r['as_of']: r for r in ours['periods'][h]}
        theirs = {p['as_of']: p for p in tiers[str(h)]['periods'] if p['status'] == 'complete'}
        d.eq(f'h{h} complete as_of', sorted(mine), sorted(theirs))
        for a in sorted(set(mine) & set(theirs)):
            m, t = mine[a], theirs[a]
            d.eq(f'h{h} {a} start/end', (m['start'], m['end']), (t['start'], t['end']))
            d.num(f'h{h} {a} baseline', m['baseline'], t['baseline'])
            for k in range(5):
                d.num(f'h{h} {a} Q{k + 1}', m['tier_excess'][k], t['tier_excess'][k])
            d.num(f'h{h} {a} spread', m['spread'], t['spread'])
            d.num(f'h{h} {a} ic', m['ic'], t['ic'])
        for k in range(5):
            d.num(f'h{h} tier Q{k + 1} mean', ours['tiers'][h][k], tiers[str(h)]['tiers'][k]['mean'])
            for key in INTERVAL_KEYS:
                d.num(f'h{h} tier Q{k + 1} {key}', ours['tier_stats'][h][k].get(key),
                      tiers[str(h)]['tiers'][k].get(key))
        for name, theirs_stat in (('spread', tiers[str(h)]['spread']), ('ic', ic[str(h)])):
            mine_stat = ours[name][h]
            d.eq(f'h{h} {name} n', mine_stat['n'], theirs_stat['n'])
            d.eq(f'h{h} {name} lag', mine_stat['lag'], theirs_stat['lag'])
            d.num(f'h{h} {name} mean', mine_stat['mean'], theirs_stat['mean'])
            for key in INTERVAL_KEYS:
                d.num(f'h{h} {name} {key}', mine_stat.get(key), theirs_stat.get(key))
        d.num(f'h{h} ICIR', ours['ic'][h].get('icir'), ic[str(h)].get('icir'))
        d.num(f'h{h} IC sd', ours['ic'][h].get('sd'), ic[str(h)].get('sd'))
    theirs_unknown = {(u['symbol'], u['as_of'], u['horizon'], u['reason']) for u in rep['appendix']['unknown']}
    d.eq('unknown list', sorted(ours['unknown']), sorted(theirs_unknown))
    d.eq('overlap', ours['overlap'], [(p['as_of'], p['overlap']) for p in ov['periods']])
    d.eq('momentum Top 40', [m for m in ours['moms'] if m is not None], [p['momentum'] for p in ov['periods']])
    d.eq('Top 40', [t for i, t in enumerate(ours['tops']) if ours['moms'][i] is not None],
         [p['top'] for p in ov['periods']])
    for (a, x), p in zip([(b['as_of'], h) for b, h in zip(boards, ours['hhi'])], conc['periods']):
        d.num(f'{a} HHI', x, p['hhi'])
    for x, p in zip(ours['turnover'], turn['quarterly']):
        d.num(f'{p["as_of"]} turnover', x, p['turnover'])
    mine_events = sorted((s, fd, st) for (s, fd), (f, st, j, lo, hi) in book.events.items())
    d.eq('split states', mine_events, sorted((x['symbol'], x['fmp_date'], x['state']) for x in prod_events))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--market-db', required=True)
    ap.add_argument('--summary', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    out = Path(a.out)
    summary = json.loads(Path(a.summary).read_text())
    rep = summary['schemes'][SCHEME]
    pdb, mdb = connect(a.db), connect(a.market_db)
    boards = load_boards(pdb, rep['header']['code_version'], rep['header']['scheme_hash'])
    members = {r['symbol'] for b in boards for r in b['rows'] if r['status'] != 'excluded'}
    spy = [d for (d,) in mdb.execute("SELECT date FROM daily_price WHERE symbol='SPY' AND close>0 ORDER BY date")]
    cal = [d for d in half_member_calendar(mdb, members) if d >= spy[0]]
    cal_note = ('calendars identical' if cal == spy else
                f'calendar differs: half-member {len(cal)} days vs SPY {len(spy)} days; '
                f'only in half-member {sorted(set(cal) - set(spy))[:10]}, only in SPY {sorted(set(spy) - set(cal))[:10]}')
    closes, splits = load_prices(mdb, members)

    book = Book(closes, splits, cal, classify_step)
    ours = recompute(boards, book)
    d = compare(ours, rep, boards, book, rep['appendix']['splits'])
    fam = summary['bh_family']
    tests, ps = [], []
    for sid, r in summary['schemes'].items():
        for h in (1, 2, 3):
            pairs = ((r['chapters']['分层前瞻收益'][str(h)]['spread'], ours['spread'][h]),
                     (r['chapters']['IC'][str(h)], ours['ic'][h]))
            for t, mine in pairs:
                tests.append(t)
                # F1: our own raw p (second review #3); other schemes are outside this check, so theirs
                ps.append(mine.get('p') if sid == SCHEME else t.get('p'))
    for t, x in zip(tests, bh(ps)):
        d.num('BH p_bh', x, t.get('p_bh'))
    d.eq('BH family size', len(tests), fam['family'])
    bh_note = (f'BH: {SCHEME} uses recomputed raw p; the other {len(summary["schemes"]) - 1} schemes use '
               'the summary raw p (not independently recomputed here)')
    lines = [f'scheme {SCHEME}; boards {len(boards)}; {cal_note}',
             f'lags 1/2/3 q: {[ours["lags"][h] for h in (1, 2, 3)]}',
             f'complete periods 1/2/3 q: {[len(ours["periods"][h]) for h in (1, 2, 3)]}',
             f'split states: ' + json.dumps({s: sum(1 for v in book.events.values() if v[1] == s)
                                              for s in ('adjusted', 'already_adjusted', 'unresolved')}),
             bh_note, f'checked {d.checked} values; differences: {len(d.lines)}', *d.lines]
    out.mkdir(parents=True, exist_ok=True)
    (out / 'verify.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines[:6]))

    # ---------------- A7b: second classifier + sensitivity
    alt = Book(closes, splits, cal, classify_window)
    disagree = sorted(k for k in book.events if k in alt.events and book.events[k][1] != alt.events[k][1])
    sens_lines = [f'A7b split classifiers disagree on {len(disagree)} records '
                  f'(production rule vs whole-window close ratio):']
    for k in disagree:
        f, st, j, lo, hi = book.events[k]
        dates, raw = closes[k[0]]
        win = [(dates[i], round(float(raw[i]), 4)) for i in range(len(dates)) if lo <= dates[i] <= hi]
        sens_lines.append(f'  {k[0]} {k[1]} factor {f:.4f}: production={st} window-ratio={alt.events[k][1]} '
                          f'prices {win}')

    def tiers_of(b):
        r = recompute(boards, b)
        return {h: (r['tiers'][h], r['spread'][h]['mean']) for h in (1, 2, 3)}

    base = tiers_of(book)
    variants = {
        'disagreements all unknown': {k: ('unresolved', None) for k in disagree},
        'disagreements per window-ratio rule': {k: alt.events[k][1:3] for k in disagree},
    }
    for name, override in variants.items():
        v = tiers_of(Book(closes, splits, cal, classify_step, override=override))
        worst = max(abs(x - y) for h in (1, 2, 3) for x, y in zip(v[h][0], base[h][0]) if x is not None and y is not None)
        sp = max(abs(v[h][1] - base[h][1]) for h in (1, 2, 3) if v[h][1] is not None and base[h][1] is not None)
        sens_lines.append(f'{name}: max |Δ tier mean excess| = {worst * 100:.4f} pp; '
                          f'max |Δ Q1−Q5 mean| = {sp * 100:.4f} pp')
    (out / 'split-sensitivity.txt').write_text('\n'.join(sens_lines) + '\n')
    print('\n'.join(sens_lines[-2:]))
    return 0 if not d.lines else 1


if __name__ == '__main__':
    raise SystemExit(main())
