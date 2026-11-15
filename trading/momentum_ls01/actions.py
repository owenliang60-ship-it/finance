"""Request lifecycle (implementation plan §3.3).

PLANNED -> SENDING -> ACKED | UNKNOWN | REJECTED | NOT_ACCEPTED
ACKED/UNKNOWN -> (query) -> FILLED | PARTIAL_FINAL | CANCELED | EXPIRED | REJECTED | NOT_ACCEPTED
PLANNED -> ABANDONED (restart only)

Rules this module enforces:
- The row is committed as SENDING before the HTTP call. A failed commit means no call.
- An unknown outcome is never resent. It is queried by client id.
- "Not accepted" needs three "does not exist" replies at >=10s, >=30s, >=120s after
  sending, plus an unchanged position and no fills for that symbol.
- Terminal rows never change. A reissue is a new row linked to the old one.
"""
from decimal import Decimal

from . import config as C
from .binance import Endpoints as E, ExchangeError
from .store import TERMINAL_STATES

ORDER_TERMINAL = {'FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH', 'REJECTED'}
CLOCK_FRESH_S = 30
ALGO_TERMINAL = {'CANCELED', 'EXPIRED', 'REJECTED'}
PURPOSE_LETTER = {'wave_a_reduce': 'A', 'wave_a_close': 'A', 'wave_b_open': 'B', 'wave_b_add': 'B',
                  'protect_new': 'P', 'protect_replace': 'P', 'protect_cancel': 'P',
                  'stop_residual_exit': 'X', 'crossing_exit': 'X', 'fault_reduce': 'F', 'fault_exit': 'F',
                  'leverage_set': 'S', 'margin_set': 'S'}
PROTECT_LANE = {'P', 'X', 'F'}
DONE = 'FILLED'   # settings and cancels have no fill; FILLED means "took effect"


def plain(value):
    """Fixed-point text. str(Decimal) can give '1.00E+4', which is not a valid order quantity."""
    return format(Decimal(str(value)), 'f')


def fresh_clock(client):
    """Sync the request clock unless a sample from the last half minute exists."""
    synced = getattr(client, 'clock_synced_at', None)
    if synced is not None and 0 <= client.clock() - synced < CLOCK_FRESH_S:
        return
    client.sync_clock()


def anchor_tag(anchor):
    return anchor.strftime('%y%m%d%H')


class Sent:
    """Outcome of one send. ``error`` carries HTTP status and Retry-After for 429/418."""
    def __init__(self, action, error=None):
        self.action, self.error = action, error

    @property
    def state(self):
        return self.action['state']


