"""Stage-2 fault fixtures F01–F41 (implementation plan §6, Task 2.0).

Black-box: each test drives the real jobs against the in-memory exchange and
asserts on exchange facts (positions, stops, requests) and stored state.
"""
import sqlite3
from decimal import Decimal

import pandas as pd
import pytest

from tests.momentum_ls01_fake_exchange import Crash, FakeClock, FakeExchange
from trading.momentum_ls01 import reconcile
from trading.momentum_ls01.binance import Endpoints as E
from trading.momentum_ls01.guard import guard_job, watchdog
from trading.momentum_ls01.prepare import prepare_job
from trading.momentum_ls01.rebalance import rebalance_job
from trading.momentum_ls01.runtime import Runtime
from trading.momentum_ls01.store import process_lock

ANCHOR = pd.Timestamp('2026-11-16 04:00', tz='UTC')
WEEK = pd.Timedelta(days=7)
SHORTS = ['S%02dUSDT' % i for i in range(4)]
LONGS = ['L%02dUSDT' % i for i in range(3)]
S0, S1, S2, S3 = SHORTS
L0, L1, L2 = LONGS
NOT_AUTH_REJECT = -4005      # an ordinary single-order rejection
AUTH_REJECT = -2015          # invalid key, IP or permissions


def prep_result(anchor, shorts, longs, regime):
    def row(symbol, ret):
        return dict(symbol=symbol, status='ok', eligible=True, er=.5, r_squared=.8, drawdown=.1,
                    quote_volume=1e8, slope=ret / 10, **{'return': ret})
    rows = [row(s, -.50 + .05 * i) for i, s in enumerate(shorts)] + [row(s, .50 - .05 * i) for i, s in enumerate(longs)]
    day = str((anchor.normalize() - pd.Timedelta(days=1)).date())
    names = [r['symbol'] for r in rows]
    report = dict(as_of=day, periods={'14d': dict(pool=names, pool_size=len(names), valid_count=len(names), rows=rows)})
    return dict(strategy_id='momentum-ls-01', rule_version='2026-10-19.v2', anchor_utc=str(anchor), signal_day=day,
                fisher=dict(fisher=1.0, trigger=.5, regime=regime, week_end=str(anchor.normalize()),
                            available_at=str(anchor)),
                short_ranked=list(shorts), long_ranked=list(longs), input_hash='h', input_hashes={}, report=report)


class World:
    def __init__(self, tmp_path, shorts=3, longs=2, regime=1, wallet='10000', start=1):
        self.clock = FakeClock(ANCHOR.timestamp() + start)
        self.anchor = ANCHOR
        self.shorts, self.longs, self.regime = SHORTS[:shorts], LONGS[:longs], regime
        self.ex = FakeExchange(self.clock, SHORTS + LONGS, wallet=wallet)
        self.sent, self.send_ok = [], True
        self.state_dir = tmp_path / 'state'
        self.rt = Runtime(state_dir=self.state_dir, client=self.ex, clock=self.clock.time,
                          sleep=self.clock.sleep, send=self._send, release_commit='test')

    def _send(self, text):
        if self.send_ok:
            self.sent.append(text)
        return self.send_ok

    def prepare(self, fail=None):
        def compute():
            if fail:
                raise ValueError(fail)
            return prep_result(self.anchor, self.shorts, self.longs, self.regime)
        return prepare_job(self.rt, self.anchor, compute)

    def rebalance(self, **kwargs):
        return rebalance_job(self.rt, **kwargs)

    def guard(self, **kwargs):
        return guard_job(self.rt, **kwargs)

    def open(self):
        self.prepare()
        result = self.rebalance()
        assert result['status'] == 'done', result
        return self

    def next_week(self, seconds_after=1):
        self.anchor = self.anchor + WEEK
        self.clock.now = self.anchor.timestamp() + seconds_after

    def qty(self, symbol):
        return self.ex.qty[symbol]

    def stops(self, symbol):
        return [a for a in self.ex.algos.values() if a['symbol'] == symbol and a['algoStatus'] == 'NEW']

    def orders_sent(self, symbol=None, reduce_only=None):
        rows = [p for _, _, p in self.ex.writes(E.ORDER)]
        return [p for p in rows if (symbol is None or p['symbol'] == symbol)
                and (reduce_only is None or bool(p.get('reduceOnly')) == reduce_only)]

    def week(self):
        return self.rt.store.week(str(self.anchor))

    def codes(self):
        return {d['code'] for d in self.week()['deviations']}

    def pause(self):
        return self.rt.store.get('pause')

    def assert_protected(self, symbol):
        stops = self.stops(symbol)
        assert len(stops) == 1, stops
        assert Decimal(stops[0]['quantity']) == -self.qty(symbol)
        assert Decimal(stops[0]['triggerPrice']) == self.ex.entry[symbol] * 2
        assert stops[0]['reduceOnly'] and stops[0]['workingType'] == 'CONTRACT_PRICE'
        assert stops[0]['priceProtect'] is False


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def only(symbol):
    return lambda p: p.get('symbol') == symbol


