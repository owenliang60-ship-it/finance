"""Regressions for the 2026-11-16 review (findings checked against code and official docs)."""
from decimal import Decimal
import json
import sqlite3
import time

import pandas as pd
import pytest

from tests.test_momentum_ls01_faults import (ANCHOR, L0, L1, NOT_AUTH_REJECT, S0, S1, S2, World, only,
                                             prep_result)
from tests.test_momentum_ls01_ops_cli import ops  # noqa: F401  (fixture)
from trading.momentum_ls01 import cli, config as C, reconcile
from trading.momentum_ls01.actions import Actions
from trading.momentum_ls01.binance import Endpoints as E
from trading.momentum_ls01.guard import watchdog
from trading.momentum_ls01.prepare import prepare_job
from trading.momentum_ls01.store import LockBusy, Store, process_lock


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def adds_next_week(w):
    """Second week with every short 20% lower, so each short gets an addition and a new stop."""
    w.open()
    w.next_week()
    for symbol in (S0, S1, S2):
        w.ex.set_price(symbol, 80)
    w.prepare()


def episode_qty(w, symbol):
    return Decimal(w.rt.store.get('episode:' + symbol)['qty'])


# ---- must fix 1: a stop we cancelled is final locally ------------------
def test_replaced_stop_older_than_three_days_does_not_lock_the_guard(w):
    adds_next_week(w)
    assert w.rebalance()['status'] == 'done'
    for _ in range(C.GUARD_FAIL_LIMIT):
        w.clock.now += 60
        assert w.guard()['status'] == 'ok'
    assert w.pause() is None
    for symbol in (S0, S1, S2):   # averaged entry: the trigger is twice it, rounded up to the 0.01 tick
        stops = w.stops(symbol)
        assert len(stops) == 1 and Decimal(stops[0]['quantity']) == -w.qty(symbol)
        assert Decimal('0') <= Decimal(stops[0]['triggerPrice']) - w.ex.entry[symbol] * 2 < Decimal('0.01')


def test_stop_the_exchange_expired_while_the_guard_was_down_is_resolved(w):
    w.open()
    w.stops(S1)[0]['algoStatus'] = 'EXPIRED'
    w.clock.now += 4 * 86400
    assert w.guard()['status'] == 'ok'
    w.assert_protected(S1)


# ---- must fix 2: "stop adding" never stops wave A ----------------------
@pytest.mark.parametrize('fault', ['stop_rejected', 'rate_limited'])
def test_problem_on_the_first_reduction_still_closes_every_long(tmp_path, fault):
    w = World(tmp_path).open()
    w.next_week()
    w.regime = -1
    w.ex.set_price(S0, 125)   # smaller target: S0 is reduced first in wave A
    w.prepare()
    if fault == 'stop_rejected':
        w.ex.fail('POST', E.ALGO_ORDER, 'reject', when=only(S0), code=NOT_AUTH_REJECT)
    else:
        w.ex.fail('POST', E.ORDER, 'reject', when=only(S0), code=-1003, http_status=429, retry_after=1)
    w.rebalance()
    assert w.qty(L0) == w.qty(L1) == 0


# ---- must fix 3: a failure after the week is marked running ------------
def test_read_failure_after_the_run_starts_leaves_the_week_runnable(w):
    w.prepare()
    seen = []
    w.ex.fail('GET', E.ACCOUNT, 'unknown', when=lambda p: seen.append(1) or len(seen) == 2)
    assert w.rebalance()['status'] == 'failed'
    assert w.week()['status'] == 'no_new' and not w.ex.writes()
    assert any('调仓中途出错' in text for text in w.sent)
    assert w.rebalance()['status'] == 'deviated' and w.qty(S0) == -10   # the failure stays on the record
    assert 'run_failed' in w.codes()


def test_failure_after_a_fill_is_recorded_and_the_guard_protects_the_short(w):
    w.prepare()
    w.ex.fail('GET', E.OPEN_ALGO_ORDERS, 'unknown', when=only(S1))
    assert w.rebalance()['status'] == 'failed'
    assert w.week()['status'] == 'deviated' and 'run_failed' in w.codes()
    assert w.qty(S1) == -10 and w.qty(S2) == 0
    w.clock.now += 60
    assert w.guard()['status'] == 'ok'
    w.assert_protected(S1)
    assert w.rebalance()['status'] == 'duplicate' and w.qty(S2) == 0


