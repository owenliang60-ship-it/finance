"""Monday 04:00 UTC rebalance (north star §4.2, plan §13 revision 4: serial per symbol).

Order of work: reconcile -> fix equity at one cut-off -> frozen policy targets ->
set 2x crossed on new symbols -> wave A (reduce, close) -> margin check -> wave B
(open, add). One request is in flight at a time. Each short is protected before
the next symbol is touched. Nothing is chased within the week.
"""
from decimal import Decimal
import json
import sqlite3

import pandas as pd

from . import config as C, notify, protection
from .binance import Endpoints as E, ExchangeError
from .kernel import LivePolicy, split_waves
from .reconcile import Desk, Halt, Unclear, current_anchor, pause
from .store import TERMINAL_STATES, LockBusy, process_lock

DAY_END_AFTER_ANCHOR_S = 20 * 3600   # the anchor is 04:00 UTC; its UTC day ends 20 hours later
SETTLED = frozenset({'ok', 'flat', 'closed', 'covered', 'exited'})   # reconciliation outcomes safe to trade on


def rebalance_job(rt, dry_run=False, late_approved=False, assume_equity=None):
    now = rt.clock()
    anchor = current_anchor(now)
    if dry_run:
        return _dry_run(rt, anchor, assume_equity)
    try:
        with process_lock(rt.state_dir, wait_s=C.LOCK_WAIT_S, sleep=rt.sleep):
            return _locked(rt, anchor, now, late_approved)
    except LockBusy:
        _alert(rt, '🚨 动量多空1号 调仓拿不到进程锁（等了 %d 秒），本次未运行，未发任何单。请检查是否有卡住的守护进程。'
               % C.LOCK_WAIT_S, key='locked:%s' % anchor)
        return dict(status='locked', anchor=str(anchor))


def _alert(rt, text, key=None):
    """Queue and send. If the database itself is failing, send directly."""
    try:
        notify.enqueue(rt, text, key=key)
        notify.flush(rt)
    except sqlite3.Error:
        rt.send(text)


def _locked(rt, anchor, now, late_approved):
    store, key = rt.store, str(anchor)
    week = store.week(key)
    # A week in which no order or setting was ever registered may be run again (failed preparation
    # fixed, a transient read failure, a pause that was resumed). Anything else is a duplicate.
    untouched = week is not None and week['status'] == 'no_new' and not store.actions(anchor_utc=key, lane='risk')
    if week is not None and week['status'] not in (None, 'prepared') and not untouched:
        return dict(status='duplicate', anchor=key, week_status=week['status'])
    elapsed = now - anchor.timestamp()
    late = elapsed >= C.ENTRY_WINDOW_S
    if late:
        reprepared = week is not None and (week['prep_finished_at'] or 0) >= anchor.timestamp() + C.ENTRY_WINDOW_S
        if not (late_approved and elapsed < DAY_END_AFTER_ANCHOR_S and reprepared):
            _alert(rt, '⚠️ 动量多空1号 调仓迟到：锚点 %s 已过 %d 秒，超出 5 分钟准入期。未发任何新增。'
                       '补跑需 Boss 当次批准，限当天 UTC 日内，并先重跑准备。' % (key, elapsed), key='late:' + key)
            return dict(status='late', anchor=key)
    try:
        store.save_week(key, status='running', run_started_at=now, release_commit=rt.release_commit)
        if late:
            store.add_deviation(key, 'late_start', seconds=int(elapsed))
        if week is None or week['prep_status'] != 'ready':
            store.add_deviation(key, 'prep_failed', error=None if week is None else week['prep_error'])
            store.save_week(key, status='no_new')
            _alert(rt, '⚠️ 动量多空1号 准备失败，本周不新增（%s）。已有持仓和止损不动，请手动介入。' % key,
                   key='no_prep:' + key)
            return dict(status='no_new', anchor=key, deviations=store.week(key)['deviations'])
        result = Run(rt, anchor, store.week(key)).execute()
    except sqlite3.Error as error:
        try:
            store.add_deviation(key, 'db_write_failed', error=str(error))
            store.save_week(key, status='deviated')
        except sqlite3.Error:
            pass
        _alert(rt, '🚨 动量多空1号 写库失败（%s），已停止发单。未入库的请求没有发出。请检查云端磁盘和 state.db。' % error)
        return dict(status='blocked', anchor=key, error=str(error))
    try:
        notify.flush(rt)
    except sqlite3.Error:
        pass
    return result


