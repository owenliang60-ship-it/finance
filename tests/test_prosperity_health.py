"""M12 health report: prices, per-board evaluation, structure, controls."""
import pytest

from tests.prosperity_fixtures import weekdays

CAL = weekdays('2027-12-31', 600)


def book(closes, splits=None):
    from backtest.research.prosperity_health import PriceBook
    return PriceBook(closes=closes, splits=splits or {}, calendar=CAL)


def rising(base=10.0):
    return [(d, base + k) for k, d in enumerate(CAL)]


def test_start_is_first_trading_day_after_the_freeze_date():
    b = book({'A': rising()})
    assert CAL[b.start_index('2026-09-27T00:00:00Z')] == '2026-09-28'
    assert CAL[b.start_index('2026-09-26T08:30:00Z')] == '2026-09-28'
    assert b.start_index(CAL[-1] + 'T08:30:00Z') is None


def test_window_return_and_unknown_reasons():
    s = CAL.index('2026-09-28')
    e = s + 63
    full = rising()
    gap = [p for p in full if not (CAL[e - 10] <= p[0] <= CAL[e])]
    b = book({'A': full, 'B': full[:e - 3], 'C': gap, 'D': full[s + 1:], 'E': []})
    ret, why = b.window_return('A', s, e)
    assert why == 'ok' and ret == pytest.approx(full[e][1] / full[s][1] - 1)
    assert b.window_return('B', s, e) == (None, 'ended_early')
    assert b.window_return('C', s, e) == (None, 'price_gap')
    assert b.window_return('D', s, e) == (None, 'no_start_price')
    assert b.window_return('E', s, e) == (None, 'no_price')
    assert b.window_return('ZZZ', s, e) == (None, 'no_price')


def test_recorded_split_matched_within_five_days_small_factor_left_alone():
    k = [(d, 100.0) for d in CAL[:250]] + [(d, 10.0) for d in CAL[250:]]
    g = [(d, 50.0) for d in CAL]
    b = book({'K': k, 'G': g}, {'K': [(CAL[254], 10.0)], 'G': [(CAL[100], 1.253)]})
    assert b.window_return('K', 240, 260) == (pytest.approx(0.0), 'ok')
    assert b.window_return('G', 90, 110) == (pytest.approx(0.0), 'ok')
    assert b.adjustments == (('K', CAL[250], 10.0),)
    assert [e[3] for e in b.split_events] == ['already_adjusted', 'adjusted']


def test_reverse_split_and_already_adjusted_series():
    r = [(d, 10.0) for d in CAL[:250]] + [(d, 30.0) for d in CAL[250:]]
    flat = [(d, 10.0) for d in CAL]
    b = book({'R': r, 'N': flat}, {'R': [(CAL[247], 1 / 3)], 'N': [(CAL[200], 10.0)]})
    assert b.window_return('R', 240, 260) == (pytest.approx(0.0), 'ok')
    assert b.window_return('N', 190, 210) == (pytest.approx(0.0), 'ok')
    assert b.adjustments == (('R', CAL[250], pytest.approx(1 / 3)),)


def test_five_for_four_split_is_adjusted():
    """Review P1 case 2: a 1.25 record with a matching 100→80 step; true price return is 0."""
    p = [(d, 100.0) for d in CAL[:250]] + [(d, 80.0) for d in CAL[250:]]
    b = book({'P': p}, {'P': [(CAL[250], 1.25)]})
    assert b.window_return('P', 240, 260) == (pytest.approx(0.0), 'ok')
    assert b.adjustments == (('P', CAL[250], 1.25),)