def test_guard_counts_a_failed_exchange_info_read_as_a_guard_failure(tmp_path, monkeypatch, capsys):
    w = World(tmp_path).open()
    monkeypatch.setattr(cli, 'BinanceClient', lambda **kwargs: w.ex)
    monkeypatch.setattr(cli, 'load_credentials', lambda path: None)
    monkeypatch.setattr(cli, 'clock', w.clock.time)
    monkeypatch.setattr(cli, 'private_sender', lambda: w._send)
    w.ex.fail('GET', E.EXCHANGE_INFO, 'unknown')
    w.clock.now += 60
    assert cli.main(['guard', '--state-dir', str(w.state_dir)]) == 0
    assert w.rt.store.get('fail_streak') == 1


# ---- must fix 4: resume reconciles; it rewrites the book only on --adopt
def test_resume_settles_a_stop_that_fired_during_the_pause_before_anything_else(w):
    w.open()
    reconcile.pause(w.rt, 'manual_pause')
    w.ex.stop_fill_fraction[S0] = '0.5'
    w.ex.set_price(S0, 200)   # the stop fires and half fills while no guard runs
    w.ex.set_price(S0, 150)
    assert reconcile.resume(w.rt)['status'] == 'resumed'
    assert w.qty(S0) == 0 and w.rt.store.get('cooldown:' + S0) is not None


def test_resume_rewrites_the_local_book_only_when_told_to_adopt(w):
    w.open()
    w.ex.stop_fill_fraction[S0] = '0.5'
    w.ex.set_price(S0, 200)
    w.ex.set_price(S0, 150)
    w.ex.manual_order(S1, 'BUY', 3)
    reconcile.pause(w.rt, 'manual_pause')
    out = reconcile.resume(w.rt)
    assert out['status'] == 'blocked' and out['reason'] == 'manual_takeover'
    assert episode_qty(w, S1) == -10
    assert w.pause()['reason'] == 'manual_pause' and w.pause()['also'] == ['manual_takeover']
    assert reconcile.resume(w.rt, adopt=True)['status'] == 'resumed'
    assert episode_qty(w, S1) == -7 and w.pause() is None
    assert w.qty(S0) == 0
    w.assert_protected(S1)


def test_resume_cli_passes_the_adopt_flag(ops):
    w, run, built = ops
    w.open()
    w.ex.manual_order(S0, 'BUY', 3)
    w.clock.now += 60
    w.guard()
    assert run('resume')[0] == 1
    code, out = run('resume', '--adopt')
    assert code == 0 and out['status'] == 'resumed' and w.qty(S0) == -7


# ---- exits must report what really happened ----------------------------
def test_refused_crossing_exit_is_not_reported_as_done(w):
    w.prepare()
    w.ex.fail('POST', E.ALGO_ORDER, 'hook',
              when=lambda p: p['symbol'] == S0 and (w.ex.set_price(S0, 250) or True))
    w.ex.fail('POST', E.ORDER, 'reject', when=lambda p: p['symbol'] == S0 and p['reduceOnly'],
              code=NOT_AUTH_REJECT)
    w.rebalance()
    assert w.qty(S0) == -10 and 'protection_exiting' in w.codes()
    assert any('退出未完成' in text for text in w.sent)
    w.clock.now += 60
    w.guard()
    assert w.qty(S0) == 0 and w.rt.store.get('cooldown:' + S0) is not None


def test_refused_residual_exit_is_a_guard_failure_with_an_alert(w):
    w.open()
    w.ex.stop_fill_fraction[S0] = '0.5'
    w.ex.set_price(S0, 200)
    w.ex.set_price(S0, 150)
    w.ex.fail('POST', E.ORDER, 'reject', when=only(S0), code=NOT_AUTH_REJECT)
    w.clock.now += 60
    assert w.guard()['status'] == 'failed'
    assert w.qty(S0) == -5 and any('退出未完成' in text for text in w.sent)
    w.clock.now += 60
    assert w.guard()['status'] == 'ok' and w.qty(S0) == 0


def test_failed_fault_exit_is_not_reported_as_closed(w):
    w.open()
    w.stops(S1)[0]['algoStatus'] = 'EXPIRED'
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', when=only(S1), code=NOT_AUTH_REJECT)
    w.ex.fail('POST', E.ORDER, 'reject', when=only(S1), code=NOT_AUTH_REJECT)
    w.clock.now += 60
    w.guard()
    assert w.qty(S1) == -10
    assert not any('已按单币故障自动市价退出' in text for text in w.sent)
    w.clock.now += 60
    w.guard()
    assert w.qty(S1) == 0


