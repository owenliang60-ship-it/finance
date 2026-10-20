"""Strict Decimal validation for USD-M symbol filters; never round orders upward."""
from dataclasses import dataclass
from math import lcm
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR


class RuleViolation(ValueError):
    pass


def _positive(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RuleViolation('numeric value required') from None
    if not result.is_finite():
        raise RuleViolation('finite value required')
    if result <= 0:
        raise RuleViolation('positive value required')
    return result


@dataclass(frozen=True)
class Bounds:
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    @classmethod
    def parse(cls, row, minimum, maximum, step):
        result = cls(*(_positive(row[key]) for key in (minimum, maximum, step)))
        if result.minimum > result.maximum or result.step > result.maximum:
            raise RuleViolation('inconsistent filter bounds')
        return result

    def rounded(self, value, up=False):
        value = _positive(value)
        return (value / self.step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * self.step

    def validate(self, value, label):
        if not self.minimum <= value <= self.maximum:
            raise RuleViolation(f'{label} outside filter bounds')
        if value % self.step != 0:
            raise RuleViolation('price tick mismatch' if label == 'price' else 'quantity step mismatch')


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    status: str
    contract_type: str
    quote_asset: str
    delivery_ms: int
    price: Bounds
    lot: Bounds
    market_lot: Bounds
    min_notional: Decimal
    multiplier_up: Decimal
    multiplier_down: Decimal

    @classmethod
    def from_exchange_info(cls, row):
        try:
            filters = {item['filterType']: item for item in row['filters']}
            if len(filters) != len(row['filters']):
                raise RuleViolation('duplicate filters')
            delivery = _positive(row['deliveryDate'])
            if delivery != delivery.to_integral_value():
                raise RuleViolation('integer deliveryDate required')
            result = cls(
                symbol=row['symbol'], status=row['status'], contract_type=row['contractType'],
                quote_asset=row['quoteAsset'], delivery_ms=int(delivery),
                price=Bounds.parse(filters['PRICE_FILTER'], 'minPrice', 'maxPrice', 'tickSize'),
                lot=Bounds.parse(filters['LOT_SIZE'], 'minQty', 'maxQty', 'stepSize'),
                market_lot=Bounds.parse(filters['MARKET_LOT_SIZE'], 'minQty', 'maxQty', 'stepSize'),
                min_notional=_positive(filters['MIN_NOTIONAL']['notional']),
                multiplier_up=_positive(filters['PERCENT_PRICE']['multiplierUp']),
                multiplier_down=_positive(filters['PERCENT_PRICE']['multiplierDown']))
            if result.multiplier_down > result.multiplier_up:
                raise RuleViolation('inconsistent percent price bounds')
            if not all(isinstance(v, str) and v for v in (result.symbol, result.status, result.contract_type, result.quote_asset)):
                raise RuleViolation('invalid symbol metadata')
            return result
        except (KeyError, TypeError):
            raise RuleViolation('missing or malformed required exchange filter') from None

    def can_open(self, now_ms):
        return (self.status == 'TRADING' and self.contract_type == 'PERPETUAL'
                and self.quote_asset == 'USDT' and self.delivery_ms > now_ms)

    def round_quantity(self, quantity, *, market=True):
        if not market:
            return self.lot.rounded(quantity)
        scale = Decimal(10) ** max(-self.lot.step.as_tuple().exponent, -self.market_lot.step.as_tuple().exponent)
        step = Decimal(lcm(int(self.lot.step * scale), int(self.market_lot.step * scale))) / scale
        return (_positive(quantity) / step).to_integral_value(rounding=ROUND_FLOOR) * step

    def round_price(self, price, *, up=False):
        return self.price.rounded(price, up=up)

    def validate_order(self, quantity, *, mark_price, now_ms, price=None, market=True, reduce_only=False, side=None):
        """For MARKET supply mark only; LIMIT supplies price and market=False.

        reduce_only skips entry eligibility and minimum notional, never size filters.
        Pass side for the exchange's directional percent bounds; absent side uses
        the stricter two-sided band. Trigger price is separate from LIMIT price.
        """
        if side not in (None, 'BUY', 'SELL'):
            raise RuleViolation('side must be BUY or SELL')
        qty, mark = _positive(quantity), _positive(mark_price)
        if not reduce_only and not self.can_open(now_ms):
            raise RuleViolation('symbol is not eligible for opening')
        self.lot.validate(qty, 'quantity')
        if market:
            self.market_lot.validate(qty, 'quantity')
        effective = mark
        if price is not None:
            effective = _positive(price)
            self.price.validate(effective, 'price')
            if ((side in (None, 'BUY') and effective > mark * self.multiplier_up)
                    or (side in (None, 'SELL') and effective < mark * self.multiplier_down)):
                raise RuleViolation('percent price bounds exceeded')
        elif not market:
            raise RuleViolation('limit price required')
        if not reduce_only and qty * effective < self.min_notional:
            raise RuleViolation('minimum notional not met')
