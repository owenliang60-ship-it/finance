import json

import scripts.prosperity_score as cli
from tests.prosperity_fixtures import seed_db


def _args(tmp_path, db, *extra):
    return ["--as-of", "2026-09-26", "--mode", "live", "--observed-at", "2026-09-26", "--db", str(db),
            "--out-dir", str(tmp_path / "out"), "--no-beta", *extra]


def test_cli_scores_all_schemes_read_only(tmp_path, monkeypatch):
    db = seed_db(tmp_path / "m.db")
    seen, real = {}, cli.MarketStore

    def spy(db_path=None, read_only=False):
        seen["read_only"] = read_only
        return real(db_path=db_path, read_only=read_only)

    monkeypatch.setattr(cli, "MarketStore", spy)
    assert cli.main(_args(tmp_path, db)) == 0 and seen["read_only"] is True
    summary = json.loads((tmp_path / "out" / "summary-2026-09-26.json").read_text())
    assert sorted(summary["schemes"]) == ["F0", "F1", "F1-exfin", "F1-rank"]
    assert summary["params_bootstrap"] is True and summary["future_leak_count"] == 0
    counts = summary["schemes"]["F1"]["counts"]
    assert counts["ranked"] + counts["observe"] + counts["excluded"] == summary["packets_built"]
    assert (tmp_path / "out" / "board-F1-2026-09-26.jsonl").exists()


def test_cli_rejects_unknown_scheme(tmp_path):
    assert cli.main(_args(tmp_path, seed_db(tmp_path / "m.db"), "--schemes", "F9")) == 4


def test_cli_fails_closed_on_leak(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "packet_leaks", lambda p: ["price_date later than as_of"])
    assert cli.main(_args(tmp_path, seed_db(tmp_path / "m.db"))) == 2
