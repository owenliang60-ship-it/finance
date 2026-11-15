"""In-memory USD-M exchange for LS01 fault fixtures.

It answers at the ``BinanceClient.request`` boundary, so job code runs its real
request building and response parsing. Response fields follow exchange-api.md
(official documentation, read 2026-10-20). They are NOT recorded production
replies; gate 1b adds a contract test once real probe evidence exists.

One-way mode, USDT single asset. Shorts are negative quantities.
"""
from dataclasses import dataclass
from decimal import Decimal

from trading.momentum_ls01.binance import Credentials, Endpoints as E, ExchangeError

ORDER_NOT_FOUND = -2013
CANCEL_REJECTED = -2011
WOULD_TRIGGER = -2021
REDUCE_ONLY_REJECTED = -2022
FEE_RATE = Decimal('0.0005')
FAR_FUTURE_MS = 4133404800000


class Crash(BaseException):
    """Simulated process death. Deliberately not an Exception subclass."""


class FakeClock:
    def __init__(self, now):
        self.now = float(now)

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += float(seconds)


@dataclass
class Fault:
    method: str
    path: str
    effect: str          # lost | unknown | reject | crash_before | crash_after | hook | empty
    when: object = None  # predicate(params) -> bool
    code: int = None
    http_status: int = None
    retry_after: float = None
    times: int = 1


