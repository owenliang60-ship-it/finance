from datetime import datetime, timezone

import pytest


NOW = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize('args', [
    ['--live'], ['--live', '--as-of', '2026-10-17', '--db', 'x.db'],
    ['--as-of', '2026-10-24', '--db', 'x.db'],
    ['--as-of', '2026-10-23', '--db', 'x.db'],
    ['--rebuild-chain', '--through', '2026-10-24', '--db', 'x.db'],
    ['--live', '--observed-at', '2026-10-24T08:00:00', '--db', 'x.db'],
    ['--live', '--observed-at', '2026-10-24T10:00:00Z', '--db', 'x.db'],
    ['--as-of', '2026-10-17', '--observed-at', '2026-10-17T08:00:00Z', '--db', 'x.db'],
])
def test_cli_rejects_invalid_without_creating_db(args, tmp_path, monkeypatch):
    from scripts.prosperity_weekly import main
    monkeypatch.chdir(tmp_path)
    assert main(args, now=lambda: NOW) == 4
    assert not (tmp_path / 'x.db').exists()


def test_cli_default_rebuild_through_and_paths(tmp_path, monkeypatch):
    from scripts import prosperity_weekly as cli
    from tests.prosperity_fixtures import seed_db
    from src.data.prosperity_run import RunOutcome
    captured = {}
    def rebuild(**kw):
        captured.update(kw)
        return RunOutcome(0)
    monkeypatch.setattr(cli, 'rebuild_freeze_chain', rebuild)
    db = seed_db(tmp_path / 'market.db')
    assert cli.main(['--rebuild-chain', '--db', str(tmp_path / 'research.db'), '--market-db', str(db)],
                    now=lambda: NOW) == 0
    assert captured['through'] == '2026-10-17'
    assert captured['market'].read_only


def test_cli_non_saturday_and_insufficient_space_never_creates(tmp_path, monkeypatch):
    from scripts import prosperity_weekly as cli
    target = tmp_path / 'result.db'
    assert cli.main(['--live', '--db', str(target)], now=lambda: datetime(2026, 10, 25, tzinfo=timezone.utc)) == 4
    def no_space(path):
        raise ValueError('insufficient_space')
    monkeypatch.setattr(cli, 'check_space', no_space)
    assert cli.main(['--live', '--db', str(target)], now=lambda: NOW) == 6
    assert not target.exists()


@pytest.mark.parametrize('extra,count', [([], 0), (['--pool-path', 'POOL'], 1)])
def test_cli_pool_path_configures_publisher(tmp_path, monkeypatch, extra, count):
    from scripts import prosperity_weekly as cli
    from tests.prosperity_fixtures import seed_db
    from src.data.prosperity_run import RunOutcome
    captured = {}
    def week(**kw):
        captured.update(kw)
        return RunOutcome(0)
    monkeypatch.setattr(cli, 'run_week', week)
    db = seed_db(tmp_path / 'market.db')
    extra = [str(tmp_path / 'pool.json') if x == 'POOL' else x for x in extra]
    assert cli.main(['--live', '--db', str(tmp_path / 'r.db'), '--market-db', str(db)] + extra,
                    now=lambda: NOW) == 0
    assert len(captured['publishers']) == count
    assert not (tmp_path / 'pool.json').exists()


@pytest.mark.parametrize('target', ['market', 'result'])
def test_cli_pool_path_must_not_be_a_database(tmp_path, monkeypatch, target):
    from scripts import prosperity_weekly as cli
    from tests.prosperity_fixtures import seed_db
    db = seed_db(tmp_path / 'market.db')
    before = db.read_bytes()
    result = tmp_path / 'r.db'
    pool = db if target == 'market' else result
    assert cli.main(['--live', '--db', str(result), '--market-db', str(db),
                     '--pool-path', str(tmp_path / '.' / pool.name)], now=lambda: NOW) == 4
    assert db.read_bytes() == before and not result.exists()


# --- M13 delivery wiring ----------------------------------------------------------------------

def _wire(tmp_path, monkeypatch, outcome, delivered=None, raise_exc=None):
    from scripts import prosperity_weekly as cli
    from src.data import prosperity_delivery
    from tests.prosperity_fixtures import seed_db
    calls = []

    def week(**kw):
        return outcome

    def deliver(**kw):
        calls.append(kw)
        if raise_exc:
            raise raise_exc
        return delivered or prosperity_delivery.DeliveryOutcome('delivered', ('web', 'pool_text'))
    monkeypatch.setattr(cli, 'run_week', week)
    monkeypatch.setattr(prosperity_delivery, 'deliver_week', deliver)
    market = seed_db(tmp_path / 'market.db')
    return cli, market, calls


def _live(tmp_path, market, *extra):
    return ['--live', '--db', str(tmp_path / 'r.db'), '--market-db', str(market),
            '--pool-path', str(tmp_path / 'pool.json'), *extra]


@pytest.mark.parametrize('publication', ['published', 'already_published'])
def test_cli_delivers_after_a_published_live_week(tmp_path, monkeypatch, capsys, publication):
    from src.data.prosperity_run import RunOutcome
    cli, market, calls = _wire(tmp_path, monkeypatch, RunOutcome(0, (), detail={'publication': publication}))
    rc = cli.main(_live(tmp_path, market, '--delivery-dir', str(tmp_path / 'delivery')), now=lambda: NOW)
    assert rc == 0 and len(calls) == 1
    kw = calls[0]
    assert kw['as_of'] == '2026-10-24' and kw['pool_path'] == tmp_path / 'pool.json'
    assert kw['delivery_dir'] == tmp_path / 'delivery' and kw['db'] == tmp_path / 'r.db'
    from terminal.prosperity.version import code_version
    assert kw['code_version'] == code_version()
    import json
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed['delivery']['status'] == 'delivered'


