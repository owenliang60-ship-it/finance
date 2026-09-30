import json

import scripts.prosperity_inputs as cli
from tests.prosperity_fixtures import seed_db


def _args(tmp_path, db):
    return ["--as-of", "2026-09-26", "--mode", "live", "--observed-at", "2026-09-26", "--db", str(db),
            "--out-dir", str(tmp_path / "out"), "--symbols", "AAA", "--no-beta"]


def test_cli_writes_summary_and_exits_zero(tmp_path):
    db = seed_db(tmp_path / "m.db")
    assert cli.main(_args(tmp_path, db)) == 0
    summary = json.loads((tmp_path / "out" / "summary-2026-09-26.json").read_text())
    assert summary["members_resolved"] == 2 and summary["packets_built"] == 1
    assert summary["future_leak_count"] == 0 and summary["db_path"] == str(db)
    assert summary["named_cases"]["AAA"]["membership_basis"] == "extended_membership"
    assert (tmp_path / "out" / "inputs-2026-09-26.jsonl").read_text().count("\n") == 1


def test_cli_fails_closed_on_leak(tmp_path, monkeypatch):
    db = seed_db(tmp_path / "m.db")
    monkeypatch.setattr(cli, "packet_leaks", lambda p: ["price_date 2026-09-28 > as_of 2026-09-26"])
    assert cli.main(_args(tmp_path, db)) == 2


def test_cli_opens_database_read_only(tmp_path, monkeypatch):
    db = seed_db(tmp_path / "m.db")
    seen = {}
    real = cli.MarketStore

    def spy(db_path=None, read_only=False):
        seen["read_only"] = read_only
        return real(db_path=db_path, read_only=read_only)

    monkeypatch.setattr(cli, "MarketStore", spy)
    cli.main(_args(tmp_path, db))
    assert seen["read_only"] is True


def test_cli_rejects_non_canonical_dates(tmp_path):
    db = seed_db(tmp_path / "m.db")
    args = _args(tmp_path, db)
    args[args.index("--as-of") + 1] = "20260926"
    assert cli.main(args) == 4