# ---- baseline: the fixtures below are deviations from this -------------
def test_clean_week_opens_frozen_targets_and_protects_every_short(w):
    w.open()
    assert [w.qty(s) for s in (S0, S1, S2)] == [Decimal(-10)] * 3
    assert [w.qty(s) for s in (L0, L1)] == [Decimal(5)] * 2
    for symbol in (S0, S1, S2):
        w.assert_protected(symbol)
    assert not w.stops(L0) and not w.stops(L1)
    assert w.week()['status'] == 'done' and not w.week()['deviations']
    assert all(w.ex.leverage[s] == 2 for s in (S0, S1, S2, L0, L1))
    assert all(p['newClientOrderId'].startswith('mls1-26111604-') for p in w.orders_sent())


def test_bear_regime_holds_shorts_only(tmp_path):
    w = World(tmp_path, regime=-1).open()
    assert [w.qty(s) for s in (S0, S1, S2)] == [Decimal(-10)] * 3
    assert w.qty(L0) == w.qty(L1) == 0


# ---- G6 recovery and time ----------------------------------------------
def test_f01_same_anchor_twice_sends_nothing_new(w):
    w.open()
    before = len(w.ex.writes())
    assert w.rebalance()['status'] == 'duplicate'
    assert len(w.ex.writes()) == before


def test_f02_late_wakeup_keeps_logical_anchor(tmp_path):
    w = World(tmp_path, start=2).open()
    assert w.week()['anchor_utc'] == str(ANCHOR)
    assert w.week()['run_started_at'] == pytest.approx(ANCHOR.timestamp() + 2, abs=1)


def test_f03_start_after_entry_window_sends_nothing_and_alerts(tmp_path):
    w = World(tmp_path, start=301)
    w.prepare()
    assert w.rebalance()['status'] == 'late'
    assert not w.ex.writes() and any('迟到' in text for text in w.sent)


def test_f03_approved_late_run_needs_same_utc_day(tmp_path):
    w = World(tmp_path, start=301)
    w.prepare()
    assert w.rebalance(late_approved=True)['status'] == 'done'
    late = World(tmp_path / 'next-day', start=21 * 3600)
    late.prepare()
    assert late.rebalance(late_approved=True)['status'] == 'late'
    assert not late.ex.writes()


def test_f04_midweek_restart_recovers_and_protects_but_never_opens_old_targets(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'crash_before', when=only(S2))
    with pytest.raises(Crash):
        w.rebalance()
    w.clock.now += 2 * 86400
    w.guard()
    assert w.qty(S0) == w.qty(S1) == -10 and w.qty(S2) == 0 and w.qty(L0) == 0
    w.assert_protected(S0)
    w.assert_protected(S1)
    assert [p['symbol'] for p in w.orders_sent(reduce_only=False)] == [S0, S1, S2]


def test_f05_guard_yields_to_a_running_rebalance(w):
    w.open()
    before = len(w.ex.calls)
    with process_lock(w.state_dir):
        assert w.guard()['status'] == 'skipped_locked'
    assert len(w.ex.calls) == before