def test_refused_fault_reduction_escalates_to_a_full_exit(w):
    w.open()
    w.next_week()
    w.ex.set_price(S0, 80)
    w.prepare()
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', when=only(S0), code=NOT_AUTH_REJECT, times=99)
    w.ex.fail('POST', E.ORDER, 'reject', when=lambda p: p['symbol'] == S0 and '-F-' in p['newClientOrderId'],
              code=NOT_AUTH_REJECT)
    w.rebalance()
    assert w.qty(S0) == 0 and w.rt.store.get('cooldown:' + S0)['reason'] == 'fault'


# ---- income types (Boss 2026-11-16: settlement and fee rebates are trading result)
@pytest.mark.parametrize('kind', ['DELIVERED_SETTELMENT', 'COMMISSION_REBATE', 'REFERRAL_KICKBACK',
                                  'API_REBATE', 'FEE_RETURN'])
def test_settlement_and_fee_rebates_are_trading_income(w, kind):
    w.open()
    w.ex._income(kind, '1.25', S0)
    w.clock.now += 60
    w.guard(full=True)
    assert w.pause() is None


# ---- store, guard exit codes, watchdog ---------------------------------
def test_opening_the_store_writes_nothing_once_the_schema_exists(w):
    w.rt.store.set('x', 1)
    other = sqlite3.connect(w.state_dir / 'state.db', isolation_level=None)
    other.execute('BEGIN IMMEDIATE')
    try:
        started = time.monotonic()
        Store(w.state_dir)
        assert time.monotonic() - started < 2
    finally:
        other.execute('ROLLBACK')
        other.close()


def test_guard_exits_nonzero_only_when_failures_turn_into_a_pause(ops):
    w, run, built = ops
    w.open()
    w.ex.fail('GET', E.OPEN_ALGO_ORDERS, 'unknown', times=C.GUARD_FAIL_LIMIT + 1)
    codes = []
    for _ in range(C.GUARD_FAIL_LIMIT + 1):
        w.clock.now += 60
        codes.append(run('guard')[0])
    assert codes == [0] * (C.GUARD_FAIL_LIMIT - 1) + [1, 0]
    assert w.pause()['reason'] == 'guard_failures'


def test_watchdog_alerts_again_when_the_last_alert_was_not_delivered(w):
    w.send_ok = False
    assert watchdog(w.state_dir, w.clock.time, w._send)['status'] == 'failed'
    w.send_ok = True
    w.clock.now += 60
    assert watchdog(w.state_dir, w.clock.time, w._send)['alert'] and w.sent


def test_alive_message_that_could_not_be_sent_exits_nonzero(ops):
    w, run, built = ops
    w.send_ok = False
    assert run('alive')[0] == 1


# ---- the shadow run plans what the live run would send -----------------
def test_shadow_run_orders_filters_and_scales_like_the_live_run(w):
    w.prepare()
    w.ex.available_override = '500'
    plan = json.loads((w.state_dir / w.rebalance(dry_run=True)['plan_file']).read_text())
    assert [i['symbol'] for i in plan['wave_b']] == [S0, S1, S2, L0, L1]
    assert [i['qty'] for i in plan['wave_b']] == ['2.272'] * 3 + ['1.136'] * 2
    assert 'wave_b_scaled' in {d['code'] for d in plan['deviations']}
    assert not w.ex.writes() and not w.rt.store.actions() and not w.week()['deviations']


def test_shadow_run_on_an_empty_account_without_assumed_equity_adds_nothing(tmp_path):
    w = World(tmp_path, wallet='0')
    w.prepare()
    assert w.rebalance(dry_run=True)['status'] == 'no_new'


# ---- preparation writes the week under the process lock ----------------
def test_preparation_never_overwrites_a_week_that_started_meanwhile(w):
    def compute():
        w.rt.store.save_week(str(w.anchor), status='running')   # the rebalance started while we fetched
        return prep_result(w.anchor, w.shorts, w.longs, w.regime)
    assert prepare_job(w.rt, w.anchor, compute)['status'] == 'locked'
    assert w.week()['status'] == 'running' and w.week()['prep_status'] is None


def test_preparation_writes_the_week_under_the_process_lock(w):
    held, save = [], w.rt.store.save_week
    def spy(*args, **kwargs):
        try:
            with process_lock(w.state_dir):
                held.append(False)
        except LockBusy:
            held.append(True)
        return save(*args, **kwargs)
    w.rt.store.save_week = spy
    assert w.prepare()['status'] == 'ready'
    assert held and all(held)