def test_unmatched_or_ambiguous_records_are_unresolved_not_assumed_adjusted():
    """Review P1 case 1: a 2:1 record with 100→40 is a real break, never 'already adjusted'."""
    two = [(d, 100.0) for d in CAL[:250]] + [(d, 40.0) for d in CAL[250:]]
    multi = ([(d, 100.0) for d in CAL[:248]] + [(d, 50.0) for d in CAL[248:249]]
             + [(d, 100.0) for d in CAL[249:251]] + [(d, 50.0) for d in CAL[251:]])
    small = [(d, 100.0) for d in CAL[:250]] + [(d, 90.0) for d in CAL[250:]]
    b = book({'T': two, 'M': multi, 'S': small},
             {'T': [(CAL[250], 2.0)], 'M': [(CAL[250], 2.0)], 'S': [(CAL[250], 1.05)]})
    assert b.adjustments == ()
    assert [e[3] for e in b.split_events] == ['unresolved'] * 3
    for s in ('T', 'M', 'S'):
        assert b.window_return(s, 240, 260) == (None, 'split_unresolved')
    assert b.window_return('T', 200, 240)[1] == 'ok'            # window ends before the event window
    assert b.window_return('T', 256, 300) == (pytest.approx(0.0), 'ok')   # starts after it


def test_unexplained_split_like_jump_is_unknown_and_large_moves_are_listed():
    q = [(d, 60.0) for d in CAL[:250]] + [(d, 12.0) for d in CAL[250:]]
    v = [(d, 100.0) for d in CAL[:250]] + [(d, 40.0) for d in CAL[250:]]
    b = book({'Q': q, 'V': v})
    assert b.window_return('Q', 240, 260) == (None, 'split_suspect')
    assert b.window_return('Q', 251, 270)[1] == 'ok'
    assert b.window_return('V', 240, 260) == (pytest.approx(-0.6), 'ok')
    assert b.jumps('V', 240, 260) == [{'date': CAL[250], 'ratio': pytest.approx(0.4), 'kind': 'large_move'}]


def row(symbol, rank=None, *, status='ranked', grade='FULL', sector='Technology', score=None,
        pit='approximate', beta=None, breakdown=None):
    from backtest.research.prosperity_health import Row
    return Row(symbol=symbol, status=status, grade=grade if status == 'ranked' else None, rank=rank,
               score=score if score is not None else (100.0 - rank if rank else None), sector=sector,
               pit_basis=pit, beta=beta, breakdown=breakdown or {}, family_scores={}, badges=())


def view(rows, *, as_of='2026-09-26', frozen_at='2026-09-27T00:00:00Z', board_type='quarter_final',
         mode='replay'):
    from backtest.research.prosperity_health import BoardView
    return BoardView(scheme_id='F1', scheme_hash='h', code_version='v', as_of=as_of, quarter_label='2026Q2',
                     frozen_at=frozen_at, board_type=board_type, mode=mode, rows=tuple(rows))


def stepped(r, s):
    """Flat at 10 through calendar index s, then 10 × (1 + r)."""
    return [(d, 10.0 if k <= s else 10.0 * (1 + r)) for k, d in enumerate(CAL)]


def test_tiers_are_fixed_by_rank():
    from backtest.research.prosperity_health import assign_tiers
    assert list(assign_tiers(list('abcdefghij')).values()) == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert list(assign_tiers(list('abcdefg')).values()) == [0, 0, 1, 2, 2, 3, 4]


def test_board_tiers_baseline_excess_ic_and_top():
    from backtest.research.prosperity_health import evaluate_board
    s = CAL.index('2026-09-28')
    rets = {f'R{i}': (10 - i) / 100 for i in range(10)}
    rets.update(O1=0.0, O2=0.0)
    closes = {k: stepped(v, s) for k, v in rets.items()}
    closes['X'] = stepped(0.5, s)
    rows = [row(f'R{i}', i + 1) for i in range(10)]
    rows += [row('O1', status='observe'), row('O2', status='observe'), row('X', status='excluded')]
    out = evaluate_board(view(rows), book(closes), horizon=1)
    base = sum(rets.values()) / 12
    assert out.status == 'complete' and out.start == '2026-09-28' and out.end == CAL[s + 63]
    assert out.baseline == pytest.approx(base) and 'X' not in out.returns
    assert out.tier_excess[0] == pytest.approx(0.095 - base)
    assert out.tier_excess[4] == pytest.approx(0.015 - base)
    assert out.spread == pytest.approx(0.08) and out.ic == pytest.approx(1.0)
    assert out.top == tuple(f'R{i}' for i in range(10))
    assert out.top_excess == pytest.approx(0.055 - base)
    assert out.coverage == {'ranked': (10, 10), 'universe': (12, 12)}


