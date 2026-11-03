"""M13 Telegram delivery: ledger, pool diff, message text, deliver_week."""
import json

import pytest

from src.data import prosperity_delivery as d


# --- T2: ledger -----------------------------------------------------------------------------

def test_missing_ledger_reads_empty(tmp_path):
    assert d.read_ledger(tmp_path, '2026-11-14') == {'as_of': '2026-11-14', 'items': {}}


def test_ledger_round_trip_and_atomic_failure_keeps_old(tmp_path, monkeypatch):
    first = {'as_of': '2026-11-14', 'items': {'web': {'sent_at': '2026-11-14T08:40:00Z'}}}
    d.write_ledger(tmp_path, first)
    assert d.read_ledger(tmp_path, '2026-11-14') == first

    def broken(*a, **kw):
        raise OSError('disk full')
    monkeypatch.setattr(d.os, 'replace', broken)
    with pytest.raises(OSError):
        d.write_ledger(tmp_path, {'as_of': '2026-11-14', 'items': {}})
    assert d.read_ledger(tmp_path, '2026-11-14') == first
    assert [p.name for p in d.ledger_path(tmp_path, 'x').parent.iterdir()] == ['2026-11-14.json']


def test_previous_ledger_is_latest_before_as_of(tmp_path):
    for as_of in ('2026-10-31', '2026-11-07', '2026-11-21'):
        d.write_ledger(tmp_path, {'as_of': as_of, 'items': {}, 'pool_members': [as_of]})
    assert d.previous_ledger(tmp_path, '2026-11-14')['as_of'] == '2026-11-07'
    assert d.previous_ledger(tmp_path, '2026-10-31') is None


def test_previous_ledger_skips_weeks_without_pool_members(tmp_path):
    d.write_ledger(tmp_path, {'as_of': '2026-10-31', 'items': {}, 'pool_members': ['A']})
    d.write_ledger(tmp_path, {'as_of': '2026-11-07', 'items': {}})          # pool text never sent
    assert d.previous_ledger(tmp_path, '2026-11-14')['as_of'] == '2026-10-31'


# --- T2: pool diff and text -----------------------------------------------------------------

def test_pool_diff():
    assert d.pool_diff(None, ['A', 'B']) == ([], [])
    assert d.pool_diff(['A', 'B', 'C'], ['D', 'B', 'A']) == (['D'], ['C'])


def test_md_escape():
    assert d.md_escape('BRK_B *x* [y] `z`') == 'BRK\\_B \\*x\\* \\[y] \\`z\\`'


def test_web_caption_budget_warning():
    ok = d.web_caption(as_of='2026-11-14', strict=55, full=194, pool_count=36, size_bytes=12_000_000)
    assert ok.startswith('景气榜 2026-11-14') and 'STRICT 55' in ok and 'FULL 194' in ok and '入池 36' in ok
    assert '⚠' not in ok
    big = d.web_caption(as_of='2026-11-14', strict=55, full=194, pool_count=36, size_bytes=12_000_001)
    assert '⚠ 网页 12.0 MB，超出 12 MB 预算' in big
    assert len(big) <= 1024


def test_pool_text_lists_changes():
    text = d.pool_text(as_of='2026-11-14', previous_as_of='2026-11-07',
                       added=['BRK_B', 'MU'], removed=['XOM'], members=36, reason=None)
    assert '*景气精选池 2026-11-14*' in text
    assert '入池 36 只' in text
    assert '新进（2）: BRK\\_B, MU' in text and '移出（1）: XOM' in text
    assert '对比 2026-11-07' in text


def test_pool_text_first_week_and_unavailable():
    first = d.pool_text(as_of='2026-11-14', previous_as_of=None, added=[], removed=[], members=36, reason=None)
    assert '无上周对比' in first
    same = d.pool_text(as_of='2026-11-14', previous_as_of='2026-11-07', added=[], removed=[],
                       members=36, reason=None)
    assert '与上周相同' in same
    bad = d.pool_text(as_of='2026-11-14', previous_as_of=None, added=[], removed=[], members=0,
                      reason='prosperity_pool_stale')
    assert '池文件不可用（prosperity\\_pool\\_stale）' in bad and '新进' not in bad


# --- T3: deliver_week -----------------------------------------------------------------------

import hashlib
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.data.prosperity_pool import LiquidityResult, build_pool, publish_prosperity_pool
from src.data.prosperity_store import ProsperityStore
from terminal.prosperity.schemes import SCHEMES, scheme_hash
from tests.prosperity_fixtures import storage_batch, weekdays

