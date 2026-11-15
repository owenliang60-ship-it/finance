"""Operator commands. The runtime builder is replaced so no network or credentials are needed."""
import json

import pandas as pd
import pytest

from tests.momentum_ls01_fake_exchange import FakeClock, FakeExchange
from tests.test_momentum_ls01_faults import ANCHOR, S0, World
from trading.momentum_ls01 import cli
from trading.momentum_ls01.binance import Endpoints as E


@pytest.fixture
def ops(tmp_path, monkeypatch, capsys):
    w = World(tmp_path)
    built = []

    def build(args, writes):
        built.append(writes)
        return w.rt

    monkeypatch.setattr(cli, 'build_runtime', build)
    monkeypatch.setattr(cli, 'clock', w.clock.time)
    monkeypatch.setattr(cli, 'private_sender', lambda: w._send)

    def run(*argv):
        code = cli.main([*argv, '--state-dir', str(w.state_dir)])
        return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return w, run, built


def test_status_reports_pause_heartbeat_and_week_without_touching_the_exchange(ops):
    w, run, built = ops
    w.open()
    w.guard()
    calls = len(w.ex.calls)
    code, out = run('status')
    assert code == 0 and out['paused'] is None and out['week']['status'] == 'done'
    assert out['heartbeat_age'] == 0 and S0 in out['episodes'] and out['unresolved_requests'] == 0
    assert len(w.ex.calls) == calls and built == []


def test_pause_then_resume_round_trip(ops):
    w, run, built = ops
    w.open()
    assert run('pause', '--reason', 'boss says stop')[1]['paused']['reason'] == 'manual_pause'
    assert run('status')[1]['paused']['detail'] == 'boss says stop'
    code, out = run('resume')
    assert code == 0 and out['status'] == 'resumed' and built == [True]
    assert run('status')[1]['paused'] is None


def test_rebalance_without_live_flag_is_a_dry_run(ops):
    w, run, built = ops
    w.prepare()
    code, out = run('rebalance')
    assert code == 0 and out['status'] == 'dry_run' and built == [False]
    assert not w.ex.writes()
    code, out = run('rebalance', '--live')
    assert code == 0 and out['status'] == 'done' and built == [False, True] and w.qty(S0) == -10


def test_rebalance_returns_nonzero_when_nothing_was_added(ops):
    w, run, built = ops
    w.prepare(fail='fisher_week_missing')
    code, out = run('rebalance', '--live')
    assert code == 1 and out['status'] == 'no_new'


def test_backup_writes_a_restorable_copy(ops):
    w, run, built = ops
    w.open()
    code, out = run('backup')
    assert code == 0 and (w.state_dir / 'backups').glob('state-*.db')
    assert out['path'].endswith('.db')


def test_readonly_guard_is_built_without_write_permission_and_obeys_the_drill_marker(ops):
    w, run, built = ops
    w.open()
    code, out = run('guard', '--readonly')
    assert code == 0 and out['status'] == 'ok' and built == [False]
    beat = w.rt.store.get('guard_heartbeat')['at']
    (w.state_dir / 'drill-stop-guard').write_text('')
    w.clock.now += 60
    code, out = run('guard', '--readonly')
    assert out['status'] == 'drill_skipped' and w.rt.store.get('guard_heartbeat')['at'] == beat
    code, out = run('guard')   # the live guard ignores the drill marker
    assert out['status'] == 'ok' and w.rt.store.get('guard_heartbeat')['at'] == w.clock.now


def test_watchdog_and_alive_use_the_private_sender(ops):
    w, run, built = ops
    code, out = run('watchdog')
    assert out['alert'] is True and any('心跳' in text for text in w.sent)
    code, out = run('alive')
    assert code == 0 and any('存活' in text for text in w.sent)


def test_reconcile_readonly_reports_account_facts_without_writes(ops):
    w, run, built = ops
    w.open()
    writes = len(w.ex.writes())
    code, out = run('reconcile')
    assert code == 0 and built == [False] and len(w.ex.writes()) == writes
    assert out['one_way'] is True and out['single_asset'] is True and out['open_positions'] == 5
    assert out['key_permissions'] == 'NOT_CHECKED'
