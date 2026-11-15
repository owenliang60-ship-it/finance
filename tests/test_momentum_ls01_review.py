"""Regressions for the 2026-11-15 high-effort code review (10 findings)."""
from decimal import Decimal

import pytest

from tests.momentum_ls01_fake_exchange import FakeClock, FakeExchange
from tests.test_momentum_ls01_binance import Response, client
from tests.test_momentum_ls01_faults import ANCHOR, L0, S0, S1, S2, World, only
from trading.momentum_ls01 import cli, config as C, notify, reconcile
from trading.momentum_ls01.actions import Actions
from trading.momentum_ls01.binance import Credentials, Endpoints as E, ExchangeError, RateLimitState
from trading.momentum_ls01.reconcile import Desk
from trading.momentum_ls01.store import process_lock


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def adds_next_week(w):
    """Second week with every short 20% lower, so each short gets an addition."""
    w.open()
    w.next_week()
    for symbol in (S0, S1, S2):
        w.ex.set_price(symbol, 80)
    w.prepare()


# 1 -- a rate limit on the stop request is not a single-symbol fault
@pytest.mark.parametrize('status', [429, 418])
def test_rate_limited_stop_request_never_closes_the_position(w, status):
    w.prepare()
    w.ex.fail('POST', E.ALGO_ORDER, 'reject', when=only(S0), code=-1003, http_status=status, retry_after=30)
    w.rebalance()
    assert w.qty(S0) == -10 and not w.orders_sent(reduce_only=True)
    assert 'protection_blocked' in w.codes() and w.pause() is None
    assert (w.rt.store.get('ip_ban') is not None) == (status == 418)
    w.rt.store.delete('ip_ban')
    w.clock.now += 60
    w.guard()
    w.assert_protected(S0)


# 2 -- resume clears the pause only after a reconciliation that succeeded
def test_resume_keeps_the_pause_when_the_reconciliation_cannot_finish(w):
    w.open()
    reconcile.pause(w.rt, 'manual_pause')
    w.ex.fail('GET', E.OPEN_ALGO_ORDERS, 'unknown')
    assert reconcile.resume(w.rt)['status'] == 'blocked'
    assert w.pause()['reason'] == 'manual_pause'
    assert reconcile.resume(w.rt)['status'] == 'resumed' and w.pause() is None


# 3 -- one symbol that cannot be explained must not starve the others
def test_unclear_symbol_does_not_stop_protection_of_later_symbols(w):
    w.open()
    w.ex.qty[S0] = Decimal(-7)            # changed with no trade behind it
    w.stops(S1)[0]['algoStatus'] = 'EXPIRED'
    w.clock.now += 60
    assert w.guard()['status'] == 'failed'
    w.assert_protected(S1)


def test_foreign_trade_lookup_never_asks_for_more_than_a_week(w):
    w.open()
    w.clock.now += 20 * 86400
    w.ex.adl(S0, 4)
    w.guard()
    starts = [q['startTime'] for m, p, q in w.ex.calls if p == E.USER_TRADES and q['symbol'] == S0]
    assert starts and min(starts) >= (w.clock.now - 7 * 86400) * 1000
    assert w.qty(S0) == -6 and w.pause() is None


# 4 -- the minute guard starts in the same second as the rebalance
def test_rebalance_waits_for_the_guard_to_release_the_lock(w):
    w.prepare()
    lock = process_lock(w.state_dir)
    lock.__enter__()
    held, plain_sleep = [True], w.rt.sleep
    def sleep(seconds):
        if held:
            held.clear()
            lock.__exit__(None, None, None)
        plain_sleep(seconds)
    w.rt.sleep = sleep
    assert w.rebalance()['status'] == 'done' and w.qty(S0) == -10


def test_rebalance_that_never_gets_the_lock_alerts(w):
    w.prepare()
    with process_lock(w.state_dir):
        assert w.rebalance()['status'] == 'locked'
    assert not w.ex.writes() and any('锁' in text for text in w.sent)


# 5 -- alerts are plain text and one bad message cannot block the rest
def test_alert_text_is_escaped_for_telegram_markdown():
    assert cli.telegram_plain('原因代码 manual_takeover *x* [y] `z`') == '原因代码 manual\\_takeover \\*x\\* \\[y] \\`z\\`'


def test_one_undeliverable_message_does_not_block_later_alerts(w):
    w.rt.send = lambda text: 'poison' not in text and w.sent.append(text) is None
    notify.enqueue(w.rt, 'poison message')
    notify.enqueue(w.rt, 'urgent alert')
    notify.flush(w.rt)
    assert w.sent == ['urgent alert'] and len(w.rt.store.pending_notifications()) == 1