VERSION = 'test-v1'
WEEK = '2026-10-03'
F1 = scheme_hash(SCHEMES['F1'])
NOW = datetime(2026, 10, 3, 9, 30, tzinfo=timezone.utc)


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _liquidity(stored):
    as_of = stored.board.as_of
    window = weekdays((date.fromisoformat(as_of) - timedelta(days=1)).isoformat(), 30)
    symbols = sorted(stored.meta.members)
    return LiquidityResult(as_of, tuple(window), {s: 1e9 - i * 1e6 for i, s in enumerate(symbols)},
                           {s: i + 1 for i, s in enumerate(symbols)})


@pytest.fixture()
def env(tmp_path):
    """Two quarter freezes (2026-09-26 Q2, 2026-10-03 Q3) and the live weekly boards of both Saturdays."""
    db = tmp_path / 'prosperity.db'
    with ProsperityStore(db, read_only=False) as s:
        s.save_batch(storage_batch(count=20, freeze=True, quarter_label='2026Q2'))
        s.save_batch(storage_batch(as_of=WEEK, count=20, freeze=True, quarter_label='2026Q3'))
        stored = s.get_board(next(k for k in s.list_boards(scheme_hash=F1, code_version=VERSION,
                                                            board_type='weekly') if k.as_of == WEEK))
    for suffix in ('-wal', '-shm'):
        extra = tmp_path / ('prosperity.db' + suffix)
        if extra.exists() and extra.stat().st_size == 0:
            extra.unlink()
    pool = tmp_path / 'pool.json'
    payload = build_pool(stored, _liquidity(stored), generated_at=WEEK + 'T09:10:00Z')
    publish_prosperity_pool(payload, pool, now=NOW)
    market = tmp_path / 'market.db'
    market.write_bytes(b'')
    return {'db': db, 'market': market, 'pool': pool, 'dir': tmp_path / 'delivery',
            'members': sorted(m['symbol'] for m in payload['members'])}


class FakeRun:
    """Stands in for subprocess.run: writes the --out file(s) and returns a chosen rc."""
    def __init__(self, web_bytes=1000, web_rc=0, health_rc=0):
        self.calls, self.web_bytes, self.web_rc, self.health_rc = [], web_bytes, web_rc, health_rc

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        out = Path(cmd[cmd.index('--out') + 1])
        if cmd[1].endswith('prosperity_web.py'):
            if self.web_rc == 0:
                out.write_bytes(b'x' * self.web_bytes)
            return subprocess.CompletedProcess(cmd, self.web_rc, '', 'web stderr')
        out.mkdir(parents=True, exist_ok=True)
        if self.health_rc == 0:
            (out / 'F1.html').write_text('<html>health</html>')
        return subprocess.CompletedProcess(cmd, self.health_rc, '', 'health stderr')

    def scripts(self):
        return [Path(cmd[1]).name for cmd, _ in self.calls]


class FakeSend:
    def __init__(self, fail_on=()):
        self.docs, self.msgs, self.fail_on, self.n = [], [], set(fail_on), 0

    def _ok(self):
        self.n += 1
        return self.n not in self.fail_on

    def document(self, path, caption='', channel='group', max_retries=3, timeout=60):
        assert channel == 'private'
        ok = self._ok()
        if ok:
            self.docs.append((Path(path).name, caption, timeout))
        return ok

    def message(self, text, channel='private', max_retries=3):
        assert channel == 'private'
        ok = self._ok()
        if ok:
            self.msgs.append(text)
        return ok


def deliver(env, run, send, as_of=WEEK, now=NOW):
    return d.deliver_week(as_of=as_of, code_version=VERSION, db=env['db'], market_db=env['market'],
                          pool_path=env['pool'], delivery_dir=env['dir'], now=lambda: now,
                          send_document=send.document, send_message=send.message, run=run)


def seed_previous(env, *, freeze_as_of='2026-09-26', members=None, version=VERSION):
    ledger = {'as_of': '2026-09-26', 'items': {'web': {'sent_at': 'x'}, 'pool_text': {'sent_at': 'x'},
              'health': {'freeze_as_of': freeze_as_of, 'baseline': True, 'code_version': version}}}
    if members is not None:
        ledger['pool_members'] = members
    d.write_ledger(env['dir'], ledger)


