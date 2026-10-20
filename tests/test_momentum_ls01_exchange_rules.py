from decimal import Decimal
import pytest
from trading.momentum_ls01.exchange_rules import SymbolRules, RuleViolation


def metadata():
    return {'symbol': 'BTCUSDT', 'status': 'TRADING', 'contractType': 'PERPETUAL', 'quoteAsset': 'USDT',
            'deliveryDate': 9999999999999, 'filters': [
                {'filterType': 'PRICE_FILTER', 'minPrice': '0.10', 'maxPrice': '1000', 'tickSize': '0.10'},
                {'filterType': 'LOT_SIZE', 'minQty': '0.001', 'maxQty': '100', 'stepSize': '0.001'},
                {'filterType': 'MARKET_LOT_SIZE', 'minQty': '0.01', 'maxQty': '10', 'stepSize': '0.01'},
                {'filterType': 'MIN_NOTIONAL', 'notional': '5'},
                {'filterType': 'PERCENT_PRICE', 'multiplierUp': '1.1', 'multiplierDown': '0.9'}]}


def test_decimal_rounding_and_market_filter():
    r = SymbolRules.from_exchange_info(metadata())
    assert r.round_price('12.299') == Decimal('12.2')
    assert r.round_price('12.201', up=True) == Decimal('12.3')
    assert r.round_quantity('0.01999', market=True) == Decimal('0.01')
    assert r.round_quantity('0.01999', market=False) == Decimal('0.019')
    r.validate_order('0.5', price='10', mark_price='10', now_ms=1)


@pytest.mark.parametrize('qty,price,mark,expected', [
    ('0.019', '100', '100', 'step'), ('11', '10', '10', 'quantity'),
    ('0.01', '10', '10', 'notional'), ('1', '10.01', '10', 'tick'),
    ('1', '11.1', '10', 'percent'), ('1', '8.9', '10', 'percent'),
    ('0', '10', '10', 'positive'), ('NaN', '10', '10', 'finite'),
    ('1', 'Infinity', '10', 'finite'), ('1', '10', '0', 'positive'),
])
def test_invalid_order_is_rejected(qty, price, mark, expected):
    r = SymbolRules.from_exchange_info(metadata())
    with pytest.raises(RuleViolation, match=expected):
        r.validate_order(qty, price=price, mark_price=mark, now_ms=1)


def test_market_order_notional_uses_mark_and_reduce_only_is_exempt():
    r = SymbolRules.from_exchange_info(metadata())
    with pytest.raises(RuleViolation, match='notional'):
        r.validate_order('0.01', mark_price='10', now_ms=1)
    r.validate_order('0.01', mark_price='10', reduce_only=True, now_ms=1)


@pytest.mark.parametrize('field,value', [('status', 'SETTLING'), ('deliveryDate', 1000), ('contractType','CURRENT_QUARTER'), ('quoteAsset','USDC')])
def test_noneligible_symbol_excludes_new_positions(field, value):
    row = metadata(); row[field] = value
    r = SymbolRules.from_exchange_info(row)
    assert not r.can_open(1000)
    with pytest.raises(RuleViolation, match='eligible'):
        r.validate_order('1', mark_price='10', now_ms=1000)


@pytest.mark.parametrize('field,value', [('tickSize','0'), ('maxPrice','NaN'), ('minPrice','-1'), ('minPrice','2000')])
def test_corrupt_filter_fails_closed(field, value):
    row = metadata(); row['filters'][0][field] = value
    with pytest.raises(RuleViolation):
        SymbolRules.from_exchange_info(row)


def test_missing_duplicate_and_noninteger_delivery_fail_closed():
    for transform in [lambda r: r['filters'].pop(), lambda r: r['filters'].append(r['filters'][0]), lambda r: r.update(deliveryDate='1.5')]:
        row = metadata(); transform(row)
        with pytest.raises(RuleViolation):
            SymbolRules.from_exchange_info(row)


def test_percent_price_is_side_specific():
    r = SymbolRules.from_exchange_info(metadata())
    r.validate_order('1', price='8', mark_price='10', market=False, side='BUY', now_ms=1)
    r.validate_order('1', price='12', mark_price='10', market=False, side='SELL', now_ms=1)
    with pytest.raises(RuleViolation, match='percent'):
        r.validate_order('1', price='12', mark_price='10', market=False, side='BUY', now_ms=1)
    with pytest.raises(RuleViolation, match='percent'):
        r.validate_order('1', price='8', mark_price='10', market=False, side='SELL', now_ms=1)


def test_market_must_satisfy_both_quantity_filters():
    row = metadata()
    row['filters'][1]['stepSize'] = '0.03'
    r = SymbolRules.from_exchange_info(row)
    assert r.round_quantity('0.08', market=True) == Decimal('0.06')
    with pytest.raises(RuleViolation, match='step'):
        r.validate_order('0.08', mark_price='100', now_ms=1)