def test_unknown_return_keeps_its_tier_and_ic_needs_ten():
    from backtest.research.prosperity_health import evaluate_board
    s = CAL.index('2026-09-28')
    closes = {f'R{i}': stepped((10 - i) / 100, s) for i in range(1, 10)}
    rows = [row(f'R{i}', i + 1) for i in range(10)]
    out = evaluate_board(view(rows), book(closes), horizon=1)
    assert out.tiers['R0'] == 0 and out.returns['R0'] == (None, 'no_price')
    assert out.tier_excess[0] == pytest.approx(0.09 - out.baseline)
    assert out.ic is None and out.coverage['ranked'] == (9, 10)


def test_unfinished_window_is_in_progress_and_future_freeze_not_started():
    from backtest.research.prosperity_health import evaluate_board
    s = len(CAL) - 100
    closes = {f'R{i}': stepped(0.01 * i, s) for i in range(10)}
    rows = [row(f'R{i}', i + 1) for i in range(10)]
    out = evaluate_board(view(rows, frozen_at=CAL[s - 1] + 'T08:30:00Z'), book(closes), horizon=3)
    assert out.status == 'in_progress' and out.start == CAL[s] and out.end == CAL[-1]
    later = evaluate_board(view(rows, frozen_at=CAL[-1] + 'T08:30:00Z'), book(closes), horizon=1)
    assert later.status == 'not_started' and later.tier_excess == {} and later.start is None


def test_overlap_lag_counts_actual_overlap_on_uneven_starts():
    """Review P2: freezes 50 trading days apart still overlap a 63-day window."""
    from backtest.research.prosperity_health import overlap_lag
    starts = [0, 63, 113, 189, 237]                  # gaps 63, 50, 76, 48
    assert overlap_lag([(s, s + 63) for s in starts]) == 1
    assert overlap_lag([(s, s + 126) for s in starts]) == 2
    assert overlap_lag([(s, s + 189) for s in starts]) == 3
    assert overlap_lag([(s, s + 63) for s in (0, 63, 126)]) == 0
    assert overlap_lag([]) == 0


def test_interval_uses_newey_west_with_the_given_lag():
    from scipy.stats import t as t_dist
    from backtest.pipeline.primitives.evaluation import newey_west_tstat
    from backtest.research.prosperity_health import interval
    xs = [0.02, -0.01, 0.03, 0.01, 0.00, 0.04, -0.02, 0.02]
    t, p = newey_west_tstat(xs, 2)
    mean = sum(xs) / len(xs)
    se = mean / t
    half = t_dist.ppf(0.975, len(xs) - 1) * se
    assert interval(xs, lag=2) == pytest.approx(
        {'n': 8, 'mean': mean, 'se': se, 'lo': mean - half, 'hi': mean + half, 't': t, 'p': p, 'lag': 2})
    assert interval(xs[:4], lag=0) == {'n': 4, 'mean': pytest.approx(sum(xs[:4]) / 4), 'lag': 0}
    assert interval([], lag=0) == {'n': 0, 'mean': None, 'lag': 0}


