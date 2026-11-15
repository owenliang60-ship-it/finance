"""Stop protection for shorts (north star §5).

BUY STOP_MARKET, quantity-based, reduceOnly, contract price, no price protection.
Replace by placing the new full-quantity stop first and cancelling the old one
only after the new one is confirmed working. Until gate 1b decides otherwise,
every quantity change is replaced with the exact quantity.
"""
from decimal import Decimal

from . import config as C, notify


def stop_price(entry, rules):
    """Twice the entry price, rounded UP to tick so the stop never fires early."""
    return rules.round_price(Decimal(entry) * C.STOP_MULTIPLE, up=True)


def ensure(desk, symbol, qty, entry, stops):
    """Make exactly one working stop cover the short. Returns one of:

    ok        one matching stop is working
    exited    price was already past the stop; the position was closed as a normal stop
    rejected  the exchange refused the new stop; recorded in desk.rejections
    covered   a replacement was refused recently, but an old stop still covers the whole short
    blocked   a local gate stopped the request before it was sent
    triggered the new stop fired while being confirmed
    unknown   the new stop's outcome is not known; no further stop is placed
    """
    store, acts = desk.store, desk.acts
    want_qty = -Decimal(qty)
    trigger = stop_price(entry, desk.rules[symbol])
    matching = [s for s in stops if Decimal(str(s['quantity'])) == want_qty
                and Decimal(str(s['triggerPrice'])) == trigger]
    if matching:
        keep = matching[0]
        desk.cancel_stops(symbol, [s for s in stops if s is not keep])
        store.set('protection:' + symbol, dict(client_id=keep['clientAlgoId'], qty=str(want_qty), trigger=str(trigger)))
        return 'ok'
    covering = [s for s in stops if Decimal(str(s['quantity'])) >= want_qty]
    hold = store.get('protect_hold:' + symbol)
    if covering and hold and hold['until'] > desk.rt.clock():
        return 'covered'   # do not hammer the exchange; retry when the hold runs out
    if desk.price(symbol) >= trigger:
        return desk.crossing_exit(symbol)
    action_id = acts.plan_stop(desk.anchor, symbol, want_qty, trigger,
                               'protect_replace' if stops else 'protect_new',
                               replaces=stops[0]['clientAlgoId'] if stops else None)
    sent = acts.send(action_id)
    if sent.state == 'REJECTED':
        if sent.action['error_code'] == C.WOULD_TRIGGER:
            return desk.crossing_exit(symbol)
        if covering:
            until = desk.rt.clock() + C.PROTECT_RETRY_S
            store.set('protect_hold:' + symbol, dict(until=until, code=sent.action['error_code']))
            notify.enqueue(desk.rt, '⚠️ %s 换止损被拒（代码 %s）。旧止损仍覆盖全部空头，仓位保留；%d 分钟后重试。'
                           % (symbol, sent.action['error_code'], C.PROTECT_RETRY_S // 60),
                           key='covered:%s:%d' % (symbol, until))
        desk.rejections.append(dict(symbol=symbol, code=sent.action['error_code'], covered=bool(covering),
                                    http_status=getattr(sent.error, 'http_status', None)))
        return 'rejected'
    if sent.state == 'NOT_ACCEPTED':
        return 'blocked'
    after = desk.settle_action(action_id)   # confirm before touching the old stop
    if after['state'] == 'ACKED' and not after['child_order_id']:
        desk.cancel_stops(symbol, stops)
        store.delete('protect_hold:' + symbol)
        store.set('protection:' + symbol, dict(client_id=after['client_id'], qty=str(want_qty), trigger=str(trigger)))
        return 'ok'
    if after['child_order_id'] or after['state'] in ('FILLED', 'PARTIAL_FINAL'):
        return 'triggered'
    return 'unknown'
