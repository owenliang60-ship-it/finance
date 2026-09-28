"""Tests for the backfill runner's frozen targets file and dataset subset
(prosperity M2 Task 1).

D9 history backfill freezes the 20-quarter-end member union into a JSON file
once and collects only the three statement tables. Invariants under test:
the file's symbols are exactly the grid, the dataset subset is exactly the
grid's columns, statements-only runs never rebuild the legacy profiles.json
mirror, and the CLI rejects combinations that would silently pick a
different target list.
"""
import json

import pytest

from scripts.backfill_extended_fundamentals import (
    STATEMENT_DATASETS,
    load_targets_file,
    parse_args,
    run_backfill,
)
from src.data.market_store import MarketStore
from tests.test_backfill_runner import FakeClient, FakeLock


@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


def _write_targets(tmp_path, symbols, name="targets.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"symbols": symbols, "source": "test"}),
                    encoding="utf-8")
    return path


def _grid(store, run_id):
    rows = store._get_conn().execute(
        "SELECT symbol, dataset FROM fundamental_backfill_jobs WHERE run_id = ?",
        [run_id]).fetchall()
    return {(r["symbol"], r["dataset"]) for r in rows}


def test_targets_file_freezes_exact_symbols_and_datasets(tmp_store, tmp_path):
    # No SM rows and no membership at all: the targets file alone decides.
    client = FakeClient()
    targets = load_targets_file(_write_targets(tmp_path, ["abc", "COR", "ABC"]))
    assert targets == ["ABC", "COR"]

    rc = run_backfill(run_id="r1", store=tmp_store, client=client, lock=FakeLock(),
                      targets_override=targets, datasets=STATEMENT_DATASETS,
                      limit_quarters=40)
    assert rc == 0
    assert _grid(tmp_store, "r1") == {(s, d) for s in ("ABC", "COR")
                                      for d in ("income", "balance", "cashflow")}
    assert {c["kind"] for c in client.calls} == {"income", "balance", "cashflow"}
    assert {c["limit"] for c in client.calls} == {40}
    params = json.loads(tmp_store._get_conn().execute(
        "SELECT params_json FROM fundamental_backfill_runs WHERE run_id='r1'"
    ).fetchone()[0])
    assert params["datasets"] == ["income", "balance", "cashflow"]
    assert params["targets_override_count"] == 2


def test_resume_keeps_manifest_grid_over_targets_file(tmp_store, tmp_path):
    first = FakeClient(status="fetch_failed")
    run_backfill(run_id="r1", store=tmp_store, client=first, lock=FakeLock(),
                 targets_override=["ABC"], datasets=("income",))
    retry = FakeClient()
    rc = run_backfill(run_id="r1", store=tmp_store, client=retry, lock=FakeLock(),
                      resume=True, targets_override=["ZZZ"], datasets=("income",))
    assert rc == 0
    assert retry.symbols_fetched == ["ABC"]


def test_statements_only_skips_profile_mirror(tmp_store, tmp_path):
    mirror = tmp_path / "profiles.json"
    rc = run_backfill(run_id="r2", store=tmp_store, client=FakeClient(),
                      lock=FakeLock(), targets_override=["ABC"],
                      datasets=STATEMENT_DATASETS, profiles_mirror_path=mirror)
    assert rc == 0
    assert not mirror.exists()


def test_load_targets_file_rejects_empty_and_malformed(tmp_path):
    with pytest.raises(ValueError):
        load_targets_file(_write_targets(tmp_path, []))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(["ABC"]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_targets_file(bad)


def test_run_backfill_rejects_unknown_dataset(tmp_store):
    with pytest.raises(ValueError):
        run_backfill(run_id="r3", store=tmp_store, client=FakeClient(),
                     lock=FakeLock(), targets_override=["ABC"],
                     datasets=("income", "estimates"))


def test_parse_args_targets_file_and_datasets(tmp_path):
    f = _write_targets(tmp_path, ["ABC"])
    args = parse_args(["--run-id", "x", "--targets-file", str(f),
                       "--datasets", "income,balance,cashflow"])
    assert args.targets_file == str(f)
    assert args.datasets == ("income", "balance", "cashflow")
    assert parse_args(["--run-id", "x"]).datasets is None

    with pytest.raises(SystemExit):
        parse_args(["--run-id", "x", "--targets-file", str(f),
                    "--include-historical", "--as-of", "2024-06-30"])
    with pytest.raises(SystemExit):
        parse_args(["--run-id", "x", "--datasets", "income,estimates"])
    with pytest.raises(SystemExit):
        parse_args(["--run-id", "x", "--verify-only", "--targets-file", str(f)])