def compute_targets(desk, week, equity, held, prices, stops, now_ms, unsettled=()):
    """Frozen policy -> exchange-rounded target units. Pure: reads only, writes nothing."""
    rt, anchor = desk.rt, desk.anchor
    rows = [r for r in rt.store.evidence('snapshot')
            if r['dedupe_key'] == 'signals:%s:%s' % (anchor, week['input_hash'])]
    if len(rows) != 1:
        raise Unclear('frozen signals for this anchor are missing')
    frame = pd.DataFrame(rows[0]['payload'])
    rules = desk.rules
    candidates = set(frame['symbol'])
    unusable = sorted(s for s in candidates if s not in rules or s not in prices)
    blocked = set(unusable) | {s for s in candidates if s in rules and not rules[s].can_open(now_ms)}
    residual = sorted(s for s in {stop['symbol'] for stop in stops} if s not in held)
    blocked |= set(residual)   # an old stop still open on a flat symbol: no new episode there
    blocked |= set(unsettled)  # the opening reconciliation could not settle these symbols
    lifetimes = {s: dict(delivery=rules[s].delivery_ms) for s in candidates | set(held) if s in rules}
    cooldown = {k.split(':', 1)[1]: pd.Timestamp(rt.store.get(k)['at'], unit='s', tz='UTC')
                for k in rt.store.keys('cooldown:')}
    policy = LivePolicy(frame, week['regime'], anchor, cooldown=cooldown, lifetimes=lifetimes, blocked=blocked)
    raw = policy(anchor, float(equity), {s: float(q) for s, q in held.items()},
                 {s: float(p) for s, p in prices.items()})
    targets, skipped = {}, []
    for symbol, units in raw.items():
        size = rules[symbol].round_quantity(abs(Decimal(str(units)))) if units else Decimal(0)
        if size > 0:
            targets[symbol] = size if units > 0 else -size
        elif units:
            skipped.append(symbol)
    return dict(targets=targets, policy=policy, cooldown=cooldown, skipped=skipped, unusable=unusable,
                residual=[s for s in residual if s in candidates])


def _dry_run(rt, anchor, assume_equity):
    """Shadow run: full targets and waves, no request, no write to state.db."""
    week = rt.store.week(str(anchor))
    if week is None or week['prep_status'] != 'ready':
        return dict(status='no_prep', anchor=str(anchor))
    desk = Desk(rt, anchor)
    prices = {r['symbol']: Decimal(str(r['price'])) for r in desk.read(E.TICKER_PRICE, signed=False)}
    if assume_equity is not None:
        equity, held, stops, simulated = Decimal(str(assume_equity)), {}, [], True
    else:
        account = desk.read(E.ACCOUNT)
        equity = Decimal(str(account['totalWalletBalance'])) + Decimal(str(account['totalUnrealizedProfit']))
        held = {s: q for s, (q, _) in desk.positions().items() if q != 0}
        stops, simulated = desk.open_stops(), False
    computed = compute_targets(desk, week, equity, held, prices, stops, int(rt.clock() * 1000))
    reductions, increases = split_waves(held, computed['targets'])
    config = {row['symbol']: row for row in desk.read(E.SYMBOL_CONFIG)}
    plan = dict(anchor=str(anchor), simulated=simulated, equity=str(equity), regime=week['regime'],
                release_commit=rt.release_commit, input_hash=week['input_hash'],
                targets={s: str(q) for s, q in sorted(computed['targets'].items())},
                wave_a=[dict(symbol=i.symbol, side=i.side, qty=str(i.qty), purpose=i.purpose) for i in reductions],
                wave_b=[dict(symbol=i.symbol, side=i.side, qty=str(i.qty), purpose=i.purpose) for i in increases],
                prices={s: str(prices[s]) for s in sorted(computed['targets'])},
                symbol_config={s: config.get(s) for s in sorted(computed['targets'])},
                blocked_residual=computed['residual'], skipped_below_step=computed['skipped'],
                rules_unusable=computed['unusable'])
    relative = 'evidence/plan-%s.json' % anchor.strftime('%y%m%d%H')
    path = rt.state_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    return dict(status='dry_run', anchor=str(anchor), targets=plan['targets'], plan_file=relative)