class Actions:
    def __init__(self, rt):
        self.rt, self.store, self.client = rt, rt.store, rt.client
        self.fill_time = {}   # action id -> exchange time (s) of a triggered stop's child order

    # ---- planning ------------------------------------------------------
    def _plan(self, kind, purpose, anchor, symbol, side, qty, method, path, params, id_field=None,
              position_before=None, replaces=None, attempt=1, base_id=None):
        letter = PURPOSE_LETTER[purpose]
        tag = anchor_tag(anchor)
        with self.store.tx():
            if base_id is None:
                base_id = '%s%s-%s-%04d' % (C.ID_PREFIX, tag, letter, self.store.next_sequence(tag))
            client_id = '%s-%d' % (base_id, attempt)
            if id_field:
                params = dict(params, **{id_field: client_id})
            request = dict(method=method, path=path, params=params, id_field=id_field,
                           position_before=None if position_before is None else str(position_before))
            return self.store.plan_action(client_id, kind, purpose, anchor, symbol, side, qty,
                                          'protect' if letter in PROTECT_LANE else 'risk', request, replaces=replaces)

    def plan_order(self, anchor, symbol, side, qty, purpose, reduce_only, position_before):
        params = dict(symbol=symbol, side=side, type='MARKET', positionSide='BOTH', quantity=plain(qty),
                      reduceOnly=bool(reduce_only))
        return self._plan('order', purpose, anchor, symbol, side, plain(qty), 'POST', E.ORDER, params,
                          id_field='newClientOrderId', position_before=position_before)

    def plan_stop(self, anchor, symbol, qty, trigger, purpose, replaces=None):
        params = dict(symbol=symbol, side='BUY', positionSide='BOTH', algoType='CONDITIONAL', type='STOP_MARKET',
                      quantity=plain(qty), triggerPrice=plain(trigger), reduceOnly=True,
                      workingType='CONTRACT_PRICE', priceProtect=False)
        return self._plan('algo', purpose, anchor, symbol, 'BUY', plain(qty), 'POST', E.ALGO_ORDER, params,
                          id_field='clientAlgoId', replaces=replaces)

    def plan_cancel_stop(self, anchor, symbol, target_client_id):
        return self._plan('cancel_algo', 'protect_cancel', anchor, symbol, None, None, 'DELETE', E.ALGO_ORDER,
                          dict(clientAlgoId=target_client_id))

    def plan_setting(self, anchor, symbol, kind):
        if kind == 'leverage':
            return self._plan('leverage', 'leverage_set', anchor, symbol, None, None, 'POST', E.LEVERAGE,
                              dict(symbol=symbol, leverage=C.LEVERAGE))
        return self._plan('margin_type', 'margin_set', anchor, symbol, None, None, 'POST', E.MARGIN_TYPE,
                          dict(symbol=symbol, marginType=C.MARGIN_TYPE))

    def reissue(self, action, anchor):
        """New id, same sequence, next attempt. Only for NOT_ACCEPTED or REJECTED."""
        if action['state'] not in ('NOT_ACCEPTED', 'REJECTED'):
            raise ValueError('only a refused request may be reissued')
        base, attempt = action['client_id'].rsplit('-', 1)
        request = action['request']
        params = {k: v for k, v in request['params'].items() if k != request['id_field']}
        return self._plan(action['kind'], action['purpose'], anchor, action['symbol'], action['side'], action['qty'],
                          request['method'], request['path'], params, id_field=request['id_field'],
                          position_before=request['position_before'], replaces=action['client_id'],
                          attempt=int(attempt) + 1, base_id=base)

    # ---- sending -------------------------------------------------------
    def send(self, action_id):
        self.store.mark_sending(action_id)   # raises -> nothing was sent
        action = self.store.action(action_id)
        request = action['request']
        try:
            fresh_clock(self.client)
        except ExchangeError as error:   # nothing was built or sent
            self.store.update_action(action_id, state='NOT_ACCEPTED', error_msg='blocked before sending')
            return Sent(self.store.action(action_id), error)
        try:
            reply = self.client.request(request['method'], request['path'], request['params'], signed=True,
                                        protective=action['lane'] == 'protect')
        except ExchangeError as error:
            if error.outcome == 'NOT_SENT':
                self.store.update_action(action_id, state='NOT_ACCEPTED', error_msg='blocked before sending')
            elif error.outcome == 'REJECTED':
                self.store.update_action(action_id, state='REJECTED', error_code=error.code,
                                         error_msg='http %s' % error.http_status)
            else:
                self.store.update_action(action_id, state='UNKNOWN')
            return Sent(self.store.action(action_id), error)
        if action['kind'] == 'order':
            self.store.update_action(action_id, state='ACKED', exchange_id=reply.get('orderId'))
        elif action['kind'] == 'algo':
            self.store.update_action(action_id, state='ACKED', exchange_id=reply.get('algoId'))
        else:
            self.store.update_action(action_id, state=DONE)
        return Sent(self.store.action(action_id))

    # ---- resolving -----------------------------------------------------
    def resolve(self, action_id):
        """Query the exchange once and move the row as far as the reply proves."""
        action = self.store.action(action_id)
        if action['state'] in TERMINAL_STATES or action['state'] == 'PLANNED':
            return action
        if action['state'] == 'SENDING':
            self.store.update_action(action_id, state='UNKNOWN')
        handler = {'order': self._resolve_order, 'algo': self._resolve_algo, 'cancel_algo': self._resolve_cancel,
                   'leverage': self._resolve_setting, 'margin_type': self._resolve_setting}[action['kind']]
        try:
            handler(self.store.action(action_id))
        except ExchangeError as error:
            if error.outcome == 'REJECTED' and error.code == C.ORDER_NOT_FOUND:
                self._not_found(self.store.action(action_id))
            else:
                self.store.update_action(action_id, last_checked_at=self.rt.clock())
        return self.store.action(action_id)

    def _get(self, path, params):
        fresh_clock(self.client)
        return self.client.request('GET', path, params, signed=True, protective=True)

    def _resolve_order(self, action):
        reply = self._get(E.ORDER, dict(symbol=action['symbol'], origClientOrderId=action['client_id']))
        if reply.get('clientOrderId') != action['client_id'] or reply.get('symbol') != action['symbol']:
            raise ExchangeError('UNKNOWN', 'order identity mismatch')
        self._record_order(action, reply)

    def _record_order(self, action, reply):
        status = reply.get('status')
        if status in ORDER_TERMINAL and ('executedQty' not in reply or 'avgPrice' not in reply):
            raise ExchangeError('UNKNOWN', 'terminal order reply without fill fields')   # never assume zero
        executed = Decimal(str(reply.get('executedQty', '0')))
        fields = dict(exchange_id=reply.get('orderId'), executed_qty=executed, avg_price=reply.get('avgPrice'),
                      last_checked_at=self.rt.clock())
        if status == 'FILLED':
            fields['state'] = 'FILLED'
        elif status in ORDER_TERMINAL:
            fields['state'] = ('PARTIAL_FINAL' if executed > 0
                               else 'EXPIRED' if status.startswith('EXPIRED') else status)
        else:
            fields['state'] = 'ACKED'
        self.store.update_action(action['id'], **fields)

    def _resolve_algo(self, action):
        reply = self._get(E.ALGO_ORDER, dict(clientAlgoId=action['client_id']))
        if reply.get('clientAlgoId') != action['client_id']:
            raise ExchangeError('UNKNOWN', 'conditional order identity mismatch')
        status, child = reply.get('algoStatus'), reply.get('actualOrderId')
        fields = dict(exchange_id=reply.get('algoId'), last_checked_at=self.rt.clock(), state='ACKED')
        if child:
            # Triggered. The parent's status proves nothing about the fill: read the child order.
            fields['child_order_id'] = child
            self.store.update_action(action['id'], **fields)
            order = self._get(E.ORDER, dict(symbol=action['symbol'], orderId=child))
            if order.get('status') in ORDER_TERMINAL and 'executedQty' in order and 'avgPrice' in order:
                executed = Decimal(str(order['executedQty']))
                if order.get('updateTime'):
                    self.fill_time[action['id']] = int(order['updateTime']) / 1000
                self.store.update_action(
                    action['id'], executed_qty=executed, avg_price=order.get('avgPrice'),
                    state='FILLED' if executed == Decimal(action['qty']) else 'PARTIAL_FINAL')
            return
        if status in ALGO_TERMINAL:
            fields['state'] = status
        self.store.update_action(action['id'], **fields)

    def _resolve_cancel(self, action):
        target = self._get(E.ALGO_ORDER, dict(clientAlgoId=action['request']['params']['clientAlgoId']))
        still_working = target.get('algoStatus') == 'NEW'
        self.store.update_action(action['id'], state='NOT_ACCEPTED' if still_working else DONE,
                                 last_checked_at=self.rt.clock())

    def _resolve_setting(self, action):
        row = self._get(E.SYMBOL_CONFIG, dict(symbol=action['symbol']))[0]
        applied = (int(row['leverage']) == C.LEVERAGE if action['kind'] == 'leverage'
                   else row['marginType'] == C.MARGIN_TYPE)
        self.store.update_action(action['id'], state=DONE if applied else 'NOT_ACCEPTED',
                                 last_checked_at=self.rt.clock())

    def _not_found(self, action):
        """Count a "does not exist" reply only when it is old enough to mean something."""
        now = self.rt.clock()
        if action['state'] == 'ACKED':   # the exchange acknowledged it; a missing row is not a refusal
            self.store.update_action(action['id'], last_checked_at=now)
            return
        count = action['check_count']
        age = now - (action['sent_at'] or now)
        if count < len(C.NOT_ACCEPTED_AFTER_S) and age >= C.NOT_ACCEPTED_AFTER_S[count]:
            count += 1
        fields = dict(check_count=count, last_checked_at=now)
        if count >= len(C.NOT_ACCEPTED_AFTER_S) and self._no_effect(action):
            fields['state'] = 'NOT_ACCEPTED'
        self.store.update_action(action['id'], **fields)

    def _no_effect(self, action):
        if action['kind'] != 'order':
            return True
        before = action['request'].get('position_before')
        if before is None:
            return False
        rows = self._get(E.POSITION_RISK_V2, dict(symbol=action['symbol']))
        row = next((r for r in rows if r.get('symbol') == action['symbol']), None)
        if row is None or Decimal(str(row['positionAmt'])) != Decimal(before):
            return False
        trades = self._get(E.USER_TRADES, dict(symbol=action['symbol'],
                                               startTime=int((action['sent_at'] - 2) * 1000), limit=1000))
        return not trades

    # ---- restart -------------------------------------------------------
    def recover(self):
        """Run before any new request. Returns the rows that are still unresolved."""
        for action in self.store.actions(state='PLANNED'):
            self.store.update_action(action['id'], state='ABANDONED', error_msg='process restarted before sending')
            if action['anchor_utc'] and self.store.week(action['anchor_utc']):
                self.store.add_deviation(action['anchor_utc'], 'abandoned', symbol=action['symbol'],
                                         client_id=action['client_id'])
        for action in self.store.actions(state='SENDING'):
            self.store.update_action(action['id'], state='UNKNOWN')
        return self.store.actions(state='UNKNOWN')