class FakeExchange:
    env = 'prod'
    credentials = Credentials('fake-key', 'fake-secret')
    clock_offset_ms = 0

    def __init__(self, clock, symbols=('AAAUSDT', 'BBBUSDT'), wallet='10000', price='100'):
        self.clock = clock
        self.wallet = Decimal(wallet)
        self.prices = {s: Decimal(price) for s in symbols}
        self.status = {s: 'TRADING' for s in symbols}
        self.delivery = {s: FAR_FUTURE_MS for s in symbols}
        self.qty = {s: Decimal(0) for s in symbols}
        self.entry = {s: Decimal(0) for s in symbols}
        self.leverage = {s: 20 for s in symbols}
        self.margin_type = {s: 'CROSSED' for s in symbols}
        self.fill_fraction = {}       # symbol -> fraction of a market order that fills
        self.stop_fill_fraction = {}  # symbol -> fraction of a triggered stop that fills
        self.ignore_leverage = set()  # symbols whose leverage change is acknowledged but not applied
        self.available_override = None
        self.dual = False
        self.multi_assets = False
        self.orders = {}   # clientOrderId -> order
        self.algos = {}    # clientAlgoId -> conditional order
        self.trades = []
        self.income = []
        self.calls = []
        self.faults = []
        self._seq = 1000

    # ---- test controls -------------------------------------------------
    def fail(self, method, path, effect, **kwargs):
        self.faults.append(Fault(method, path, effect, **kwargs))

    def writes(self, path=None):
        return [(m, p, dict(q)) for m, p, q in self.calls if m != 'GET' and (path is None or p == path)]

    def set_price(self, symbol, price):
        """Move the contract price and trigger any stop it crosses."""
        self.prices[symbol] = Decimal(str(price))
        for algo in list(self.algos.values()):
            if algo['symbol'] != symbol or algo['algoStatus'] != 'NEW':
                continue
            trigger = Decimal(algo['triggerPrice'])
            crossed = self.prices[symbol] >= trigger if algo['side'] == 'BUY' else self.prices[symbol] <= trigger
            if crossed:
                self._trigger(algo)

    def forget(self, symbol):
        """The exchange does not list this symbol at all: no price, no filters, no position row."""
        for book in (self.prices, self.status, self.delivery, self.qty, self.entry, self.leverage, self.margin_type):
            book.pop(symbol, None)

    def deposit(self, amount, tran_id=None):
        self.wallet += Decimal(str(amount))
        return self._income('TRANSFER', amount, '', tran_id=tran_id)

    def funding(self, symbol, amount):
        self.wallet += Decimal(str(amount))
        return self._income('FUNDING_FEE', amount, symbol)

    def external_fill(self, symbol, side, quantity, client_id):
        """A fill the system did not send: manual, liquidation, ADL or settlement."""
        order = self._new_order(dict(symbol=symbol, side=side, quantity=str(quantity),
                                     newClientOrderId=client_id, reduceOnly=False, type='MARKET'))
        self._fill(order, Decimal(str(quantity)))
        return order

    def manual_order(self, symbol, side, quantity):
        return self.external_fill(symbol, side, quantity, 'web_' + str(self._next()))

    def liquidate(self, symbol, quantity):
        return self.external_fill(symbol, 'BUY' if self.qty[symbol] < 0 else 'SELL', quantity,
                                  'autoclose-' + str(self._next()))

    def adl(self, symbol, quantity):
        return self.external_fill(symbol, 'BUY' if self.qty[symbol] < 0 else 'SELL', quantity, 'adl_autoclose')

    def settle(self, symbol):
        """Delisting settlement: the exchange closes the position and stops trading."""
        if self.qty[symbol]:
            self.external_fill(symbol, 'BUY' if self.qty[symbol] < 0 else 'SELL', abs(self.qty[symbol]),
                               'settlement_autoclose-' + str(self._next()))
        self.status[symbol] = 'SETTLING'
        for algo in self.algos.values():
            if algo['symbol'] == symbol and algo['algoStatus'] == 'NEW':
                algo['algoStatus'] = 'EXPIRED'

    # ---- client boundary -----------------------------------------------
    def sync_clock(self):
        return 0

    def request(self, method, path, params=None, signed=False, protective=False):
        params = dict(params or {})
        self.calls.append((method, path, params))
        fault = self._match_fault(method, path, params)
        if fault and fault.effect == 'crash_before':
            raise Crash()
        if fault and fault.effect == 'not_sent':
            self.calls.pop()   # it never left the process
            raise ExchangeError('NOT_SENT', 'injected: local gate', retry_after=fault.retry_after)
        if fault and fault.effect == 'unknown':
            raise ExchangeError('UNKNOWN', 'injected: request never reached the exchange')
        if fault and fault.effect == 'reject':
            raise ExchangeError('REJECTED', 'injected', code=fault.code, http_status=fault.http_status,
                                retry_after=fault.retry_after)
        if fault and fault.effect == 'empty':
            return []
        result = self._dispatch(method, path, params)
        if fault and fault.effect == 'crash_after':
            raise Crash()
        if fault and fault.effect == 'lost':
            raise ExchangeError('UNKNOWN', 'injected: accepted but response lost')
        return result

    def _match_fault(self, method, path, params):
        for fault in self.faults:
            if fault.method == method and fault.path == path and (fault.when is None or fault.when(params)):
                fault.times -= 1
                if fault.times <= 0:
                    self.faults.remove(fault)
                return fault
        return None

    def _dispatch(self, method, path, p):
        if path == E.TIME:
            return {'serverTime': self._ms()}
        if path == E.EXCHANGE_INFO:
            return {'rateLimits': [{'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE',
                                    'intervalNum': 1, 'limit': 2400}],
                    'symbols': [self._info(s) for s in self.prices]}
        if path == E.TICKER_PRICE:
            if p.get('symbol'):
                return {'symbol': p['symbol'], 'price': str(self.prices[p['symbol']]), 'time': self._ms()}
            return [{'symbol': s, 'price': str(v), 'time': self._ms()} for s, v in self.prices.items()]
        if path == E.MARK_PRICE:
            if p.get('symbol'):
                return {'symbol': p['symbol'], 'markPrice': str(self.prices[p['symbol']])}
            return [{'symbol': s, 'markPrice': str(v)} for s, v in self.prices.items()]
        if path == E.ACCOUNT:
            return self._account()
        if path == E.POSITION_MODE:
            return {'dualSidePosition': self.dual}
        if path == E.MULTI_ASSETS_MODE:
            return {'multiAssetsMargin': self.multi_assets}
        if path == E.SYMBOL_CONFIG:
            names = [p['symbol']] if p.get('symbol') else list(self.prices)
            return [{'symbol': s, 'marginType': self.margin_type[s], 'leverage': self.leverage[s]} for s in names]
        if path == E.LEVERAGE:
            if p['symbol'] not in self.ignore_leverage:
                self.leverage[p['symbol']] = int(p['leverage'])
            return {'symbol': p['symbol'], 'leverage': int(p['leverage'])}
        if path == E.MARGIN_TYPE:
            self.margin_type[p['symbol']] = p['marginType']
            return {'code': 200, 'msg': 'success'}
        if path == E.POSITION_RISK_V2:
            names = [p['symbol']] if p.get('symbol') else list(self.prices)
            return [self._position_row(s) for s in names]
        if path == E.POSITION_RISK:
            names = [p['symbol']] if p.get('symbol') else list(self.prices)
            return [self._position_row(s) for s in names if self.qty[s]]
        if path == E.OPEN_ORDERS:
            return [o for o in self.orders.values() if o['status'] == 'NEW'
                    and (not p.get('symbol') or o['symbol'] == p['symbol'])]
        if path == E.OPEN_ALGO_ORDERS:
            return [dict(a) for a in self.algos.values() if a['algoStatus'] == 'NEW'
                    and (not p.get('symbol') or a['symbol'] == p['symbol'])]
        if path == E.USER_TRADES:
            rows = [t for t in self.trades if t['symbol'] == p['symbol']
                    and t['id'] >= int(p.get('fromId', 0)) and t['time'] >= int(p.get('startTime', 0))
                    and (p.get('orderId') is None or t['orderId'] == int(p['orderId']))]
            return [dict(t) for t in rows[:int(p.get('limit', 500))]]
        if path == E.INCOME:
            rows = [i for i in self.income if i['time'] >= int(p.get('startTime', 0))
                    and i['time'] <= int(p.get('endTime', 2 ** 62))
                    and (not p.get('incomeType') or i['incomeType'] == p['incomeType'])]
            return [dict(i) for i in rows[:int(p.get('limit', 100))]]
        if path == E.ORDER:
            return self._order(method, p)
        if path == E.ALGO_ORDER:
            return self._algo(method, p)
        raise AssertionError('fake exchange has no handler for ' + method + ' ' + path)

    # ---- ordinary orders -----------------------------------------------
    def _order(self, method, p):
        if method == 'POST':
            if p['newClientOrderId'] in self.orders:
                raise ExchangeError('REJECTED', 'duplicate client order id', code=-4116)
            symbol, side, qty = p['symbol'], p['side'], Decimal(p['quantity'])
            if self.status[symbol] != 'TRADING':
                raise ExchangeError('REJECTED', 'symbol not trading', code=-4141)
            reduce_only = str(p.get('reduceOnly', False)).lower() == 'true'
            delta = qty if side == 'BUY' else -qty
            if reduce_only and (self.qty[symbol] * delta >= 0 or abs(delta) > abs(self.qty[symbol])):
                raise ExchangeError('REJECTED', 'reduce only rejected', code=REDUCE_ONLY_REJECTED)
            order = self._new_order(p)
            self._fill(order, qty * Decimal(str(self.fill_fraction.get(symbol, 1))))
            # Acknowledgement only: the caller must query for the fill.
            return {'orderId': order['orderId'], 'clientOrderId': order['clientOrderId'], 'symbol': symbol,
                    'status': 'NEW', 'executedQty': '0', 'avgPrice': '0'}
        order = self._find_order(p)
        if method == 'DELETE':
            if order['status'] != 'NEW':
                raise ExchangeError('REJECTED', 'unknown order sent', code=CANCEL_REJECTED)
            order['status'] = 'CANCELED'
        return dict(order)

    def _find_order(self, p):
        for order in self.orders.values():
            if order['symbol'] != p['symbol']:
                continue
            if order['clientOrderId'] == p.get('origClientOrderId') or str(order['orderId']) == str(p.get('orderId')):
                return order
        raise ExchangeError('REJECTED', 'order does not exist', code=ORDER_NOT_FOUND)

    def _new_order(self, p):
        order = {'orderId': self._next(), 'clientOrderId': p['newClientOrderId'], 'symbol': p['symbol'],
                 'side': p['side'], 'type': p.get('type', 'MARKET'), 'origQty': str(p['quantity']),
                 'reduceOnly': str(p.get('reduceOnly', False)).lower() == 'true',
                 'status': 'NEW', 'executedQty': '0', 'avgPrice': '0', 'updateTime': self._ms()}
        self.orders[order['clientOrderId']] = order
        return order

    def _fill(self, order, quantity):
        symbol, price = order['symbol'], self.prices[order['symbol']]
        quantity = min(Decimal(quantity), Decimal(order['origQty']))
        if quantity > 0:
            delta = quantity if order['side'] == 'BUY' else -quantity
            before = self.qty[symbol]
            after = before + delta
            realized = Decimal(0)
            if before * delta < 0:   # reducing
                closed = min(abs(delta), abs(before))
                realized = (price - self.entry[symbol]) * closed * (1 if before > 0 else -1)
                if before * after < 0:
                    self.entry[symbol] = price
            elif before == 0:
                self.entry[symbol] = price
            else:                    # adding: quantity-weighted average
                self.entry[symbol] = (abs(before) * self.entry[symbol] + abs(delta) * price) / abs(after)
            self.qty[symbol] = after
            if after == 0:
                self.entry[symbol] = Decimal(0)
            fee = quantity * price * FEE_RATE
            self.wallet += realized - fee
            trade = {'symbol': symbol, 'id': self._next(), 'orderId': order['orderId'], 'side': order['side'],
                     'price': str(price), 'qty': str(quantity), 'realizedPnl': str(realized),
                     'commission': str(fee), 'commissionAsset': 'USDT', 'time': self._ms()}
            self.trades.append(trade)
            self._income('COMMISSION', -fee, symbol, trade_id=trade['id'])
            if realized:
                self._income('REALIZED_PNL', realized, symbol, trade_id=trade['id'])
        order['executedQty'] = str(quantity)
        order['avgPrice'] = str(price) if quantity > 0 else '0'
        order['status'] = 'FILLED' if quantity == Decimal(order['origQty']) else 'EXPIRED'
        order['updateTime'] = self._ms()

    # ---- conditional orders --------------------------------------------
    def _algo(self, method, p):
        if method == 'POST':
            if p['clientAlgoId'] in self.algos:
                raise ExchangeError('REJECTED', 'duplicate client algo id', code=-4116)
            symbol, trigger = p['symbol'], Decimal(p['triggerPrice'])
            if 'closePosition' in p and 'quantity' in p:
                raise ExchangeError('REJECTED', 'closePosition with quantity', code=-4136)
            crossed = self.prices[symbol] >= trigger if p['side'] == 'BUY' else self.prices[symbol] <= trigger
            if crossed:
                raise ExchangeError('REJECTED', 'order would immediately trigger', code=WOULD_TRIGGER)
            algo = {'algoId': self._next(), 'clientAlgoId': p['clientAlgoId'], 'algoType': p['algoType'],
                    'orderType': p['type'], 'symbol': symbol, 'side': p['side'],
                    'positionSide': p.get('positionSide', 'BOTH'), 'quantity': str(p['quantity']),
                    'triggerPrice': str(p['triggerPrice']), 'workingType': p.get('workingType'),
                    'priceProtect': str(p.get('priceProtect', False)).lower() == 'true',
                    'reduceOnly': str(p.get('reduceOnly', False)).lower() == 'true',
                    'closePosition': False, 'algoStatus': 'NEW', 'actualOrderId': '',
                    'createTime': self._ms(), 'updateTime': self._ms()}
            self.algos[algo['clientAlgoId']] = algo
            return dict(algo)
        algo = next((a for a in self.algos.values()
                     if a['clientAlgoId'] == p.get('clientAlgoId') or str(a['algoId']) == str(p.get('algoId'))), None)
        if algo is None:
            raise ExchangeError('REJECTED', 'order does not exist', code=ORDER_NOT_FOUND)
        if method == 'DELETE':
            if algo['algoStatus'] != 'NEW':
                raise ExchangeError('REJECTED', 'unknown order sent', code=CANCEL_REJECTED)
            algo['algoStatus'] = 'CANCELED'
            algo['updateTime'] = self._ms()
        return dict(algo)

    def _trigger(self, algo):
        symbol = algo['symbol']
        held = self.qty[symbol]
        reducing = (held < 0 and algo['side'] == 'BUY') or (held > 0 and algo['side'] == 'SELL')
        child = self._new_order(dict(symbol=symbol, side=algo['side'], quantity=algo['quantity'],
                                     newClientOrderId='algo-child-' + str(algo['algoId']),
                                     reduceOnly=True, type='MARKET'))
        # A reduce-only stop larger than the position closes to zero, never reverses.
        size = min(Decimal(algo['quantity']), abs(held)) if reducing else Decimal(0)
        self._fill(child, size * Decimal(str(self.stop_fill_fraction.get(symbol, 1))))
        algo.update(algoStatus='FINISHED', actualOrderId=str(child['orderId']), updateTime=self._ms())

    # ---- account views -------------------------------------------------
    def _position_row(self, symbol):
        qty, entry, price = self.qty[symbol], self.entry[symbol], self.prices[symbol]
        return {'symbol': symbol, 'positionAmt': str(qty), 'entryPrice': str(entry),
                'breakEvenPrice': str(entry * (1 + FEE_RATE)) if qty else '0',
                'markPrice': str(price), 'unRealizedProfit': str((price - entry) * qty),
                'positionSide': 'BOTH', 'leverage': str(self.leverage[symbol]),
                'marginType': 'cross' if self.margin_type[symbol] == 'CROSSED' else 'isolated',
                'updateTime': self._ms()}

    def _account(self):
        unrealized = sum(((self.prices[s] - self.entry[s]) * q for s, q in self.qty.items()), Decimal(0))
        initial = sum((abs(q) * self.prices[s] / self.leverage[s] for s, q in self.qty.items()), Decimal(0))
        maintenance = sum((abs(q) * self.prices[s] * Decimal('0.01') for s, q in self.qty.items()), Decimal(0))
        margin_balance = self.wallet + unrealized
        available = max(Decimal(0), margin_balance - initial)
        if self.available_override is not None:
            available = Decimal(str(self.available_override))
        return {'totalWalletBalance': str(self.wallet), 'totalUnrealizedProfit': str(unrealized),
                'totalMarginBalance': str(margin_balance), 'totalInitialMargin': str(initial),
                'totalMaintMargin': str(maintenance), 'availableBalance': str(available),
                'assets': [{'asset': 'USDT', 'walletBalance': str(self.wallet)}]}

    def _info(self, symbol):
        return {'symbol': symbol, 'status': self.status[symbol], 'contractType': 'PERPETUAL',
                'quoteAsset': 'USDT', 'deliveryDate': self.delivery[symbol],
                'filters': [
                    {'filterType': 'PRICE_FILTER', 'minPrice': '0.01', 'maxPrice': '1000000', 'tickSize': '0.01'},
                    {'filterType': 'LOT_SIZE', 'minQty': '0.001', 'maxQty': '1000000', 'stepSize': '0.001'},
                    {'filterType': 'MARKET_LOT_SIZE', 'minQty': '0.001', 'maxQty': '100000', 'stepSize': '0.001'},
                    {'filterType': 'MIN_NOTIONAL', 'notional': '5'},
                    {'filterType': 'PERCENT_PRICE', 'multiplierUp': '1.05', 'multiplierDown': '0.95'}]}

    def _income(self, kind, amount, symbol, trade_id='', tran_id=None):
        row = {'symbol': symbol, 'incomeType': kind, 'income': str(amount), 'asset': 'USDT',
               'time': self._ms(), 'tranId': tran_id if tran_id is not None else self._next(),
               'tradeId': str(trade_id), 'info': kind}
        self.income.append(row)
        return row

    def _next(self):
        self._seq += 1
        return self._seq

    def _ms(self):
        return int(self.clock.time() * 1000)