def test_icir_is_mean_over_sample_sd_and_not_annualized():
    import statistics
    from backtest.research.prosperity_health import ic_summary
    ics = [0.05, 0.02, -0.01, 0.04, 0.03, 0.06]
    out = ic_summary(ics, lag=1)
    assert out['sd'] == pytest.approx(statistics.stdev(ics))
    assert out['icir'] == pytest.approx(statistics.fmean(ics) / statistics.stdev(ics))
    assert out['lag'] == 1 and out['n'] == 6
    assert ic_summary([0.03] * 6, lag=1)['icir'] is None
    assert ic_summary(ics[:4], lag=1)['icir'] is None


def test_monotonic_requires_strictly_falling_complete_tiers():
    from backtest.research.prosperity_health import monotonic
    assert monotonic([0.03, 0.01, 0.0, -0.01, -0.02])
    assert not monotonic([0.03, 0.01, 0.01, -0.01, -0.02])
    assert not monotonic([0.03, None, 0.0, -0.01, -0.02])


def test_board_view_reads_a_stored_board():
    from backtest.research.prosperity_health import board_view
    from tests.prosperity_fixtures import storage_batch
    stored = next(b for b in storage_batch(freeze=True) if b.meta.board_type == 'quarter_final'
                  and b.board.scheme_id == 'F1')
    v = board_view(stored)
    assert v.board_type == 'quarter_final' and v.frozen_at == stored.meta.frozen_at
    assert [r.symbol for r in v.rows] == [r.symbol for r in stored.board.rows]
    ranked = [r for r in v.rows if r.status == 'ranked']
    first = ranked[0]
    src = next(r for r in stored.board.rows if r.symbol == first.symbol)
    assert first.rank == src.rank and first.grade == src.grade
    assert set(first.breakdown) == set(src.z) | set(src.missing)


def test_constant_returns_give_no_ic():
    import warnings
    from backtest.research.prosperity_health import evaluate_board
    s = CAL.index('2026-09-28')
    closes = {f'R{i}': stepped(0.02, s) for i in range(12)}
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        out = evaluate_board(view([row(f'R{i}', i + 1) for i in range(12)]), book(closes), horizon=1)
    assert out.ic is None and out.spread == pytest.approx(0.0)


def test_top_concentration_and_turnover():
    from backtest.research.prosperity_health import concentration, top_symbols, turnover
    rows = [row(f'S{i}', i + 1, sector='Energy' if i < 30 else 'Technology') for i in range(50)]
    rows[5] = row('S5', 6, grade='BELOW')
    top = top_symbols(view(rows))
    assert len(top) == 40 and 'S5' not in top and top[-1] == 'S40'
    c = concentration(view(rows), top)
    assert c == pytest.approx({'top_sector': 'Energy', 'max_share': 29 / 40,
                               'hhi': (29 / 40) ** 2 + (11 / 40) ** 2})
    assert turnover(top, top[10:]) == pytest.approx(10 / 40)


def test_failures_are_worst_strict_three_quarter_returns_with_breakdown():
    from backtest.research.prosperity_health import evaluate_board, failure_cases
    s = CAL.index('2026-09-28')
    br = {'revenue_accel': {'raw': 0.3, 'z': 1.2, 'weight': 0.22, 'contribution': 0.264}}
    rows = [row('W', 1, grade='STRICT', breakdown=br), row('F', 2, grade='FULL'), row('G', 3, grade='STRICT')]
    rows += [row(f'R{i}', i + 4) for i in range(7)]
    rets = {'W': -0.30, 'F': -0.40, 'G': 0.10, **{f'R{i}': 0.0 for i in range(7)}}
    v = view(rows)
    res = evaluate_board(v, book({k: stepped(x, s) for k, x in rets.items()}), horizon=3)
    out = failure_cases([(v, res)], limit=10)
    assert [f['symbol'] for f in out] == ['W', 'G']
    assert out[0]['return'] == pytest.approx(-0.30) and out[0]['breakdown'] == br
    assert out[0]['as_of'] == '2026-09-26' and out[0]['excess'] == pytest.approx(-0.30 - res.baseline)


