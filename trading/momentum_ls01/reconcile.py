"""Reconciliation shared by the guard and the rebalance (north star §6).

The exchange is the truth for positions and entry price. For every symbol the
desk brings three things into agreement: the exchange position, the local
episode, and exactly one working stop per short. Anything it cannot explain
stops new exposure; it never guesses.
"""
from decimal import Decimal

import pandas as pd

from . import config as C, notify, protection
from .actions import Actions, fresh_clock
from .binance import Endpoints as E, ExchangeError
from .exchange_rules import RuleViolation, SymbolRules
from .kernel import cost_after_trade
from .protection import Unclear
from .store import TERMINAL_STATES, process_lock

REASONS = {
    'external_flow': '未核验的外部资金流', 'manual_takeover': '检测到手动操作',
    'entry_price_mismatch': '交易所成本价与本地成本不一致', 'protection_common_cause': '保护被拒，疑似共同根因',
    'unknown_order_overdue': '未知订单超期', 'account_mode': '账户模式或杠杆不符',
    'account_unclear': '账实不清', 'guard_failures': '守护连续失败', 'manual_pause': '手动暂停'}


class Halt(Exception):
    """A finding that needs the Boss. Becomes a persistent pause."""
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def current_anchor(now):
    """Latest Monday 04:00 UTC at or before ``now`` (epoch seconds)."""
    moment = pd.Timestamp(now, unit='s', tz='UTC')
    monday = (moment - pd.Timedelta(hours=4)).normalize()
    monday -= pd.Timedelta(days=monday.weekday())
    return monday + pd.Timedelta(hours=4)


def pause(rt, reason, detail=None):
    """Persistent pause. Only ``resume`` clears it. The first reason is kept."""
    current = rt.store.get('pause')
    if current is None:
        current = dict(reason=reason, detail=detail, at=rt.clock(), also=[])
        rt.store.set('pause', current)
        notify.enqueue(rt, '⚠️ 动量多空1号 持久暂停：%s。原因代码 %s。%s\n不再新增；原生止损保留。处理后用 resume 恢复。'
                       % (REASONS.get(reason, reason), reason, '' if detail is None else '详情：%s' % (detail,)),
                       key='pause:%s:%s' % (reason, current['at']))
    elif reason != current['reason'] and reason not in current['also']:
        current['also'].append(reason)
        rt.store.set('pause', current)
    return current