@pytest.mark.parametrize('rc,detail', [(0, {'publication': 'publication_not_configured'}), (0, None),
                                       (3, None), (5, {'publication': 'published'}), (6, None)])
def test_cli_does_not_deliver_unless_published(tmp_path, monkeypatch, rc, detail):
    from src.data.prosperity_run import RunOutcome
    cli, market, calls = _wire(tmp_path, monkeypatch, RunOutcome(rc, (), detail=detail))
    assert cli.main(_live(tmp_path, market, '--delivery-dir', str(tmp_path / 'd')), now=lambda: NOW) == rc
    assert calls == []


def test_cli_delivery_failure_and_exception_return_7(tmp_path, monkeypatch, capsys):
    from src.data.prosperity_delivery import DeliveryOutcome
    from src.data.prosperity_run import RunOutcome
    ok = RunOutcome(0, (), detail={'publication': 'published'})
    cli, market, _ = _wire(tmp_path, monkeypatch, ok, delivered=DeliveryOutcome('failed', ('web',), 'pool_text: x'))
    assert cli.main(_live(tmp_path, market, '--delivery-dir', str(tmp_path / 'd')), now=lambda: NOW) == 7
    assert '"status":"failed"' in capsys.readouterr().out.replace(' ', '')
    second = tmp_path / 'second'
    second.mkdir()
    cli, market, _ = _wire(second, monkeypatch, ok, raise_exc=ImportError('scipy'))
    assert cli.main(_live(second, market, '--delivery-dir', str(second / 'd')), now=lambda: NOW) == 7


def test_cli_already_delivered_is_0(tmp_path, monkeypatch):
    from src.data.prosperity_delivery import DeliveryOutcome
    from src.data.prosperity_run import RunOutcome
    cli, market, _ = _wire(tmp_path, monkeypatch, RunOutcome(0, (), detail={'publication': 'already_published'}),
                           delivered=DeliveryOutcome('already_delivered'))
    assert cli.main(_live(tmp_path, market, '--delivery-dir', str(tmp_path / 'd')), now=lambda: NOW) == 0


def test_cli_without_delivery_dir_never_delivers(tmp_path, monkeypatch, capsys):
    from src.data.prosperity_run import RunOutcome
    cli, market, calls = _wire(tmp_path, monkeypatch, RunOutcome(0, (), detail={'publication': 'published'}))
    assert cli.main(_live(tmp_path, market), now=lambda: NOW) == 0
    assert calls == [] and '"delivery"' not in capsys.readouterr().out


def test_cli_replay_modes_accept_delivery_dir_without_delivering(tmp_path, monkeypatch):
    from src.data.prosperity_run import RunOutcome
    from scripts import prosperity_weekly as cli
    cli_, market, calls = _wire(tmp_path, monkeypatch, RunOutcome(0, ()))
    monkeypatch.setattr(cli, 'rebuild_freeze_chain', lambda **kw: RunOutcome(0, ()))
    base = ['--db', str(tmp_path / 'r.db'), '--market-db', str(market), '--delivery-dir', str(tmp_path / 'd')]
    assert cli.main(['--as-of', '2026-10-17', *base], now=lambda: NOW) == 0
    assert cli.main(['--rebuild-chain', *base], now=lambda: NOW) == 0
    assert calls == []


def test_cli_live_delivery_requires_pool_path(tmp_path, monkeypatch):
    from src.data.prosperity_run import RunOutcome
    cli, market, calls = _wire(tmp_path, monkeypatch, RunOutcome(0, (), detail={'publication': 'published'}))
    assert cli.main(['--live', '--db', str(tmp_path / 'r.db'), '--market-db', str(market),
                     '--delivery-dir', str(tmp_path / 'd')], now=lambda: NOW) == 4
    assert calls == [] and not (tmp_path / 'r.db').exists()


@pytest.mark.parametrize('target', ['market.db', 'r.db', 'r.db-wal', 'r.db.state'])
def test_cli_delivery_dir_must_not_be_a_database(tmp_path, monkeypatch, target):
    from src.data.prosperity_run import RunOutcome
    cli, market, calls = _wire(tmp_path, monkeypatch, RunOutcome(0, (), detail={'publication': 'published'}))
    assert cli.main(_live(tmp_path, market, '--delivery-dir', str(tmp_path / '.' / target)),
                    now=lambda: NOW) == 4
    assert calls == []


@pytest.mark.parametrize('flag', ['--db', '--market-db', '--pool-path'])
def test_cli_inputs_must_be_outside_delivery_dir(tmp_path, monkeypatch, capsys, flag):
    """second review P1: delivery cleanup must never reach an input; refused before the week runs."""
    from src.data.prosperity_run import RunOutcome
    cli, market, calls = _wire(tmp_path, monkeypatch, RunOutcome(0, (), detail={'publication': 'published'}))
    argv = _live(tmp_path, market, '--delivery-dir', str(tmp_path / 'd'))
    argv[argv.index(flag) + 1] = str(tmp_path / 'd' / 'tmp' / 'input')
    assert cli.main(argv, now=lambda: NOW) == 4
    assert calls == [] and 'inputs_must_be_outside_delivery_dir' in capsys.readouterr().err