def test_expectation_factors_are_unknown_with_period_count():
    from backtest.research.prosperity_health import short_history
    from terminal.prosperity.schemes import SCHEMES
    z = {'surprise': {'raw': 0.1, 'z': 0.5, 'weight': 0.05, 'contribution': 0.025},
         'revision': {'raw': None, 'z': None, 'weight': None, 'contribution': None}}
    nz = {'surprise': {'raw': None, 'z': None, 'weight': None, 'contribution': None}}
    old = view([row('A', 1, breakdown=z), row('B', 2, breakdown=nz)], as_of='2026-05-30')
    new = view([row('A', 1, breakdown=nz), row('B', 2, breakdown=nz)], as_of='2026-08-29')
    assert short_history([old, new], SCHEMES['F1']) == [
        {'factor': 'surprise', 'periods': 1, 'latest_coverage': 0.0, 'verdict': 'UNKNOWN'},
        {'factor': 'revision', 'periods': 0, 'latest_coverage': 0.0, 'verdict': 'UNKNOWN'}]
    assert short_history([old, new], SCHEMES['F0']) == []


def test_momentum_12_1_skips_last_month_and_needs_a_year():
    from backtest.research.prosperity_health import momentum_top
    t0 = len(CAL) - 60
    closes = {f'M{i:02d}': [(d, 10.0 if k < t0 - 200 else 10.0 * (1 + i / 100)) for k, d in enumerate(CAL)]
              for i in range(45)}
    closes['SPIKE'] = [(d, 10.0 if k < t0 - 10 else 17.0) for k, d in enumerate(CAL)]
    rows = [row(s, k + 1) for k, s in enumerate(sorted(closes))]
    top = momentum_top(view(rows, as_of=CAL[t0]), book(closes))
    assert top == tuple(f'M{i:02d}' for i in range(44, 4, -1))
    assert momentum_top(view(rows, as_of=CAL[200]), book(closes)) is None


def quarter_rows(eps, accepted):
    """Six fiscal quarters 2025Q1..2026Q2, newest first, as MarketStore.get_income returns them."""
    ends = ['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30']
    labels = [('2025', 'Q1'), ('2025', 'Q2'), ('2025', 'Q3'), ('2025', 'Q4'), ('2026', 'Q1'), ('2026', 'Q2')]
    income = [{'date': d, 'fiscal_year': fy, 'period': p, 'eps_diluted': e, 'revenue': 100.0 + k,
               'net_income': 10.0 + k, 'accepted_date': a + ' 16:00:00', 'filing_date': a}
              for k, (d, (fy, p), e, a) in enumerate(zip(ends, labels, eps, accepted))]
    metrics = [{'date': d, 'revenue_cagr_4q': 0.2, 'net_income_cagr_4q': 0.2} for d in ends]
    return {'income': income[::-1], 'metrics': metrics[::-1]}


ACCEPTED = ['2025-05-01', '2025-08-01', '2025-11-01', '2026-02-20', '2026-05-01', '2026-08-05']


def test_premium_recomputed_point_in_time_with_archived_beta():
    from backtest.research.prosperity_health import premium_members
    funds = {'A': quarter_rows([1.0, 1.0, 1.0, 1.0, 1.0, 1.5], ACCEPTED),
             'B': quarter_rows([1.0, 1.0, 1.0, 1.0, 1.0, 1.5], ACCEPTED)}
    rows = [row('A', 1, beta=1.5), row('B', 2, beta=1.2)]
    after = premium_members(view(rows, as_of='2026-08-08'), funds, as_of='2026-08-08')
    assert after['members'] == ('A',) and after['basis'] == 'approximate'
    before = premium_members(view(rows, as_of='2026-07-31'), funds, as_of='2026-07-31')
    assert before['members'] == ()