def test_f06_stop_at_the_anchor_is_handled_first_and_blocks_reentry(tmp_path):
    w = World(tmp_path).open()
    w.next_week(seconds_after=1)
    w.ex.set_price(S0, 200)   # fires one second after the anchor, before the run acts
    w.ex.set_price(S0, 100)
    assert w.qty(S0) == 0
    w.shorts = SHORTS[:4]
    w.prepare()
    assert w.rebalance()['status'] in ('done', 'deviated')
    assert w.qty(S0) == 0 and not w.orders_sent(S0)[1:]
    assert w.qty(S3) < 0
    w.assert_protected(S3)
    assert w.rt.store.get('cooldown:' + S0) is not None


def test_f07_partial_stop_then_price_falls_back_still_exits_to_zero(w):
    w.open()
    w.ex.stop_fill_fraction[S0] = '0.5'
    w.ex.set_price(S0, 200)
    w.ex.set_price(S0, 150)
    assert w.qty(S0) == -5
    algo_posts = len(w.ex.writes(E.ALGO_ORDER))
    w.clock.now += 60
    w.guard()
    assert w.qty(S0) == 0 and not w.stops(S0)
    assert not [p for _, _, p in w.ex.writes(E.ALGO_ORDER)[algo_posts:] if p.get('symbol') == S0 and 'quantity' in p]


def test_f08_failed_preparation_adds_nothing_and_leaves_positions_alone(w):
    w.open()
    w.next_week()
    w.prepare(fail='fisher_week_missing')
    before = len(w.ex.writes())
    assert w.rebalance()['status'] == 'no_new'
    assert len(w.ex.writes()) == before
    assert w.qty(S0) == -10 and w.qty(L0) == 5
    w.assert_protected(S0)
    assert 'prep_failed' in w.codes() and any('准备失败' in text for text in w.sent)


# ---- G5/G6 unknown results ---------------------------------------------
def test_f09_lost_response_is_queried_not_resent(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'lost', when=only(S1))
    w.rebalance()
    assert w.qty(S1) == -10 and len(w.orders_sent(S1)) == 1
    w.assert_protected(S1)


def test_f10_confirmed_not_accepted_is_reissued_once_with_a_new_id(w):
    w.prepare()
    started = w.clock.now
    w.ex.fail('POST', E.ORDER, 'unknown', when=only(S1))
    w.rebalance()
    ids = [p['newClientOrderId'] for p in w.orders_sent(S1)]
    assert len(ids) == 2 and ids[0].endswith('-1') and ids[1].endswith('-2') and ids[0][:-2] == ids[1][:-2]
    assert w.clock.now - started >= 120
    assert w.qty(S1) == -10
    w.assert_protected(S1)


def test_f11_partial_or_rejected_reduction_is_not_chased(tmp_path):
    w = World(tmp_path).open()
    w.next_week()
    w.regime = -1
    w.prepare()
    w.ex.fill_fraction[L0] = '0.5'
    w.ex.fail('POST', E.ORDER, 'reject', when=only(L1), code=NOT_AUTH_REJECT)
    w.rebalance()
    assert w.qty(L0) == Decimal('2.5') and w.qty(L1) == 5
    assert len(w.orders_sent(L0, reduce_only=True)) == 1 and len(w.orders_sent(L1, reduce_only=True)) == 1
    assert {'wave_a_partial', 'wave_a_rejected'} <= w.codes()


def test_f12_margin_shortage_scales_wave_b_by_one_factor(w):
    w.prepare()
    w.ex.available_override = '500'
    w.rebalance()
    assert [w.qty(s) for s in (S0, S1, S2)] == [Decimal('-2.272')] * 3
    assert [w.qty(s) for s in (L0, L1)] == [Decimal('1.136')] * 2
    scaled = next(d for d in w.week()['deviations'] if d['code'] == 'wave_b_scaled')
    assert float(scaled['factor']) == pytest.approx(500 / 2200)
    assert float(w.week()['wave_b_scale']) == pytest.approx(500 / 2200)