class Desk:
    def __init__(self, rt, anchor=None):
        self.rt, self.store, self.acts = rt, rt.store, Actions(rt)
        self.anchor = anchor if anchor is not None else current_anchor(rt.clock())
        self.rejections = []   # protection refusals seen in this run
        self.outcome = {}      # per-symbol result of the last run()
        self._rules = None

    # ---- exchange reads ------------------------------------------------
    def read(self, path, params=None, signed=True):
        if signed:
            fresh_clock(self.rt.client)
        return self.rt.client.request('GET', path, params, signed=signed, protective=True)

    @property
    def rules(self):
        if self._rules is None:
            rules = {}
            for row in self.read(E.EXCHANGE_INFO, signed=False)['symbols']:
                try:
                    rules[row['symbol']] = SymbolRules.from_exchange_info(row)
                except RuleViolation:
                    continue   # unusable filters: the symbol is simply not tradable for us
            self._rules = rules
        return self._rules

    def price(self, symbol):
        return Decimal(str(self.read(E.TICKER_PRICE, dict(symbol=symbol), signed=False)['price']))

    def positions(self, expect=()):
        rows = self.read(E.POSITION_RISK_V2)
        found = {r['symbol']: (Decimal(str(r['positionAmt'])), Decimal(str(r['entryPrice']))) for r in rows}
        missing = [s for s in expect if s not in found]
        if not found or missing:
            raise Unclear('position rows missing: %s' % (missing or 'all'))   # absence is never proof of zero
        return found

    def position(self, symbol):
        rows = [r for r in self.read(E.POSITION_RISK_V2, dict(symbol=symbol)) if r.get('symbol') == symbol]
        if len(rows) != 1:
            raise Unclear('position row missing: ' + symbol)
        return Decimal(str(rows[0]['positionAmt'])), Decimal(str(rows[0]['entryPrice']))

    def open_stops(self, symbol=None):
        return self.read(E.OPEN_ALGO_ORDERS, dict(symbol=symbol) if symbol else None)

    # ---- episodes ------------------------------------------------------
    def episode(self, symbol):
        return self.store.get('episode:' + symbol)

    def episode_symbols(self):
        return [key.split(':', 1)[1] for key in self.store.keys('episode:')]

    def apply_fill(self, symbol, delta, price):
        """Fold one of our own or an exchange-forced fill into the local quantity and cost."""
        episode = self.episode(symbol)
        before = Decimal(episode['qty']) if episode else Decimal(0)
        basis = cost_after_trade(before, episode['basis'] if episode else None, delta, price)
        if episode is None:
            episode = dict(opened_at=self.rt.clock(), anchor=str(self.anchor),
                           trades_since_ms=int(self.rt.clock() * 1000) - 5000)
        episode.update(qty=str(before + Decimal(delta)), basis=None if basis is None else str(basis))
        self.store.set('episode:' + symbol, episode)

    def settle_action(self, action_id):
        """Query once. A fill that just became final reaches the episode in the same transaction."""
        with self.store.tx():
            before = self.store.action(action_id)
            after = self.acts.resolve(action_id)
            newly_final = before['state'] not in TERMINAL_STATES and after['state'] in ('FILLED', 'PARTIAL_FINAL')
            if newly_final and after['kind'] in ('order', 'algo'):
                executed = Decimal(after['executed_qty'] or '0')
                if executed > 0:
                    self.apply_fill(after['symbol'], executed if after['side'] == 'BUY' else -executed,
                                    Decimal(after['avg_price']))
                if after['kind'] == 'algo':   # a triggered stop: the exit target is zero from now on
                    # Cooldown runs from the exchange's fill time, not from when we noticed.
                    self.mark_exiting(after['symbol'], 'stop', at=self.acts.fill_time.get(action_id))
            return after

    def mark_exiting(self, symbol, reason, at=None):
        episode = self.episode(symbol)
        if episode is not None and not episode.get('exiting'):
            episode['exiting'] = dict(reason=reason, at=at if at is not None else self.rt.clock())
            self.store.set('episode:' + symbol, episode)

    def close_episode(self, symbol, episode, stops):
        exiting = episode.get('exiting') or {}
        reason = exiting.get('reason') or episode.get('forced_close') or 'rebalance'
        at = exiting.get('at') or self.rt.clock()
        with self.store.tx():
            self.store.add_evidence('exit', 'exit:%s:%s' % (symbol, episode['opened_at']),
                                    dict(symbol=symbol, reason=reason, at=at), symbol=symbol, time_ms=int(at * 1000))
            if reason not in ('delist', 'rebalance'):
                self.store.set('cooldown:' + symbol, dict(at=at, reason=reason))
            self.store.delete('episode:' + symbol)
            self.store.delete('protection:' + symbol)
            self.store.delete('protect_hold:' + symbol)
        self.cancel_stops(symbol, stops)

    # ---- actions on one symbol -----------------------------------------
    def cancel_stops(self, symbol, stops):
        """Cancel our stops and confirm. Returns False while any cancel is unconfirmed."""
        confirmed = True
        for stop in stops:
            action_id = self.acts.plan_cancel_stop(self.anchor, symbol, stop['clientAlgoId'])
            sent = self.acts.send(action_id)
            if sent.state == 'UNKNOWN':
                sent = type(sent)(self.acts.resolve(action_id))
            confirmed = confirmed and sent.state == 'FILLED'
        return confirmed

    def exit(self, symbol, purpose, reason=None):
        """reduceOnly MARKET for the whole exchange position. Returns the order's state."""
        if reason:
            self.mark_exiting(symbol, reason)
        qty, _ = self.position(symbol)
        if qty == 0:
            return self.finish_if_flat(symbol)
        action_id = self.acts.plan_order(self.anchor, symbol, 'BUY' if qty < 0 else 'SELL', abs(qty), purpose,
                                         reduce_only=True, position_before=qty)
        sent = self.acts.send(action_id)
        state = self.settle_action(action_id)['state'] if sent.state in ('ACKED', 'UNKNOWN') else sent.state
        self.finish_if_flat(symbol)
        return state

    def finish_if_flat(self, symbol):
        episode = self.episode(symbol)
        if episode is not None and Decimal(episode['qty']) == 0 and self.position(symbol)[0] == 0:
            self.close_episode(symbol, episode, [s for s in self.open_stops(symbol)
                                                 if s['clientAlgoId'].startswith(C.ID_PREFIX)])
            return 'closed'
        return 'open'

    def crossing_exit(self, symbol):
        """Price is already past the stop: the stop condition is met, so exit as a normal stop."""
        self.exit(symbol, 'crossing_exit', reason='stop')
        return 'exited'

    def explain_difference(self, symbol, episode):
        """Fills we did not send: forced by the exchange, or manual. Manual means hands off."""
        known = set()
        for action in self.store.actions(symbol=symbol):
            known.update(str(x) for x in (action['exchange_id'], action['child_order_id']) if x)
        since = max(episode['trades_since_ms'], int((self.rt.clock() - C.TRADE_LOOKBACK_S) * 1000))
        trades = self.read(E.USER_TRADES, dict(symbol=symbol, startTime=since, limit=1000))
        foreign = [t for t in trades if str(t['orderId']) not in known]
        labels = {}
        for order_id in sorted({t['orderId'] for t in foreign}):
            client_id = str(self.read(E.ORDER, dict(symbol=symbol, orderId=order_id)).get('clientOrderId', ''))
            label = next((name for prefix, name in C.FORCED_PREFIXES if client_id.startswith(prefix)), None)
            if label is None:
                raise Halt('manual_takeover', dict(symbol=symbol, order_id=order_id))
            labels[order_id] = label
        with self.store.tx():
            for trade in foreign:
                self.store.add_evidence('trade', 'trade:%s:%s' % (symbol, trade['id']), trade, symbol=symbol,
                                        time_ms=trade['time'])
                size = Decimal(str(trade['qty']))
                self.apply_fill(symbol, size if trade['side'] == 'BUY' else -size, Decimal(str(trade['price'])))
            episode = self.episode(symbol)
            if foreign:
                episode['trades_since_ms'] = max(t['time'] for t in foreign) + 1
                if Decimal(episode['qty']) == 0:
                    episode['forced_close'] = labels[foreign[-1]['orderId']]
                self.store.set('episode:' + symbol, episode)

    def check_symbol(self, symbol, qty, entry, stops):
        """Bring one symbol into agreement. Raises Halt or Unclear when it cannot."""
        store = self.store
        pending = False
        for action in (store.actions(symbol=symbol, state=('UNKNOWN', 'SENDING'))
                       + store.actions(symbol=symbol, kind='order', state='ACKED')):
            after = self.settle_action(action['id'])
            pending = pending or after['state'] not in TERMINAL_STATES and not (
                after['kind'] == 'algo' and after['state'] == 'ACKED')
        if pending:
            return 'pending'   # touch nothing until this symbol's own requests are known
        if any(not s['clientAlgoId'].startswith(C.ID_PREFIX) for s in stops):
            raise Halt('manual_takeover', dict(symbol=symbol, what='foreign conditional order'))
        working = {s['clientAlgoId'] for s in stops}
        for action in store.actions(symbol=symbol, kind='algo', state='ACKED'):
            if action['client_id'] in working:
                continue
            after = self.settle_action(action['id'])   # no longer working: triggered, cancelled or expired
            if after['state'] == 'ACKED':
                if after['child_order_id']:
                    return 'hold'   # triggered, child order not final yet: send nothing else
                raise Unclear('stop is neither working nor final: ' + symbol)
        episode = self.episode(symbol)
        if episode is None:
            if qty != 0:
                raise Halt('manual_takeover', dict(symbol=symbol, what='position without episode'))
            return 'flat' if self.cancel_stops(symbol, stops) else 'residual'
        if qty != Decimal(episode['qty']):
            self.explain_difference(symbol, episode)
            episode = self.episode(symbol)
            if qty != Decimal(episode['qty']):
                raise Unclear('unexplained quantity on ' + symbol)
        if qty == 0:
            self.close_episode(symbol, episode, stops)
            return 'closed'
        basis = Decimal(episode['basis'])
        if entry <= 0:
            raise Unclear('entry price missing: ' + symbol)   # never a proof of zero
        if abs(entry - basis) > basis * C.BASIS_TOLERANCE:
            raise Halt('entry_price_mismatch', dict(symbol=symbol, exchange=str(entry), local=str(basis)))
        if episode.get('exiting'):
            return self.exit(symbol, 'stop_residual_exit')   # allowed even while paused
        if qty < 0:
            return protection.ensure(self, symbol, qty, entry, stops)
        self.cancel_stops(symbol, stops)   # longs carry no stop
        return 'ok'

    # ---- whole account -------------------------------------------------
    def full_checks(self):
        """Hourly and before every rebalance: account modes and money that is not trading result."""
        if self.read(E.POSITION_MODE).get('dualSidePosition') is not False:
            raise Halt('account_mode', 'position mode is not one-way')
        if self.read(E.MULTI_ASSETS_MODE).get('multiAssetsMargin') is not False:
            raise Halt('account_mode', 'multi-assets mode is on')
        held = set(self.episode_symbols())
        for row in self.read(E.SYMBOL_CONFIG):
            if row['symbol'] in held and (int(row['leverage']) != C.LEVERAGE or row['marginType'] != C.MARGIN_TYPE):
                raise Halt('account_mode', dict(symbol=row['symbol'], leverage=row['leverage'],
                                                margin=row['marginType']))
        self.ingest_income()

    def ingest_income(self):
        store = self.store
        cursor = store.get('cursor:income', 0)
        authorized, pending = store.get('authorized_flows', []), store.get('pending_flows', [])
        while True:
            rows = self.read(E.INCOME, dict(startTime=cursor, limit=1000))
            for row in rows:
                key = 'income:%s:%s:%s:%s:%s' % (row['incomeType'], row.get('symbol', ''), row.get('tranId'),
                                                 row.get('tradeId', ''), row['time'])
                store.add_evidence('income', key, row, symbol=row.get('symbol') or None, time_ms=row['time'])
                if (row['incomeType'] not in C.TRADING_INCOME and row.get('tranId') not in authorized
                        and row.get('tranId') not in pending):
                    pending.append(row.get('tranId'))
            if rows:
                cursor = max(cursor, max(r['time'] for r in rows))
            if len(rows) < 1000:
                break
            cursor += 1
        store.set('cursor:income', cursor)
        store.set('pending_flows', pending)
        if pending:
            raise Halt('external_flow', dict(tran_ids=pending))

    def overdue_unknowns(self):
        now = self.rt.clock()
        rows = (self.store.actions(kind='order', state=('UNKNOWN', 'SENDING', 'ACKED'))
                + self.store.actions(kind=('algo', 'cancel_algo'), state=('UNKNOWN', 'SENDING')))
        return [a for a in rows if now - (a['sent_at'] or now) > C.CONVERGE_S]

    def run(self, full=False):
        """One guard pass. Protection, stop exits and crossing exits run even while paused."""
        self.acts.recover()
        if full:
            try:
                self.full_checks()
            except Halt as halt:
                pause(self.rt, halt.reason, halt.detail)
        episodes = self.episode_symbols()
        positions = self.positions(expect=episodes)
        stops = self.open_stops()
        if any(not str(o.get('clientOrderId', '')).startswith(C.ID_PREFIX) for o in self.read(E.OPEN_ORDERS)):
            pause(self.rt, 'manual_takeover', 'foreign open order')
        account = self.read(E.ACCOUNT)
        symbols = set(episodes) | {s for s, (q, _) in positions.items() if q != 0} | {s['symbol'] for s in stops}
        symbols |= {a['symbol'] for a in self.store.actions(state=('UNKNOWN', 'SENDING')) if a['symbol']}
        outcome, failed = {}, []
        for symbol in sorted(symbols):
            try:   # one symbol that cannot be explained must not starve the others of protection
                if symbol not in positions:
                    raise Unclear('position row missing: ' + symbol)
                qty, entry = positions[symbol]
                outcome[symbol] = self.check_symbol(symbol, qty, entry, [s for s in stops if s['symbol'] == symbol])
            except Halt as halt:
                pause(self.rt, halt.reason, halt.detail)
                outcome[symbol] = 'halt:' + halt.reason
            except (Unclear, ExchangeError) as error:
                outcome[symbol] = 'failed'
                failed.append('%s: %s' % (symbol, error))
        self.outcome = outcome
        self.judge_rejections()
        if self.overdue_unknowns():
            pause(self.rt, 'unknown_order_overdue', [a['client_id'] for a in self.overdue_unknowns()])
        if failed:
            raise Unclear('; '.join(failed))   # every other symbol was still checked
        return dict(symbols=outcome, margin_balance=account.get('totalMarginBalance'),
                    maintenance=account.get('totalMaintMargin'))

    def judge_rejections(self):
        """D1: one symbol is handled automatically; several, or an account-level cause, wait for the Boss."""
        rejected, self.rejections = self.rejections, []
        if not rejected:
            return None
        common = len(rejected) >= 2 or any(r['code'] in C.COMMON_CAUSE_CODES for r in rejected)
        if common:
            pause(self.rt, 'protection_common_cause', rejected)
            return 'common'
        symbol = rejected[0]['symbol']
        if rejected[0].get('covered'):
            return 'covered'   # an old stop still covers the short: keep the position (north star §5.5)
        if self.store.get('pause') is not None:
            notify.enqueue(self.rt, '⚠️ %s 保护被拒（代码 %s）。系统处于暂停，未自动退出，请处理。'
                           % (symbol, rejected[0]['code']))
            return 'paused'
        self.exit(symbol, 'fault_exit', reason='fault')
        notify.enqueue(self.rt, '⚠️ %s 无法挂上止损（代码 %s），已按单币故障自动市价退出。' % (symbol, rejected[0]['code']))
        return 'exited'

    def adopt_exchange_state(self):
        """After the Boss resumes: the exchange position becomes the local episode, as is."""
        positions = self.positions()
        now_ms = int(self.rt.clock() * 1000)
        for symbol in sorted(set(self.episode_symbols()) | {s for s, (q, _) in positions.items() if q != 0}):
            qty, entry = positions[symbol]
            episode = self.episode(symbol) or dict(opened_at=self.rt.clock(), anchor=str(self.anchor))
            if qty == 0:
                episode.update(qty='0', basis=None, exiting=dict(reason='manual', at=self.rt.clock()))
            else:
                episode.update(qty=str(qty), basis=str(entry))
            episode['trades_since_ms'] = now_ms
            self.store.set('episode:' + symbol, episode)