class Run:
    def __init__(self, rt, anchor, week):
        self.rt, self.store, self.anchor, self.key, self.week = rt, rt.store, anchor, str(anchor), week
        self.desk = Desk(rt, anchor)
        self.acts = self.desk.acts
        self.deadline = None
        self.halted = False    # no further new exposure in this run
        self.last_beat = 0.0

    # ---- small helpers -------------------------------------------------
    def deviation(self, code, symbol=None, **detail):
        self.store.add_deviation(self.key, code, symbol=symbol, **detail)

    def beat(self):
        """The rebalance holds the lock, so it writes the guard's heartbeat itself."""
        now = self.rt.clock()
        if now - self.last_beat >= 10:
            self.store.set('guard_heartbeat', dict(at=now, status='rebalance'))
            self.last_beat = now

    def finish(self, status=None):
        week = self.store.week(self.key)
        material = [d for d in week['deviations'] if d['code'] not in C.MINOR_DEVIATIONS]
        status = status or ('deviated' if material else 'done')
        self.store.save_week(self.key, status=status, report_status='queued')
        shorts = sum(1 for q in week['targets'].values() if Decimal(q) < 0)
        longs = sum(1 for q in week['targets'].values() if Decimal(q) > 0)
        notify.enqueue(self.rt, '📊 动量多空1号 周调仓 %s\n状态：%s。权益 %s USDT。目标：空 %d 只，多 %d 只。\n偏差 %d 项%s'
                       % (self.key, status, week['equity_usdt'], shorts, longs, len(material),
                          '：' + '、'.join(sorted({d['code'] for d in material})) if material else '。'),
                       key='weekly:%s:%s:%d' % (self.key, status, self.rt.clock()))
        return dict(status=status, anchor=self.key, deviations=week['deviations'])

    # ---- the nine steps ------------------------------------------------
    def execute(self):
        desk, store = self.desk, self.store
        self.beat()
        try:
            desk.run(full=True)   # stops that already fired are settled before anything else
        except (Unclear, ExchangeError) as error:
            self.deviation('reconcile_failed', error=str(error))
            return self.finish('no_new')
        paused = store.get('pause')
        ban = store.get('ip_ban')
        if paused is not None or (ban and ban['until'] > self.rt.clock()):
            self.deviation('paused', reason=paused['reason'] if paused else 'ip_ban')
            return self.finish('no_new')

        cutoff_ms = int(self.rt.clock() * 1000)
        account = desk.read(E.ACCOUNT)
        wallet = Decimal(str(account['totalWalletBalance']))
        unrealized = Decimal(str(account['totalUnrealizedProfit']))
        equity = wallet + unrealized
        held = {s: q for s, (q, _) in desk.positions(expect=desk.episode_symbols()).items() if q != 0}
        self.prices = {r['symbol']: Decimal(str(r['price'])) for r in desk.read(E.TICKER_PRICE, signed=False)}
        store.save_week(self.key, cutoff_ms=cutoff_ms, wallet_usdt=str(wallet), upnl_usdt=str(unrealized),
                        equity_usdt=str(equity))
        if equity <= 0:
            self.deviation('no_equity')
            return self.finish('no_new')

        unsettled = {s for s, result in desk.outcome.items() if result not in SETTLED}
        computed = compute_targets(desk, self.week, equity, held, self.prices, desk.open_stops(), cutoff_ms,
                                   unsettled=unsettled)
        targets = computed['targets']
        for symbol in computed['residual']:
            self.deviation('residual_protection', symbol=symbol)
        for symbol in computed['skipped']:
            self.deviation('below_min', symbol=symbol)
        for symbol in computed['unusable']:   # our parser or the price feed, not the exchange's eligibility
            self.deviation('rules_unusable', symbol=symbol)
        with store.tx():   # cooldowns the policy just created (regime exits) and the ones that ran out
            for symbol, at in computed['policy'].cooldown.items():
                if at == self.anchor and symbol not in computed['cooldown']:
                    store.set('cooldown:' + symbol, dict(at=self.anchor.timestamp(), reason='regime'))
            for symbol, at in computed['cooldown'].items():
                if at < self.anchor:
                    store.delete('cooldown:' + symbol)
            store.save_week(self.key, targets={s: str(q) for s, q in sorted(targets.items())})

        reductions, increases = split_waves(held, targets)
        for intent in reductions + increases:
            if intent.symbol in unsettled:   # do not trade on a position we could not reconcile
                self.deviation('symbol_unsettled', symbol=intent.symbol, outcome=desk.outcome[intent.symbol])
        reductions = [i for i in reductions if i.symbol not in unsettled]
        increases = [i for i in increases if i.symbol not in unsettled]
        reductions = [i for i in self.ordered(reductions, held) if self.big_enough(i, opening=False)]
        increases = [i for i in self.ordered(increases, targets) if self.big_enough(i, opening=True)]
        increases = [i for i in increases if i.purpose != 'wave_b_open' or self.configure(i.symbol)]

        self.wave(reductions, held, reduce_only=True)
        if not self.halted:
            increases = self.fit_margin(increases)
            self.wave(increases, held, reduce_only=False)
        return self.finish()

    def ordered(self, intents, book):
        """Shorts first, then longs; inside each side the frozen rank order."""
        rank = {s: i for i, s in enumerate(self.week['short_ranked'] + self.week['long_ranked'])}
        return sorted(intents, key=lambda i: (0 if book.get(i.symbol, 0) < 0 else 1, rank.get(i.symbol, 10 ** 6), i.symbol))

    def big_enough(self, intent, opening):
        rules = self.desk.rules.get(intent.symbol)
        if rules is None:
            if intent.purpose == 'wave_a_close':
                return True   # closing the whole position needs no rounding
            self.deviation('rules_unusable', symbol=intent.symbol, qty=str(intent.qty))
            return False
        too_small = intent.qty < max(rules.lot.minimum, rules.market_lot.minimum)
        if not too_small and opening:   # reduce-only orders are exempt from minimum notional
            too_small = intent.qty * self.prices[intent.symbol] < rules.min_notional
        if too_small:
            self.deviation('below_min', symbol=intent.symbol, qty=str(intent.qty))
        return not too_small

    def configure(self, symbol):
        """New symbol: set 2x and crossed, then read back. The system never sets any other leverage."""
        desk = self.desk
        row = next((r for r in desk.read(E.SYMBOL_CONFIG, dict(symbol=symbol)) if r['symbol'] == symbol), None)
        wanted = []
        if row is None or row['marginType'] != C.MARGIN_TYPE:
            wanted.append('margin_type')
        if row is None or int(row['leverage']) != C.LEVERAGE:
            wanted.append('leverage')
        for kind in wanted:
            action_id = self.acts.plan_setting(self.anchor, symbol, kind)
            sent = self.acts.send(action_id)
            state = self.acts.resolve(action_id)['state'] if sent.state == 'UNKNOWN' else sent.state
            if state != 'FILLED':
                self.deviation('leverage_failed', symbol=symbol, setting=kind, exchange_code=sent.action['error_code'])
                return False   # empty slot, no substitute: this is usually an account or API problem
        row = next((r for r in desk.read(E.SYMBOL_CONFIG, dict(symbol=symbol)) if r['symbol'] == symbol), None)
        if row is None or int(row['leverage']) != C.LEVERAGE or row['marginType'] != C.MARGIN_TYPE:
            self.deviation('config_mismatch', symbol=symbol, leverage=None if row is None else row['leverage'],
                           margin=None if row is None else row['marginType'])
            return False
        return True

    def fit_margin(self, increases):
        """D6: if wave B needs more margin than is free, shrink every order by one factor."""
        if not increases:
            return increases
        available = Decimal(str(self.desk.read(E.ACCOUNT)['availableBalance']))
        required = sum(i.qty * self.prices[i.symbol] for i in increases) / C.LEVERAGE * C.MARGIN_BUFFER
        if required <= available:
            return increases
        factor = available / required
        self.deviation('wave_b_scaled', factor=str(factor), available=str(available), required=str(required))
        self.store.save_week(self.key, wave_b_scale=str(factor))
        scaled = []
        for intent in increases:
            qty = intent.qty * factor
            qty = self.desk.rules[intent.symbol].round_quantity(qty) if qty > 0 else Decimal(0)
            shrunk = type(intent)(intent.symbol, intent.side, qty, intent.purpose)
            if qty > 0 and self.big_enough(shrunk, opening=True):
                scaled.append(shrunk)
        return scaled

    def wave(self, intents, held, reduce_only):
        """Register the whole wave, then send one request at a time."""
        with self.store.tx():
            ids = [self.acts.plan_order(self.anchor, i.symbol, i.side, i.qty, i.purpose, reduce_only,
                                        position_before=held.get(i.symbol, Decimal(0))) for i in intents]
        for position, action_id in enumerate(ids):
            if not self.halted and self.store.get('pause') is not None:
                self.halted = True   # paused while this run was working: send nothing more
            if self.halted:
                for rest in ids[position:]:
                    action = self.store.action(rest)
                    self.store.update_action(rest, state='ABANDONED', error_msg='run stopped adding')
                    self.deviation('not_sent', symbol=action['symbol'], client_id=action['client_id'])
                return
            if not reduce_only and not self.still_valid(self.store.action(action_id)):
                self.store.update_action(action_id, state='ABANDONED', error_msg='voided before sending')
                self.deviation('add_voided', symbol=self.store.action(action_id)['symbol'])
                continue
            self.one(action_id, reduce_only)

    def still_valid(self, action):
        """Re-check right before sending: a stop may have fired since the targets were computed."""
        symbol = action['symbol']
        try:
            if action['purpose'] == 'wave_b_open':
                return self.desk.episode(symbol) is None and self.desk.position(symbol)[0] == 0
            qty, entry = self.desk.position(symbol)
            return self.desk.check_symbol(symbol, qty, entry, self.desk.open_stops(symbol)) in ('ok', 'covered')
        except Halt as halt:
            pause(self.rt, halt.reason, halt.detail)
        except (Unclear, ExchangeError):
            pass
        return False

    def one(self, action_id, reduce_only):
        self.beat()
        if self.deadline is None:
            self.deadline = self.rt.clock() + C.CONVERGE_S   # one deadline for the whole run
        sent = self.acts.send(action_id)
        wait = getattr(sent.error, 'retry_after', None)
        if (sent.state == 'NOT_ACCEPTED' and sent.action['client_id'].endswith('-1') and wait is not None
                and wait <= 60 and self.rt.clock() + wait + 1 < self.deadline):
            # Our own request-weight gate, never sent: wait for the next minute and try once more.
            self.rt.sleep(wait + 1)
            return self.one(self.acts.reissue(sent.action, self.anchor), reduce_only)
        action = sent.action
        symbol = action['symbol']
        if sent.state in ('ACKED', 'UNKNOWN'):
            action = self.await_final(action_id)
        if action['state'] == 'NOT_ACCEPTED' and action['error_msg'] is None and action['client_id'].endswith('-1'):
            # Confirmed never accepted: one new attempt with a new id, linked to the first.
            return self.one(self.acts.reissue(action, self.anchor), reduce_only)
        state = action['state']
        if state not in TERMINAL_STATES:
            pause(self.rt, 'unknown_order_overdue', [action['client_id']])
            self.deviation('unknown_overdue', symbol=symbol, client_id=action['client_id'])
            self.halted = True
            return None
        if state in ('REJECTED', 'NOT_ACCEPTED'):
            self.refused(action, sent, reduce_only)
        elif state in ('CANCELED', 'EXPIRED') or (state == 'FILLED' and not Decimal(action['executed_qty'] or '0')):
            self.deviation('order_no_fill', symbol=symbol, order_state=state)
        elif state == 'PARTIAL_FINAL':
            self.deviation('wave_a_partial' if reduce_only else 'wave_b_partial', symbol=symbol,
                           executed=action['executed_qty'], wanted=action['qty'])
        executed = Decimal(action['executed_qty'] or '0')
        if executed > 0:
            self.after_fill(symbol, Decimal(0) if reduce_only else executed)
        return None

    def await_final(self, action_id):
        while True:
            action = self.desk.settle_action(action_id)
            if action['state'] in TERMINAL_STATES or self.rt.clock() >= self.deadline:
                return action
            self.beat()
            self.rt.sleep(C.POLL_S)

    def refused(self, action, sent, reduce_only):
        symbol = action['symbol']
        status = getattr(sent.error, 'http_status', None)
        if status == 429 or action['state'] == 'NOT_ACCEPTED':
            # Back off: nothing more is added in this run.
            self.deviation('rate_limited' if status == 429 else 'blocked_before_sending', symbol=symbol)
            self.halted = True
        elif status == 418:
            wait = getattr(sent.error, 'retry_after', None)
            until = self.rt.clock() + (wait if wait and wait != float('inf') else 86400)
            self.store.set('ip_ban', dict(until=until, at=self.rt.clock()))
            self.deviation('ip_banned', symbol=symbol, until=until)
            notify.enqueue(self.rt, '🚨 动量多空1号 收到 418（IP 被封），已停新增。预计解封时间戳 %d。原生止损仍在交易所。' % until)
            self.halted = True
        else:
            self.deviation('wave_a_rejected' if reduce_only else 'open_rejected', symbol=symbol,
                           exchange_code=action['error_code'])

    def after_fill(self, symbol, added):
        """Read the real quantity and entry price, then protect before touching the next symbol."""
        desk = self.desk
        qty, entry = desk.position(symbol)
        if qty == 0:
            desk.finish_if_flat(symbol)
            return
        if qty > 0:
            return   # longs carry no stop
        stops = [s for s in desk.open_stops(symbol) if s['clientAlgoId'].startswith(C.ID_PREFIX)]
        outcome = protection.ensure(desk, symbol, qty, entry, stops)
        if outcome in ('ok', 'exited', 'triggered', 'covered'):
            return
        self.halted = True   # north star §5.5 step 1: stop adding
        if outcome != 'rejected':
            self.deviation('protection_' + outcome, symbol=symbol)   # the guard retries next minute
            return
        rejection = desk.rejections.pop()
        if rejection['code'] in C.COMMON_CAUSE_CODES:
            pause(self.rt, 'protection_common_cause', [rejection])   # account-level: no per-symbol exit
            self.deviation('protection_common_cause', symbol=symbol, exchange_code=rejection['code'])
            return
        self.deviation('protection_rejected', symbol=symbol, exchange_code=rejection['code'])
        if added <= 0:
            return   # a reduction: the old, larger stop still covers the position
        if added >= -qty:
            desk.exit(symbol, 'fault_exit', reason='fault')   # a new position has no old stop: close it
            return
        action_id = self.acts.plan_order(self.anchor, symbol, 'BUY', added, 'fault_reduce', True, position_before=qty)
        if self.acts.send(action_id).state in ('ACKED', 'UNKNOWN'):
            self.await_final(action_id)   # back to the quantity the old stop fully covers