# ---- G1/G2 protection --------------------------------------------------
def test_f13_price_already_past_the_stop_exits_as_a_normal_stop(w):
    w.prepare()
    w.ex.fail('POST', E.ALGO_ORDER, 'hook',
              when=lambda p: p['symbol'] == S0 and (w.ex.set_price(S0, 250) or True))
    w.rebalance()
    assert w.qty(S0) == 0 and not w.stops(S0)
    assert w.rt.store.get('cooldown:' + S0) is not None
    assert w.pause() is None
    w.assert_protected(S1)


def test_f14_single_rejected_protection_closes_the_new_position_and_stops_adding(w):
    w.prepare()
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', when=only(S1), code=NOT_AUTH_REJECT)
    w.rebalance()
    w.assert_protected(S0)
    assert w.qty(S1) == 0 and w.qty(S2) == 0 and w.qty(L0) == 0
    assert 'protection_rejected' in w.codes() and w.week()['status'] == 'deviated'
    assert w.pause() is None


def test_f14_rejected_replacement_reduces_back_to_the_covered_quantity(w):
    w.open()
    w.next_week()
    w.ex.set_price(S0, 80)   # same budget buys more units, so S0 gets an addition
    w.prepare()
    posts = len(w.ex.writes(E.ALGO_ORDER))
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', when=only(S0), code=NOT_AUTH_REJECT)
    w.rebalance()
    assert w.qty(S0) == -10
    old = w.stops(S0)
    assert len(old) == 1 and Decimal(old[0]['quantity']) == 10
    assert 'protection_rejected' in w.codes()
    assert len(w.ex.writes(E.ALGO_ORDER)) == posts + 1


def test_f15_permission_class_rejection_is_a_common_cause_not_a_per_symbol_exit(w):
    w.prepare()
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', when=only(S0), code=AUTH_REJECT)
    w.rebalance()
    assert w.qty(S0) == -10 and not w.orders_sent(reduce_only=True)
    assert w.qty(S1) == 0
    assert w.pause()['reason'] == 'protection_common_cause'
    assert any('共同根因' in text for text in w.sent)


def test_f16_two_symbols_failing_together_wait_for_the_boss(tmp_path):
    w = World(tmp_path, shorts=2, longs=0).open()
    for symbol in (S0, S1):
        w.stops(symbol)[0]['algoStatus'] = 'EXPIRED'
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', code=NOT_AUTH_REJECT, times=99)
    w.clock.now += 60
    w.guard()
    assert w.qty(S0) == w.qty(S1) == -10 and not w.orders_sent(reduce_only=True)
    assert w.pause()['reason'] == 'protection_common_cause'


def test_f40_last_remaining_symbol_with_a_single_fault_exits_automatically(tmp_path):
    w = World(tmp_path, shorts=1, longs=0).open()
    w.stops(S0)[0]['algoStatus'] = 'EXPIRED'
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', code=NOT_AUTH_REJECT, times=99)
    w.clock.now += 60
    w.guard()
    assert w.qty(S0) == 0
    assert w.pause() is None


# ---- G4 leverage and cost ----------------------------------------------
def test_f17_failed_leverage_setting_leaves_the_slot_empty(w):
    w.prepare()
    w.ex.fail('POST', E.LEVERAGE, 'reject', when=only(S1), code=NOT_AUTH_REJECT)
    w.rebalance()
    assert w.qty(S1) == 0 and not w.orders_sent(S1)
    assert w.qty(S0) == w.qty(S2) == -10
    assert 'leverage_failed' in w.codes()


def test_f18_leverage_readback_other_than_two_blocks_the_symbol(w):
    w.prepare()
    w.ex.ignore_leverage = {S1}
    w.rebalance()
    assert w.qty(S1) == 0 and not w.orders_sent(S1)
    assert 'config_mismatch' in w.codes()


def test_f32_entry_price_disagreeing_with_local_cost_stops_and_overwrites_nothing(w):
    w.open()
    w.ex.entry[S0] *= Decimal('1.01')
    writes = len(w.ex.writes())
    w.clock.now += 60
    w.guard()
    assert w.pause()['reason'] == 'entry_price_mismatch'
    assert len(w.ex.writes()) == writes
    assert w.ex.entry[S0] == Decimal('101')