def test_first_delivery_sends_web_and_text_and_records_baseline(env):
    run, send = FakeRun(), FakeSend()
    before = _sha(env['db'])
    out = deliver(env, run, send)
    assert out.status == 'delivered' and out.sent == ('web', 'pool_text')
    assert run.scripts() == ['prosperity_web.py']
    cmd, kw = run.calls[0]
    assert cmd[0] == sys.executable and kw['timeout'] == 600
    assert cmd[cmd.index('--code-version') + 1] == VERSION and cmd[cmd.index('--pool') + 1] == str(env['pool'])
    assert Path(cmd[cmd.index('--db') + 1]) != env['db']                 # a snapshot, never the live db
    name, caption, timeout = send.docs[0]
    assert name == 'prosperity-web-20261003.html' and timeout == 300
    assert caption == f'景气榜 2026-10-03 · F1 STRICT 2 / FULL 7 · 入池 {len(env["members"])}'
    assert '无上周对比' in send.msgs[0]
    ledger = d.read_ledger(env['dir'], WEEK)
    assert set(ledger['items']) == {'web', 'pool_text', 'health'}
    assert ledger['items']['health'] == {'freeze_as_of': WEEK, 'baseline': True, 'code_version': VERSION}
    assert ledger['pool_members'] == env['members']
    assert out.baseline == WEEK
    assert _sha(env['db']) == before
    assert not (env['dir'] / 'tmp').exists()                              # snapshots removed


def test_rerun_is_already_delivered_without_snapshot(env):
    deliver(env, FakeRun(), FakeSend())
    run, send = FakeRun(), FakeSend()
    out = deliver(env, run, send)
    assert out.status == 'already_delivered' and out.sent == () and not run.calls and not send.docs


def test_partial_failure_resends_only_missing_items(env):
    send = FakeSend(fail_on={2})                                          # web ok, pool text fails
    out = deliver(env, FakeRun(), send)
    assert out.status == 'failed' and out.sent == ('web',) and 'pool_text' in out.error
    assert set(d.read_ledger(env['dir'], WEEK)['items']) == {'web'}
    run, send = FakeRun(), FakeSend()
    out = deliver(env, run, send)
    assert out.status == 'delivered' and out.sent == ('pool_text',) and not run.calls and not send.docs


def test_web_cli_failure_and_size_limits(env):
    out = deliver(env, FakeRun(web_rc=4), FakeSend())
    assert out.status == 'failed' and 'prosperity_web.py rc=4' in out.error
    send = FakeSend()
    out = deliver(env, FakeRun(web_bytes=d.WEB_SEND_LIMIT_BYTES + 1), send)
    assert out.status == 'failed' and 'web_too_large' in out.error and not send.docs
    send = FakeSend()
    assert deliver(env, FakeRun(web_bytes=d.WEB_BUDGET_BYTES + 1), send).status == 'delivered'
    assert '⚠ 网页' in send.docs[0][1]


def test_health_sent_when_freeze_is_newer_than_last(env):
    seed_previous(env, members=env['members'][1:] + ['GONE'])
    run, send = FakeRun(), FakeSend()
    out = deliver(env, run, send)
    assert out.status == 'delivered' and out.sent == ('web', 'pool_text', 'health')
    assert run.scripts() == ['prosperity_web.py', 'prosperity_health.py']
    cmd, kw = run.calls[1]
    assert kw['timeout'] == 1800 and cmd[cmd.index('--schemes') + 1] == 'F1'
    assert cmd[cmd.index('--code-version') + 1] == VERSION
    assert Path(cmd[cmd.index('--market-db') + 1]) != env['market']
    assert send.docs[1][:2] == ('prosperity-health-F1-20261003.html', 'F1 体检报告 · 冻结 2026-10-03')
    assert f'新进（1）: {d.md_escape(env["members"][0])}' in send.msgs[0] and '移出（1）: GONE' in send.msgs[0]
    health = d.read_ledger(env['dir'], WEEK)['items']['health']
    assert health['freeze_as_of'] == WEEK and health['code_version'] == VERSION and 'sent_at' in health


def test_health_not_due_when_already_sent_and_version_change_sends_once(env):
    seed_previous(env, freeze_as_of=WEEK)
    run = FakeRun()
    assert deliver(env, run, FakeSend()).sent == ('web', 'pool_text')
    assert d.read_ledger(env['dir'], WEEK)['items']['health'] == {
        'freeze_as_of': WEEK, 'not_due': True, 'code_version': VERSION}
    env['dir'] = env['dir'].parent / 'delivery2'
    seed_previous(env, freeze_as_of=WEEK, version='old-engine')
    run = FakeRun()
    assert deliver(env, run, FakeSend()).sent == ('web', 'pool_text', 'health')