def test_bh_uses_the_fixed_family_and_keeps_missing_tests_unknown():
    """Review P2: one estimated p = .04 in a family of 12 → .48, not .04."""
    from backtest.research.prosperity_health import bh_adjust
    out = bh_adjust([0.04] + [None] * 11)
    assert out[0] == pytest.approx(0.48) and out[1:] == [None] * 11
    assert bh_adjust([0.01, 0.04, None]) == [pytest.approx(0.03), pytest.approx(0.06), None]


def test_scheme_report_and_bh_family():
    from backtest.research.prosperity_health import CHAPTERS, apply_bh, scheme_report
    s = CAL.index('2026-09-28')
    closes = {f'R{i}': stepped((10 - i) / 100, s) for i in range(10)}
    views = [view([row(f'R{i}', i + 1) for i in range(10)])]
    rep = scheme_report(views, [], book(closes), {})
    assert rep['header']['periods'] == 1 and rep['header']['complete'] == {'1': 1, '2': 1, '3': 1}
    assert list(rep['chapters']) == list(CHAPTERS)
    assert rep['chapters']['换手率']['weekly'] == {'n': 0}
    assert rep['chapters']['对照组']['common_periods'] == {'1': 0, '2': 0, '3': 0}   # no fundamentals: Premium empty
    assert set(rep['chapters']['IC']['3']) >= {'mean', 'sd', 'icir', 'lag', 'n'}
    bh = apply_bh({'F1': rep, 'F0': scheme_report(views, [], book(closes), {})})
    assert bh == {'family': 12, 'estimated': 0}


def test_control_chapter_uses_common_periods_only():
    """Momentum is missing for the early board, so it is left out of every control row."""
    from backtest.research.prosperity_health import scheme_report
    s0 = 261                                   # momentum needs 252 days; Premium needs a quarter ≤ 200 days old
    early, late = CAL[s0 - 1], CAL[s0 + 63 - 1]
    closes = {f'R{i}': [(d, 10.0 * (1 + 0.001 * i) ** k) for k, d in enumerate(CAL)] for i in range(45)}
    funds = {s: quarter_rows([1.0, 1.0, 1.0, 1.0, 1.0, 1.5], ACCEPTED) for s in closes}
    rows = [row(s, k + 1, beta=1.5) for k, s in enumerate(sorted(closes))]
    views = [view(rows, as_of=CAL[100], frozen_at=CAL[100] + 'T08:30:00Z'),
             view(rows, as_of=early, frozen_at=early + 'T08:30:00Z'),
             view(rows, as_of=late, frozen_at=late + 'T08:30:00Z')]
    rep = scheme_report(views, [], book(closes), funds)
    ctrl = rep['chapters']['对照组']
    assert ctrl['common_periods']['1'] == 2 and ctrl['common_range']['1'] == [early, late]
    h1 = ctrl['horizons']['1']
    assert h1['top']['excess']['n'] == h1['momentum']['excess']['n'] == h1['premium']['excess']['n'] == 2
    assert rep['chapters']['分层前瞻收益']['1']['spread']['n'] == 3


def test_overlap_chapter_lists_both_top_40s_for_independent_checks():
    from backtest.research.prosperity_health import scheme_report
    s0 = 261
    closes = {f'R{i:02d}': [(d, 10.0 * (1 + 0.001 * i) ** k) for k, d in enumerate(CAL)] for i in range(45)}
    rows = [row(s, k + 1) for k, s in enumerate(sorted(closes))]
    v = view(rows, as_of=CAL[s0 - 1], frozen_at=CAL[s0 - 1] + 'T08:30:00Z')
    period = scheme_report([v], [], book(closes), {})['chapters']['与价格动量的重叠度']['periods'][0]
    assert period['top'] == [f'R{i:02d}' for i in range(40)]
    assert period['momentum'] == [f'R{i:02d}' for i in range(44, 4, -1)]
    assert period['overlap'] == 35