# ---- G7 external money -------------------------------------------------
def test_f19_first_deposit_becomes_baseline_only_after_explicit_acceptance(tmp_path):
    w = World(tmp_path, wallet='0')
    w.ex.deposit(10000, tran_id=5)
    w.guard(full=True)
    assert w.pause()['reason'] == 'external_flow'
    assert reconcile.resume(w.rt)['status'] == 'blocked'
    assert reconcile.resume(w.rt, accept_flow=5)['status'] == 'resumed'
    assert w.pause() is None and 5 in w.rt.store.get('authorized_flows')
    w.open()
    assert w.qty(S0) == -10


def test_f20_unverified_transfer_pauses_persistently(w):
    w.open()
    w.ex.deposit(-300)
    w.clock.now += 60
    w.guard(full=True)
    assert w.pause()['reason'] == 'external_flow'
    w.next_week()
    w.prepare()
    before = len(w.ex.writes())
    assert w.rebalance()['status'] == 'no_new'
    assert len(w.ex.writes()) == before


def test_f21_income_rows_are_recorded_once(w):
    w.open()
    w.ex.funding(S0, '-1.5')
    w.guard(full=True)
    first = w.rt.store.evidence('income')
    w.clock.now += 3600
    w.guard(full=True)
    assert len(w.rt.store.evidence('income')) == len(first)
    assert sum(1 for row in first if row['payload']['incomeType'] == 'FUNDING_FEE') == 1
    assert w.pause() is None


# ---- G8 guard failure and rate limits ----------------------------------
def test_f22_missed_guard_is_reported_within_five_minutes_by_the_watchdog(w):
    w.open()
    w.guard()
    last = w.clock.now
    alerts = []
    for minute in range(1, 6):
        w.clock.now = last + 60 * minute
        if watchdog(w.state_dir, w.clock.time, alerts.append)['alert']:
            break
    assert alerts and w.clock.now - last <= 300
    assert w.clock.now - last > 180


def test_f23_rate_limit_stops_adding_for_this_run_only(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'reject', when=only(S1), http_status=429, retry_after=30)
    w.rebalance()
    w.assert_protected(S0)
    assert w.qty(S1) == w.qty(S2) == w.qty(L0) == 0
    assert 'rate_limited' in w.codes() and w.pause() is None


def test_f23_ip_ban_stops_adding_and_alerts(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'reject', when=only(S1), http_status=418, retry_after=600)
    w.rebalance()
    assert w.qty(S1) == w.qty(S2) == 0
    assert w.rt.store.get('ip_ban')['until'] >= w.clock.now
    assert any('418' in text for text in w.sent)


def test_f24_database_write_failure_means_the_request_is_never_sent(w, monkeypatch):
    w.prepare()
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(w.rt.store, 'mark_sending', broken)
    result = w.rebalance()
    assert result['status'] == 'blocked'
    assert not w.ex.writes(E.ORDER) and not w.ex.writes(E.ALGO_ORDER)
    assert any('写库失败' in text for text in w.sent)


def test_f25_failed_notification_stays_pending_and_trading_is_unaffected(w):
    w.send_ok = False
    w.open()
    assert w.qty(S0) == -10 and w.rt.store.pending_notifications()
    w.send_ok = True
    w.clock.now += 60
    w.guard()
    assert w.sent and not w.rt.store.pending_notifications()


def test_f26_pause_does_not_block_stop_residual_or_crossing_exits(w):
    w.open()
    reconcile.pause(w.rt, 'manual_pause')
    w.ex.stop_fill_fraction[S0] = '0.5'
    w.ex.set_price(S0, 200)
    w.ex.set_price(S0, 150)
    w.stops(S1)[0]['algoStatus'] = 'EXPIRED'
    w.ex.set_price(S1, 230)
    w.clock.now += 60
    w.guard()
    assert w.qty(S0) == 0 and w.qty(S1) == 0
    assert w.pause()['reason'] == 'manual_pause'
    assert w.qty(S2) == -10


