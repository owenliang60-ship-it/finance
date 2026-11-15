"""Minute guard, heartbeat watchdog and daily alive message (north star §6).

The guard never opens or adds. It recovers pending requests, keeps one working
stop on every short, finishes stop exits, and pauses on what it cannot explain.
The watchdog and the alive message are separate processes that take no lock, so
a dead guard cannot silence its own alarm.
"""
from pathlib import Path
import sqlite3

from . import config as C, notify
from .binance import ExchangeError
from .reconcile import Desk, Unclear, pause
from .store import LockBusy, Store, process_lock


def guard_job(rt, full=None):
    try:
        with process_lock(rt.state_dir):
            return _guard(rt, full)
    except LockBusy:
        return dict(status='skipped_locked')   # the rebalance is doing the guard's work


def _guard(rt, full):
    store = rt.store
    if full is None:
        full = int(rt.clock() // 60) % 60 == 0   # full reconciliation on the hour
    try:
        summary = Desk(rt).run(full=full)
        store.set('fail_streak', 0)
        result = dict(status='ok', full=full, **summary)
    except (Unclear, ExchangeError) as error:
        streak = store.get('fail_streak', 0) + 1
        store.set('fail_streak', streak)
        if streak >= C.GUARD_FAIL_LIMIT:
            pause(rt, 'guard_failures', str(error))
        result = dict(status='failed', full=full, error=str(error), fail_streak=streak)
    store.set('guard_heartbeat', dict(at=rt.clock(), status=result['status']))
    notify.flush(rt)
    return result


def _heartbeat_age(state_dir, now):
    try:
        beat = Store(state_dir, clock=lambda: now, readonly=True).get('guard_heartbeat')
    except sqlite3.Error:
        return None
    return None if beat is None else now - beat['at']


def watchdog(state_dir, clock, send):
    """Every minute, no lock, read-only. Alerts when the heartbeat is older than three minutes."""
    now = clock()
    age = _heartbeat_age(state_dir, now)
    if age is not None and age <= C.HEARTBEAT_STALE_S:
        return dict(alert=False, age=age)
    marker = Path(state_dir) / 'watchdog.last'
    try:
        if now - float(marker.read_text()) < C.WATCHDOG_REPEAT_S:
            return dict(alert=False, age=age, stale=True)   # already alerted recently
    except (OSError, ValueError):
        pass
    send('🚨 动量多空1号 守护心跳%s。分钟守护可能已停。交易所原生止损仍在；请登录云端检查 cron 和日志。'
         % ('不存在' if age is None else '已 %d 秒未更新' % age))
    try:
        marker.write_text(str(now))
    except OSError:
        pass
    return dict(alert=True, age=age)


def alive(state_dir, clock, send):
    """Daily one-line message (D2). Its absence is the earliest sign the host is down."""
    now = clock()
    age = _heartbeat_age(state_dir, now)
    try:
        paused = Store(state_dir, clock=lambda: now, readonly=True).get('pause')
    except sqlite3.Error:
        paused = None
    send('✅ 动量多空1号 存活。守护心跳：%s。状态：%s。'
         % ('无' if age is None else '%d 秒前' % age, '运行中' if paused is None else '暂停（%s）' % paused['reason']))
    return dict(heartbeat_age=age, paused=None if paused is None else paused['reason'])
