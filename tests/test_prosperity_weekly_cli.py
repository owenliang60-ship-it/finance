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