def test_f27_manual_trade_is_external_and_pauses_until_resume_reconciles(w):
    w.open()
    w.ex.manual_order(S0, 'BUY', 3)
    w.clock.now += 60
    w.guard()
    assert w.pause()['reason'] == 'manual_takeover'
    assert Decimal(w.stops(S0)[0]['quantity']) == 10
    assert reconcile.resume(w.rt)['status'] == 'resumed'
    assert w.pause() is None and w.qty(S0) == -7
    w.assert_protected(S0)


# ---- G10 delisting and contract state ----------------------------------
def test_f28_delisting_keeps_the_stop_until_settlement_and_records_a_delist_exit(w):
    w.open()
    w.ex.delivery[S0] = int((w.clock.now + 2 * 86400) * 1000)
    w.clock.now += 60
    w.guard(full=True)
    w.assert_protected(S0)
    w.ex.settle(S0)
    w.clock.now += 3600
    w.guard(full=True)
    assert w.pause() is None
    assert w.rt.store.get('episode:' + S0) is None and w.rt.store.get('cooldown:' + S0) is None
    exits = [row['payload'] for row in w.rt.store.evidence('exit')]
    assert [e['reason'] for e in exits if e['symbol'] == S0] == ['delist']


def test_f29_contract_that_cannot_be_opened_is_not_a_candidate(w):
    w.shorts = SHORTS[:4]
    w.ex.status[S0] = 'SETTLING'
    w.prepare()
    assert w.rebalance()['status'] == 'done'
    assert w.qty(S0) == 0 and not w.orders_sent(S0)
    assert w.qty(S1) == w.qty(S2) == w.qty(S3) == -10


def test_f30_eligible_symbol_rejected_on_open_leaves_an_empty_slot(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'reject', when=only(S1), code=NOT_AUTH_REJECT)
    w.rebalance()
    assert w.qty(S1) == 0 and len(w.orders_sent(S1)) == 1
    assert w.qty(S0) == w.qty(S2) == -10 and w.qty(L0) == 5
    assert 'open_rejected' in w.codes()


# ---- G3 quantity changes -----------------------------------------------
def test_f31_adl_keeps_exact_protection_and_full_liquidation_closes_the_episode(w):
    w.open()
    w.ex.adl(S0, 4)
    w.ex.liquidate(S1, 10)
    w.clock.now += 60
    w.guard()
    assert w.pause() is None
    assert w.qty(S0) == -6
    w.assert_protected(S0)
    assert not w.stops(S1) and w.rt.store.get('episode:' + S1) is None
    assert w.rt.store.get('cooldown:' + S1) is not None


def test_f33_residual_protection_on_a_flat_symbol_blocks_its_new_episode(w):
    w.ex.request('POST', E.ALGO_ORDER, dict(algoType='CONDITIONAL', symbol=S0, side='BUY', type='STOP_MARKET',
                                            quantity='10', triggerPrice='200', workingType='CONTRACT_PRICE',
                                            reduceOnly=True, clientAlgoId='mls1-26110904-P-0001-1'), signed=True)
    w.ex.calls.clear()
    w.ex.fail('DELETE', E.ALGO_ORDER, 'unknown', times=99)
    w.prepare()
    w.rebalance()
    assert w.qty(S0) == 0 and not w.orders_sent(S0)
    assert w.qty(S1) == w.qty(S2) == -10
    assert 'residual_protection' in w.codes()


def test_f34_missing_position_row_is_never_proof_of_zero(w):
    w.open()
    w.ex.fail('GET', E.POSITION_RISK_V2, 'empty')
    writes = len(w.ex.writes())
    w.clock.now += 60
    assert w.guard()['status'] == 'failed'
    assert len(w.ex.writes()) == writes
    assert w.rt.store.get('episode:' + S0) is not None
    w.assert_protected(S0)


def test_f41_triggered_stop_with_unknown_child_is_queried_before_any_extra_close(w):
    w.open()
    w.ex.stop_fill_fraction[S0] = '0.5'
    w.ex.set_price(S0, 200)
    w.ex.set_price(S0, 150)
    w.ex.fail('GET', E.ORDER, 'unknown', when=only(S0))
    w.clock.now += 60
    w.guard()
    assert w.qty(S0) == -5 and not w.orders_sent(S0, reduce_only=True)
    w.clock.now += 60
    w.guard()
    assert w.qty(S0) == 0 and len(w.orders_sent(S0, reduce_only=True)) == 1