# 6 -- a planned add is re-checked right before it is sent
def test_stop_that_fires_during_the_run_voids_the_pending_add(w):
    adds_next_week(w)
    fired = []
    def trigger(params):
        if params['symbol'] == S0 and not fired:
            fired.append(1)
            w.ex.set_price(S1, 200)
            w.ex.set_price(S1, 80)
        return False
    w.ex.fail('POST', E.ORDER, 'hook', when=trigger)
    w.rebalance()
    assert fired and w.qty(S1) == 0
    assert len(w.orders_sent(S1, reduce_only=False)) == 1      # only the first week's opening order
    assert w.rt.store.get('cooldown:' + S1) is not None and 'add_voided' in w.codes()
    assert w.qty(S2) < -10


def test_pause_issued_during_the_run_stops_the_remaining_orders(w):
    adds_next_week(w)
    def stop_now(params):
        if params['symbol'] == S0 and w.pause() is None:
            reconcile.pause(w.rt, 'manual_pause')
        return False
    w.ex.fail('POST', E.ORDER, 'hook', when=stop_now)
    w.rebalance()
    assert w.qty(S1) == w.qty(S2) == -10 and w.qty(L0) == 5
    assert 'not_sent' in w.codes()


# 7 -- a week where nothing was sent can still be prepared and run again
def test_week_with_failed_preparation_can_be_rerun_after_the_data_is_fixed(w):
    w.prepare(fail='fisher_week_missing')
    assert w.rebalance()['status'] == 'no_new'
    assert w.prepare()['status'] == 'ready'
    assert w.rebalance()['status'] == 'deviated'
    assert w.qty(S0) == -10 and 'prep_failed' in w.codes()
    before = len(w.ex.writes())
    assert w.rebalance()['status'] == 'duplicate' and len(w.ex.writes()) == before


def test_transient_failure_before_any_order_leaves_the_week_runnable(w):
    w.prepare()
    w.ex.fail('GET', E.POSITION_RISK_V2, 'unknown')
    assert w.rebalance()['status'] == 'no_new'
    assert w.rebalance()['status'] == 'deviated' and w.qty(S0) == -10


# 8 -- a request that was never built is not an unknown outcome
def test_clock_sync_failure_before_sending_is_recorded_as_not_sent(w):
    acts = Actions(w.rt)
    action_id = acts.plan_stop(ANCHOR, S0, Decimal(1), Decimal(200), 'protect_new')
    def broken():
        raise ExchangeError('UNKNOWN', 'slow clock sample')
    w.ex.sync_clock = broken
    assert acts.send(action_id).state == 'NOT_ACCEPTED' and not w.ex.writes()


def test_stop_request_stuck_unknown_is_reported_as_overdue(w):
    acts = Actions(w.rt)
    w.ex.fail('POST', E.ALGO_ORDER, 'unknown')
    acts.send(acts.plan_stop(ANCHOR, S0, Decimal(1), Decimal(200), 'protect_new'))
    w.clock.now += C.CONVERGE_S + 1
    assert [a['kind'] for a in Desk(w.rt).overdue_unknowns()] == ['algo']


def test_clock_is_synced_at_most_once_per_half_minute():
    cli_, transport = client(Response({'serverTime': 1000000}), Response({}), Response({}),
                             credentials=Credentials('k', 's'))
    rt = type('RT', (), dict(client=cli_, clock=lambda self=None: 1000.0))()
    from trading.momentum_ls01.actions import fresh_clock
    fresh_clock(cli_)
    fresh_clock(cli_)
    assert len(transport.calls) == 1


# 9 -- the measured weight replaces the conservative reservation
def test_measured_weight_header_replaces_the_flat_private_reservation():
    replies = [Response({'serverTime': 1000000})] + [Response({}, headers={'X-MBX-USED-WEIGHT-1M': '5'})] * 6
    cli_, transport = client(*replies, credentials=Credentials('k', 's'), limiter=RateLimitState(limit=100))
    cli_.sync_clock()
    for _ in range(6):
        cli_.request('GET', E.ACCOUNT, signed=True)
    assert cli_.limiter.used_weight == 5


def test_soft_limit_block_waits_for_the_next_minute_instead_of_dropping_the_week(w):
    w.prepare()
    w.ex.fail('POST', E.ORDER, 'not_sent', when=only(S1), retry_after=20)
    started = w.clock.now
    assert w.rebalance()['status'] == 'done'
    assert w.qty(S1) == -10 and w.clock.now - started >= 20
    assert len(w.orders_sent(S1)) == 1      # the blocked attempt never reached the exchange


# 10 -- an order that did nothing is never a clean week
def test_terminal_order_without_fill_fields_is_not_recorded_as_a_fill(w):
    acts = Actions(w.rt)
    action_id = acts.plan_order(ANCHOR, S0, 'SELL', Decimal(1), 'wave_b_open', False, 0)
    acts.send(action_id)
    with pytest.raises(ExchangeError):
        acts._record_order(w.rt.store.action(action_id), {'orderId': 1, 'status': 'FILLED'})
    assert w.rt.store.action(action_id)['state'] == 'ACKED'


def test_order_that_expires_with_no_fill_is_a_material_deviation(w):
    w.prepare()
    w.ex.fill_fraction[S1] = '0'
    assert w.rebalance()['status'] == 'deviated'
    assert w.qty(S1) == 0 and 'order_no_fill' in w.codes()