# ---- small items ---------------------------------------------------------
def test_setting_readback_with_no_row_is_not_confirmed_and_never_hangs(w):
    acts = Actions(w.rt)
    w.ex.fail('POST', E.LEVERAGE, 'lost')
    action_id = acts.plan_setting(ANCHOR, S0, 'leverage')
    acts.send(action_id)
    w.ex.fail('GET', E.SYMBOL_CONFIG, 'empty')
    assert acts.resolve(action_id)['state'] == 'NOT_ACCEPTED'   # a setting has no exposure to wait on


def test_setting_whose_readback_keeps_failing_is_reported_as_overdue(w):
    acts = Actions(w.rt)
    w.ex.fail('POST', E.MARGIN_TYPE, 'lost')
    action_id = acts.plan_setting(ANCHOR, S0, 'margin_type')
    acts.send(action_id)
    w.ex.fail('GET', E.SYMBOL_CONFIG, 'unknown')
    assert acts.resolve(action_id)['state'] == 'UNKNOWN'
    w.clock.now += C.CONVERGE_S + 1
    assert [a['kind'] for a in reconcile.Desk(w.rt).overdue_unknowns()] == ['margin_type']


# ---- second review, 2026-11-16 ------------------------------------------
def test_hourly_full_check_that_keeps_failing_pauses_and_alerts_once(ops):
    w, run, built = ops
    w.open()
    w.ex.fail('GET', E.SYMBOL_CONFIG, 'unknown', times=99)
    w.clock.now = (ANCHOR + pd.Timedelta(hours=1)).timestamp()   # on the hour: a full check
    codes = []
    for _ in range(C.GUARD_FAIL_LIMIT + 1):
        codes.append(run('guard')[0])
        w.clock.now += 60
    assert codes == [0] * (C.GUARD_FAIL_LIMIT - 1) + [1, 0]
    assert w.pause()['reason'] == 'guard_failures'


def test_full_check_is_repeated_every_minute_until_it_passes(w):
    w.open()
    w.ex.fail('GET', E.SYMBOL_CONFIG, 'unknown')
    w.clock.now = (ANCHOR + pd.Timedelta(hours=1)).timestamp()
    assert w.guard()['status'] == 'failed'
    w.clock.now += 60
    retry = w.guard()
    assert retry['full'] is True and retry['status'] == 'ok'
    w.clock.now += 60
    assert w.guard()['full'] is False


def test_failed_resume_keeps_the_original_pause_and_points_to_adopt(w):
    w.open()
    w.ex.manual_order(S0, 'BUY', 3)
    w.clock.now += 60
    w.guard()
    first = w.pause()
    alerts = sum('持久暂停' in text for text in w.sent)
    out = reconcile.resume(w.rt)
    assert out['status'] == 'blocked' and out['reason'] == 'manual_takeover' and '--adopt' in out['hint']
    assert w.pause() == first
    assert sum('持久暂停' in text for text in w.sent) == alerts


def test_cancel_query_that_does_not_identify_the_stop_is_not_a_confirmation(w):
    w.open()
    acts = Actions(w.rt)
    action_id = acts.plan_cancel_stop(ANCHOR, S0, w.stops(S0)[0]['clientAlgoId'])
    w.ex.fail('DELETE', E.ALGO_ORDER, 'unknown')
    assert acts.send(action_id).state == 'UNKNOWN'
    w.ex.fail('GET', E.ALGO_ORDER, 'empty')
    assert acts.resolve(action_id)['state'] == 'UNKNOWN'
    assert acts.resolve(action_id)['state'] == 'NOT_ACCEPTED'   # the stop is still working: nothing was cancelled


def test_held_symbol_missing_from_symbol_config_fails_the_check_but_not_protection(w):
    w.open()
    w.stops(S1)[0]['algoStatus'] = 'EXPIRED'
    w.ex.fail('GET', E.SYMBOL_CONFIG, 'empty')
    w.clock.now += 60
    assert w.guard(full=True)['status'] == 'failed'
    w.assert_protected(S1)


def test_cooldown_after_a_full_liquidation_runs_from_the_fill_time(w):
    w.open()
    w.clock.now += 3600
    filled_at = w.clock.now
    w.ex.liquidate(S0, 10)
    w.clock.now += 7200   # the guard was down for two hours
    w.guard()
    assert w.rt.store.get('cooldown:' + S0)['at'] == pytest.approx(filled_at)