# ---- G6 crash windows --------------------------------------------------
def test_f35_planned_wave_that_was_never_sent_is_abandoned_not_replayed(w, monkeypatch):
    w.prepare()
    original = w.rt.store.mark_sending
    def crash(action_id):
        if w.rt.store.action(action_id)['kind'] == 'order':
            raise Crash()
        return original(action_id)
    monkeypatch.setattr(w.rt.store, 'mark_sending', crash)
    with pytest.raises(Crash):
        w.rebalance()
    monkeypatch.setattr(w.rt.store, 'mark_sending', original)
    w.clock.now += 60
    w.guard()
    assert not w.ex.writes(E.ORDER)
    states = {a['state'] for a in w.rt.store.actions() if a['kind'] == 'order'}
    assert states == {'ABANDONED'}
    assert 'abandoned' in w.codes()


def test_f36_sending_that_never_reached_the_exchange_is_queried_then_closed_without_resend(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'crash_before', when=only(S0))
    with pytest.raises(Crash):
        w.rebalance()
    first = next(a for a in w.rt.store.actions() if a['symbol'] == S0 and a['kind'] == 'order')
    assert first['state'] == 'SENDING'
    for _ in range(4):
        w.clock.now += 60
        w.guard()
    action = next(a for a in w.rt.store.actions() if a['id'] == first['id'])
    assert action['state'] == 'NOT_ACCEPTED' and action['check_count'] >= 3
    assert len(w.orders_sent(S0)) == 1 and w.qty(S0) == 0


def test_f37_accepted_fill_lost_in_a_crash_is_found_and_protected_without_resend(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'crash_after', when=only(S0))
    with pytest.raises(Crash):
        w.rebalance()
    assert w.qty(S0) == -10 and not w.stops(S0)
    w.clock.now += 60
    w.guard()
    w.assert_protected(S0)
    assert len(w.orders_sent(S0)) == 1
    action = next(a for a in w.rt.store.actions() if a['symbol'] == S0 and a['kind'] == 'order')
    assert action['state'] == 'FILLED' and Decimal(action['executed_qty']) == 10


def test_f38_unknown_open_holds_back_later_symbols_until_it_is_resolved(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'lost', when=only(S2))
    w.ex.fail('GET', E.ORDER, 'unknown', when=only(S2), times=2)
    w.rebalance()
    calls = w.ex.calls
    stop_s2 = next(i for i, (m, p, q) in enumerate(calls) if m == 'POST' and p == E.ALGO_ORDER and q['symbol'] == S2)
    open_l0 = next(i for i, (m, p, q) in enumerate(calls) if m == 'POST' and p == E.ORDER and q['symbol'] == L0)
    assert stop_s2 < open_l0 and len(w.orders_sent(S2)) == 1
    for symbol in (S0, S1, S2):
        w.assert_protected(symbol)
    assert w.qty(L0) == w.qty(L1) == 5


def test_f38_unknown_open_past_the_deadline_pauses_and_sends_no_later_symbol(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'lost', when=only(S2))
    w.ex.fail('GET', E.ORDER, 'unknown', when=only(S2), times=10 ** 6)
    w.rebalance()
    assert w.pause()['reason'] == 'unknown_order_overdue'
    assert not w.orders_sent(L0) and not w.orders_sent(L1)
    w.assert_protected(S0)
    w.assert_protected(S1)


# ---- G9 shadow ---------------------------------------------------------
def test_f39_shadow_run_on_an_empty_account_plans_everything_and_touches_nothing(tmp_path):
    w = World(tmp_path, wallet='0')
    w.prepare()
    result = w.rebalance(dry_run=True, assume_equity='10000')
    assert result['status'] == 'dry_run'
    assert {s: Decimal(q) for s, q in result['targets'].items()} == {S0: -10, S1: -10, S2: -10, L0: 5, L1: 5}
    assert not w.ex.writes() and not w.rt.store.actions()
    assert w.week()['status'] == 'prepared' and w.rt.store.get('pause') is None
    assert (w.state_dir / result['plan_file']).exists()