def test_control_chapter_reports_premium_size_and_coverage_per_period():
    """Plan §8.5: the Premium gate is set to 0 for the look-back, so the actual coverage is reported."""
    from backtest.research.prosperity_health import scheme_report
    s0 = 261
    closes = {f'R{i:02d}': [(d, 10.0 * (1 + 0.001 * i) ** k) for k, d in enumerate(CAL)] for i in range(45)}
    funds = {s: quarter_rows([1.0, 1.0, 1.0, 1.0, 1.0, 1.5], ACCEPTED) for s in list(closes)[:9]}
    rows = [row(s, k + 1, beta=1.5) for k, s in enumerate(sorted(closes))]
    v = view(rows, as_of=CAL[s0 - 1], frozen_at=CAL[s0 - 1] + 'T08:30:00Z')
    periods = scheme_report([v], [], book(closes), funds)['chapters']['对照组']['premium_periods']
    assert periods == [{'as_of': CAL[s0 - 1], 'members': 9, 'fundamental_ready': pytest.approx(9 / 45),
                        'beta_ready': pytest.approx(1.0)}]


def test_premium_cut_rejects_placeholder_dates_like_the_engine():
    """Review #1: an accepted/filing date on the fiscal end is a placeholder, not disclosure.

    The engine's own rule (statement_known_on) dates such a quarter by its EPS release instead.
    """
    from backtest.research.prosperity_health import premium_members
    accepted = ACCEPTED[:5] + ['2026-06-30']          # newest quarter: placeholder on the fiscal end
    funds = {'A': quarter_rows([1.0, 1.0, 1.0, 1.0, 1.0, 1.5], accepted)}
    funds['A']['earnings'] = [{'fiscal_date': '2026-06-30', 'announce_date': '2026-08-20', 'eps_actual': 1.5}]
    rows = [row('A', 1, beta=1.5)]
    assert premium_members(view(rows, as_of='2026-08-08'), funds, as_of='2026-08-08')['members'] == ()
    assert premium_members(view(rows, as_of='2026-08-22'), funds, as_of='2026-08-22')['members'] == ('A',)


def test_two_records_for_one_jump_adjust_it_once():
    """Review #2: a duplicated split record must not divide the history twice."""
    c = [(d, 100.0) for d in CAL[:250]] + [(d, 20.0) for d in CAL[250:]]
    b = book({'C': c}, {'C': [(CAL[250], 5.0), (CAL[254], 5.0)]})
    assert b.window_return('C', 240, 260) == (pytest.approx(0.0), 'ok')
    assert b.adjustments == (('C', CAL[250], 5.0),)
    assert [e[3] for e in b.split_events] == ['adjusted', 'already_adjusted']


def test_unrecorded_small_ratio_step_counts_as_a_real_move():
    """Review #3, kept by design: without a record, a 150→100 step is indistinguishable from an
    earnings move (F1 ranked names had ~1,000 real daily moves near 3:2/4:3/5:4 since 2021-09),
    so it is a return, not 'unknown'. Recorded splits of any ratio are resolved by rule 2."""
    q = [(d, 150.0) for d in CAL[:250]] + [(d, 100.0) for d in CAL[250:]]
    assert book({'Q': q}).window_return('Q', 240, 260) == (pytest.approx(-1 / 3), 'ok')


def test_ordinary_large_move_is_not_split_suspect():
    v = [(d, 100.0) for d in CAL[:250]] + [(d, 72.0) for d in CAL[250:]]      # −28%: not a split ratio
    assert book({'V': v}).window_return('V', 240, 260) == (pytest.approx(-0.28), 'ok')