def test_health_failure_keeps_other_items_and_retries(env):
    seed_previous(env)
    out = deliver(env, FakeRun(health_rc=6), FakeSend())
    assert out.status == 'failed' and out.sent == ('web', 'pool_text') and 'prosperity_health.py rc=6' in out.error
    assert 'health' not in d.read_ledger(env['dir'], WEEK)['items']
    out = deliver(env, FakeRun(), FakeSend())
    assert out.sent == ('health',)


def test_two_failed_weeks_still_send_the_freeze(env):
    seed_previous(env)
    d.write_ledger(env['dir'], {'as_of': '2026-09-27', 'items': {'web': {'sent_at': 'x'}}})   # failed week
    assert deliver(env, FakeRun(), FakeSend()).sent == ('web', 'pool_text', 'health')


def test_unavailable_pool_sends_reason_without_members(env):
    seed_previous(env, members=['A'])
    env['pool'].unlink()
    send = FakeSend()
    out = deliver(env, FakeRun(), send)
    assert out.status == 'delivered'
    assert '池文件不可用（prosperity\\_pool\\_missing）' in send.msgs[0]
    assert 'pool_members' not in d.read_ledger(env['dir'], WEEK)


def test_pool_from_another_week_is_not_compared(env):
    with ProsperityStore(env['db']) as s:                                   # last week's F1 board
        stored = s.get_board(next(k for k in s.list_boards(scheme_hash=F1, code_version=VERSION,
                                                            board_type='weekly') if k.as_of != WEEK))
    payload = build_pool(stored, _liquidity(stored), generated_at=WEEK + 'T09:10:00Z')
    publish_prosperity_pool(payload, env['pool'], now=NOW)
    seed_previous(env, members=['A'])
    send = FakeSend()
    assert deliver(env, FakeRun(), send).status == 'delivered'
    assert 'pool\\_as\\_of\\_mismatch' in send.msgs[0] and '入池 0' in send.docs[0][1]
    assert 'pool_members' not in d.read_ledger(env['dir'], WEEK)


def test_missing_weekly_board_fails_before_sending(env):
    send = FakeSend()
    out = deliver(env, FakeRun(), send, as_of='2026-10-10',
                  now=datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc))
    assert out.status == 'failed' and 'weekly_board_missing' in out.error and not send.docs and not send.msgs


def test_insufficient_space_takes_no_snapshot(env, monkeypatch):
    def full(path):
        raise ValueError('insufficient_space')
    monkeypatch.setattr(d, 'check_space', full)
    run = FakeRun()
    out = deliver(env, run, FakeSend())
    assert out.status == 'failed' and 'insufficient_space' in out.error and not run.calls


def test_errors_never_leak_the_token(env, monkeypatch):
    monkeypatch.setattr('src.telegram_bot.TELEGRAM_BOT_TOKEN', 'secret-token')
    def boom(*a, **kw):
        raise RuntimeError('https://api.telegram.org/botsecret-token/sendDocument failed')
    out = deliver(env, FakeRun(), type('S', (), {'document': staticmethod(boom), 'message': staticmethod(boom)}))
    assert out.status == 'failed' and 'secret-token' not in out.error and '<redacted>' in out.error


def test_out_keeps_two_weeks(env):
    for as_of in ('2026-09-19', '2026-09-26'):
        (env['dir'] / 'out' / as_of).mkdir(parents=True)
    deliver(env, FakeRun(), FakeSend())
    assert sorted(p.name for p in (env['dir'] / 'out').iterdir()) == ['2026-09-26', WEEK]


def test_last_health_freeze_rules(tmp_path):
    assert d.last_health_freeze(tmp_path, WEEK, VERSION) is None
    d.write_ledger(tmp_path, {'as_of': '2026-09-19', 'items': {
        'health': {'freeze_as_of': '2026-08-29', 'sent_at': 'x', 'code_version': VERSION}}})
    d.write_ledger(tmp_path, {'as_of': '2026-09-26', 'items': {
        'health': {'freeze_as_of': '2026-08-29', 'not_due': True, 'code_version': VERSION}}})
    assert d.last_health_freeze(tmp_path, WEEK, VERSION) == '2026-08-29'
    assert d.last_health_freeze(tmp_path, WEEK, 'new') is d.VERSION_CHANGED


def test_real_web_cli_integration(env):
    send = FakeSend()
    out = deliver(env, subprocess.run, send)
    assert out.status == 'delivered', out.error
    page = env['dir'] / 'out' / WEEK / 'prosperity-web-20261003.html'
    assert page.is_file() and '<section id=' in page.read_text(encoding='utf-8')
