"""Request lifecycle details that the black-box fault fixtures do not pin down."""
from decimal import Decimal

import pandas as pd
import pytest

from tests.momentum_ls01_fake_exchange import FakeClock, FakeExchange
from trading.momentum_ls01.actions import Actions
from trading.momentum_ls01.binance import Endpoints as E
from trading.momentum_ls01.runtime import Runtime

ANCHOR = pd.Timestamp('2026-11-16 04:00', tz='UTC')


@pytest.fixture
def world(tmp_path):
    clock = FakeClock(ANCHOR.timestamp() + 1)
    ex = FakeExchange(clock)
    rt = Runtime(state_dir=tmp_path / 'state', client=ex, clock=clock.time, sleep=clock.sleep, send=lambda text: True)
    return rt, ex, clock, Actions(rt)


def test_quantities_and_prices_are_sent_in_plain_decimal_never_scientific(world):
    rt, ex, clock, acts = world
    order = rt.store.action(acts.plan_order(ANCHOR, 'AAAUSDT', 'SELL', Decimal('1.00E+4'), 'wave_b_open', False, 0))
    stop = rt.store.action(acts.plan_stop(ANCHOR, 'AAAUSDT', Decimal('1.00E+4'), Decimal('2E+2'), 'protect_new'))
    assert order['request']['params']['quantity'] == '10000' and order['qty'] == '10000'
    assert stop['request']['params']['quantity'] == '10000' and stop['request']['params']['triggerPrice'] == '200'


def test_client_ids_follow_the_plan_format_and_reissue_keeps_the_sequence(world):
    rt, ex, clock, acts = world
    first = rt.store.action(acts.plan_order(ANCHOR, 'AAAUSDT', 'SELL', Decimal('1'), 'wave_b_open', False, 0))
    assert first['client_id'] == 'mls1-26111604-B-0001-1' and first['lane'] == 'risk'
    assert first['request']['params']['newClientOrderId'] == first['client_id']
    with pytest.raises(ValueError):
        acts.reissue(first, ANCHOR)   # still PLANNED: not a refused request
    ex.fail('POST', E.ORDER, 'reject', code=-4005)
    refused = acts.send(first['id']).action
    second = rt.store.action(acts.reissue(refused, ANCHOR))
    assert second['client_id'] == 'mls1-26111604-B-0001-2' and second['replaces_client_id'] == first['client_id']
    assert second['request']['params']['newClientOrderId'] == second['client_id']
    assert rt.store.action(acts.plan_stop(ANCHOR, 'AAAUSDT', 1, 200, 'protect_new'))['lane'] == 'protect'


def test_not_found_is_not_a_refusal_until_three_spaced_replies(world):
    rt, ex, clock, acts = world
    action_id = acts.plan_order(ANCHOR, 'AAAUSDT', 'SELL', Decimal('1'), 'wave_b_open', False, 0)
    ex.fail('POST', E.ORDER, 'unknown')
    assert acts.send(action_id).state == 'UNKNOWN'
    for _ in range(5):                       # five immediate queries count as none
        assert acts.resolve(action_id)['state'] == 'UNKNOWN'
    assert rt.store.action(action_id)['check_count'] == 0
    for age, expected in ((10, 'UNKNOWN'), (30, 'UNKNOWN'), (119, 'UNKNOWN'), (120, 'NOT_ACCEPTED')):
        clock.now = ANCHOR.timestamp() + 1 + age
        assert acts.resolve(action_id)['state'] == expected


def test_not_found_with_a_changed_position_stays_unknown(world):
    rt, ex, clock, acts = world
    action_id = acts.plan_order(ANCHOR, 'AAAUSDT', 'SELL', Decimal('1'), 'wave_b_open', False, 0)
    ex.fail('POST', E.ORDER, 'unknown')
    acts.send(action_id)
    ex.manual_order('AAAUSDT', 'SELL', 1)    # something moved the position meanwhile
    for age in (10, 30, 120, 300):
        clock.now = ANCHOR.timestamp() + 1 + age
        assert acts.resolve(action_id)['state'] == 'UNKNOWN'


def test_acknowledged_order_missing_from_a_query_is_never_treated_as_refused(world):
    rt, ex, clock, acts = world
    action_id = acts.plan_order(ANCHOR, 'AAAUSDT', 'SELL', Decimal('1'), 'wave_b_open', False, 0)
    assert acts.send(action_id).state == 'ACKED'
    ex.fail('GET', E.ORDER, 'reject', code=-2013, times=10)
    for age in (10, 30, 120):
        clock.now = ANCHOR.timestamp() + 1 + age
        assert acts.resolve(action_id)['state'] == 'ACKED'