def resume(rt, accept_flow=None):
    """Clear a persistent pause after a full reconciliation. Optionally accept one external flow."""
    with process_lock(rt.state_dir):
        store = rt.store
        desk = Desk(rt)
        if accept_flow is not None:
            pending, authorized = store.get('pending_flows', []), store.get('authorized_flows', [])
            if accept_flow not in pending and accept_flow not in authorized:
                return dict(status='blocked', reason='flow %s is not waiting for acceptance' % (accept_flow,))
            store.set('pending_flows', [x for x in pending if x != accept_flow])
            if accept_flow not in authorized:
                store.set('authorized_flows', authorized + [accept_flow])
        if store.get('pending_flows', []):
            return dict(status='blocked', reason='unverified external flow', tran_ids=store.get('pending_flows'))
        desk.acts.recover()
        for action in store.actions(state=('UNKNOWN', 'SENDING')) + store.actions(kind='order', state='ACKED'):
            if desk.settle_action(action['id'])['state'] not in TERMINAL_STATES:
                return dict(status='blocked', reason='request outcome still unknown', client_id=action['client_id'])
        previous = store.get('pause')
        desk.adopt_exchange_state()
        store.delete('pause')
        try:
            desk.run(full=True)
        except BaseException as error:   # the pause is cleared only by a reconciliation that finished
            if store.get('pause') is None:
                store.set('pause', previous or dict(reason='account_unclear', detail=str(error), at=rt.clock(), also=[]))
            if not isinstance(error, (Unclear, ExchangeError)):
                raise
            return dict(status='blocked', reason='reconciliation did not finish', error=str(error))
        notify.flush(rt)
        current = store.get('pause')
        if current is not None:
            return dict(status='blocked', reason=current['reason'], detail=current.get('detail'))
        return dict(status='resumed', cleared=None if previous is None else previous['reason'])