def test_momentum_checks_jumps_back_to_the_close_actually_used():
    """Review #5: close_at may reach up to 5 days early. A split-like jump between that close and
    the nominal look-back day falls outside (calendar[lo], calendar[t0]] and was not checked."""
    from backtest.research.prosperity_health import momentum_top
    t0 = len(CAL) - 60
    lo = t0 - 252
    closes = {f'M{i:02d}': [(d, 10.0 * (1 + i / 100) if k >= lo + 1 else 10.0) for k, d in enumerate(CAL)]
              for i in range(45)}
    # GAP: 100 up to lo−3, an unrecorded 5:1 split at lo−2 (20), no close at lo−1 or lo, then 20 again.
    # close_at(lo) = 20 (lo−2, after the split) → clean 12-1, the stock stays eligible.
    # LATE: 100 up to lo−3, no close lo−2..lo−1, split at lo (20). close_at(lo) = 20 → clean.
    # EARLY: 100 up to lo−4, no close lo−3..lo, split at lo+1 (20). close_at(lo) = 100 (lo−4) and the
    #        jump is inside (lo, t0] → excluded. These three already behave; the review case is:
    # MISS: 100 up to lo−4, split at lo−3 (20), no close lo−2..lo. close_at(lo) = 20, after the split
    #       → also clean. So a close returned by close_at is always the last one ≤ calendar[lo], and any
    #       jump after it lies in (calendar[lo], calendar[t0]]. The scan already covers it.
    closes['EARLY'] = [(d, 100.0) for d in CAL[:lo - 3]] + [(d, 20.0) for d in CAL[lo + 1:]]
    rows = [row(s, k + 1) for k, s in enumerate(sorted(closes))]
    b = book(closes)
    assert b.close_at('EARLY', lo) == 100.0
    assert [j['kind'] for j in b.jumps('EARLY', lo, t0)] == ['split_suspect']
    assert 'EARLY' not in momentum_top(view(rows, as_of=CAL[t0]), b)


def test_bh_matches_the_event_study_helper():
    """Review #9: reuse backtest/event_study/stats.py (north star §4.3) instead of a second BH."""
    from backtest.event_study.stats import _apply_bh_fdr_to_p_values
    from backtest.research.prosperity_health import bh_adjust
    ps = [0.001, 0.04, None, 0.2, 0.03, None]
    full = _apply_bh_fdr_to_p_values([1.0 if p is None else p for p in ps])
    assert bh_adjust(ps) == [None if p is None else pytest.approx(a) for p, a in zip(ps, full)]


def test_price_book_without_calendar_is_refused():
    """Second review #2, library side: an empty calendar must not read as 'not started'."""
    from backtest.research.prosperity_health import PriceBook
    with pytest.raises(ValueError, match='empty_calendar'):
        PriceBook(closes={'A': rising()}, splits={}, calendar=[])


def _verifier():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'reports/prosperity/m12-health/verify_health.py'
    spec = importlib.util.spec_from_file_location('m12_verify_health', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('closes,splits,window,expected', [
    ([(d, 100.0) for d in CAL[:250]] + [(d, 20.0) for d in CAL[250:]],
     [(CAL[250], 5.0), (CAL[254], 5.0)], (240, 260), (0.0, 'ok')),             # two records, one 5:1 jump
    ([(d, 100.0) for d in CAL[:250]] + [(d, 40.0) for d in CAL[250:]],
     [(CAL[250], 2.0)], (240, 260), (None, 'split_unresolved')),                # 2:1 record, 100→40
    ([(d, 100.0) for d in CAL[:250]] + [(d, 80.0) for d in CAL[250:]],
     [(CAL[250], 1.25)], (240, 260), (0.0, 'ok')),                              # 5:4 record, 100→80
])
def test_production_and_verifier_agree_on_split_cases(closes, splits, window, expected):
    """Second review #4: the independent verifier must handle the same cases as production."""
    import numpy as np
    v = _verifier()
    prod = book({'X': closes}, {'X': splits}).window_return('X', *window)
    dates = [d for d, _ in closes]
    mine = v.Book({'X': (dates, np.array([c for _, c in closes], float))}, {'X': splits},
                  list(CAL), v.classify_step).ret('X', *window)
    for got in (prod, mine):
        assert got[1] == expected[1]
        assert (got[0] is None) if expected[0] is None else got[0] == pytest.approx(expected[0])
