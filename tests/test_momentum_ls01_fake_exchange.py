"""The fault fixtures are only as good as the fake. Pin the behaviours they rely on."""
from decimal import Decimal

import pytest

from tests.momentum_ls01_fake_exchange import (Crash, FakeClock, FakeExchange, ORDER_NOT_FOUND,
                                               REDUCE_ONLY_REJECTED, WOULD_TRIGGER)
from trading.momentum_ls01.binance import Endpoints as E, ExchangeError


@pytest.fixture
def ex():
    return FakeExchange(FakeClock(1_800_000_000))


def market(ex, symbol, side, qty, cid, reduce_only=False):
    return ex.request('POST', E.ORDER, dict(symbol=symbol, side=side, type='MARKET', quantity=str(qty),
                                            reduceOnly=reduce_only, newClientOrderId=cid), signed=True)


def stop(ex, symbol, qty, trigger, cid):
    return ex.request('POST', E.ALGO_ORDER, dict(algoType='CONDITIONAL', symbol=symbol, side='BUY',
                                                 type='STOP_MARKET', quantity=str(qty), triggerPrice=str(trigger),
                                                 workingType='CONTRACT_PRICE', reduceOnly=True, clientAlgoId=cid),
                      signed=True)


def position(ex, symbol):
    row = ex.request('GET', E.POSITION_RISK_V2, dict(symbol=symbol), signed=True)[0]
    return Decimal(row['positionAmt']), Decimal(row['entryPrice'])


def test_market_post_is_only_an_acknowledgement_and_query_shows_the_fill(ex):
    reply = market(ex, 'AAAUSDT', 'SELL', 2, 'c1')
    assert reply['status'] == 'NEW' and reply['executedQty'] == '0'
    order = ex.request('GET', E.ORDER, dict(symbol='AAAUSDT', origClientOrderId='c1'), signed=True)
    assert order['status'] == 'FILLED' and Decimal(order['executedQty']) == 2
    assert position(ex, 'AAAUSDT') == (Decimal(-2), Decimal(100))


def test_adding_averages_entry_and_reducing_keeps_it(ex):
    market(ex, 'AAAUSDT', 'SELL', 1, 'c1')
    ex.set_price('AAAUSDT', 120)
    market(ex, 'AAAUSDT', 'SELL', 1, 'c2')
    assert position(ex, 'AAAUSDT') == (Decimal(-2), Decimal(110))
    market(ex, 'AAAUSDT', 'BUY', 1, 'c3', reduce_only=True)
    assert position(ex, 'AAAUSDT') == (Decimal(-1), Decimal(110))


def test_flat_symbols_have_explicit_zero_rows_only_in_v2(ex):
    assert position(ex, 'BBBUSDT') == (Decimal(0), Decimal(0))
    assert ex.request('GET', E.POSITION_RISK, signed=True) == []


def test_reduce_only_cannot_open_or_exceed_the_position(ex):
    with pytest.raises(ExchangeError) as error:
        market(ex, 'AAAUSDT', 'BUY', 1, 'c1', reduce_only=True)
    assert error.value.code == REDUCE_ONLY_REJECTED
    market(ex, 'AAAUSDT', 'SELL', 1, 'c2')
    with pytest.raises(ExchangeError):
        market(ex, 'AAAUSDT', 'BUY', 2, 'c3', reduce_only=True)


def test_unknown_order_query_is_rejected_with_not_found(ex):
    with pytest.raises(ExchangeError) as error:
        ex.request('GET', E.ORDER, dict(symbol='AAAUSDT', origClientOrderId='nope'), signed=True)
    assert error.value.outcome == 'REJECTED' and error.value.code == ORDER_NOT_FOUND


def test_stop_already_crossed_is_rejected_as_immediate_trigger(ex):
    market(ex, 'AAAUSDT', 'SELL', 1, 'c1')
    with pytest.raises(ExchangeError) as error:
        stop(ex, 'AAAUSDT', 1, 100, 's1')
    assert error.value.code == WOULD_TRIGGER


def test_two_quantity_stops_coexist_and_oversized_stop_only_closes_to_zero(ex):
    market(ex, 'AAAUSDT', 'SELL', 1, 'c1')
    stop(ex, 'AAAUSDT', 1, 200, 's1')
    stop(ex, 'AAAUSDT', 3, 210, 's2')
    assert len(ex.request('GET', E.OPEN_ALGO_ORDERS, dict(symbol='AAAUSDT'), signed=True)) == 2
    ex.request('DELETE', E.ALGO_ORDER, dict(clientAlgoId='s1'), signed=True)
    ex.set_price('AAAUSDT', 210)
    algo = ex.request('GET', E.ALGO_ORDER, dict(clientAlgoId='s2'), signed=True)
    assert algo['algoStatus'] == 'FINISHED' and algo['actualOrderId']
    child = ex.request('GET', E.ORDER, dict(symbol='AAAUSDT', orderId=algo['actualOrderId']), signed=True)
    assert Decimal(child['executedQty']) == 1
    assert position(ex, 'AAAUSDT')[0] == 0


def test_partial_stop_fill_leaves_a_residual_short(ex):
    market(ex, 'AAAUSDT', 'SELL', 2, 'c1')
    stop(ex, 'AAAUSDT', 2, 200, 's1')
    ex.stop_fill_fraction['AAAUSDT'] = '0.5'
    ex.set_price('AAAUSDT', 200)
    assert position(ex, 'AAAUSDT')[0] == -1


def test_lost_response_still_executes_and_never_sent_does_not(ex):
    ex.fail('POST', E.ORDER, 'lost')
    with pytest.raises(ExchangeError) as error:
        market(ex, 'AAAUSDT', 'SELL', 1, 'c1')
    assert error.value.outcome == 'UNKNOWN' and position(ex, 'AAAUSDT')[0] == -1
    ex.fail('POST', E.ORDER, 'unknown')
    with pytest.raises(ExchangeError):
        market(ex, 'AAAUSDT', 'SELL', 1, 'c2')
    assert position(ex, 'AAAUSDT')[0] == -1


def test_crash_is_not_catchable_as_exception(ex):
    ex.fail('POST', E.ORDER, 'crash_after')
    with pytest.raises(Crash):
        try:
            market(ex, 'AAAUSDT', 'SELL', 1, 'c1')
        except Exception:  # production code catching Exception must not swallow a crash
            pytest.fail('crash was swallowed')
    assert position(ex, 'AAAUSDT')[0] == -1


def test_fills_fees_and_transfers_appear_once_in_income_and_wallet(ex):
    market(ex, 'AAAUSDT', 'SELL', 1, 'c1')
    ex.deposit(500, tran_id=77)
    rows = ex.request('GET', E.INCOME, signed=True)
    assert [r['incomeType'] for r in rows] == ['COMMISSION', 'TRANSFER']
    account = ex.request('GET', E.ACCOUNT, signed=True)
    assert Decimal(account['totalWalletBalance']) == Decimal('10000') - Decimal('0.05') + 500


def test_external_fills_carry_exchange_client_id_prefixes(ex):
    market(ex, 'AAAUSDT', 'SELL', 2, 'c1')
    liquidation = ex.liquidate('AAAUSDT', 1)
    assert liquidation['clientOrderId'].startswith('autoclose-') and position(ex, 'AAAUSDT')[0] == -1
    ex.settle('AAAUSDT')
    assert position(ex, 'AAAUSDT')[0] == 0
    info = ex.request('GET', E.EXCHANGE_INFO)
    assert next(s for s in info['symbols'] if s['symbol'] == 'AAAUSDT')['status'] == 'SETTLING'
