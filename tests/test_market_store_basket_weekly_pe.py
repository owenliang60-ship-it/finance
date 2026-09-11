"""Storage contract for basket_weekly_pe_history (plan §3.3, Task 2).

Covers: DDL/index bootstrap, atomic whole-batch upsert, CHECK/fail-fast
validation, methodology_version silent-overwrite protection, the R5 tail
evolution contract (estimate -> actual upgrade legal / downgrade rejected),
read-only query side-effect freedom, and table-whitelist registration.
"""
import datetime as dt_module
import json
import logging
import sqlite3

import pytest

from src.data import market_store
from src.data.market_store import MarketStore, _validate_table

EXPECTED_TABLES = {"basket_weekly_pe_history", "basket_pe_backfill_runs"}


@pytest.fixture
def store(tmp_path):
    value = MarketStore(tmp_path / "market.db")
    yield value
    value.close()


def _row(basket="SPY", valuation_date="2026-07-10", methodology_version="v1",
         run_id="run-fixture",
         quality_tier="actual_only", hindsight_actual_quarters=4,
         hindsight_estimate_quarters=0, ttm_pe_gaap=22.0,
         mcap_coverage_ttm=0.98, mcap_coverage_hindsight=0.97):
    return {
        "basket": basket,
        "valuation_date": valuation_date,
        "ttm_pe_gaap": ttm_pe_gaap,
        "hindsight_ntm_pe_gaap": 21.0,
        "ttm_total_mcap": 1.0e13,
        "ttm_net_income": 4.5e11,
        "hindsight_total_mcap": 1.0e13,
        "hindsight_ntm_net_income": 4.8e11,
        "n_members": 500,
        "n_covered_ttm": 495,
        "n_covered_hindsight": 494,
        "mcap_coverage_ttm": mcap_coverage_ttm,
        "mcap_coverage_hindsight": mcap_coverage_hindsight,
        "hindsight_actual_quarters": hindsight_actual_quarters,
        "hindsight_estimate_quarters": hindsight_estimate_quarters,
        "composition_effective_date": "2026-06-22",
        "composition_available_date": "2026-07-01",
        "quality_tier": quality_tier,
        "run_id": run_id,
        "members_json": [{"symbol": "AAPL"}],
        "warnings_json": [],
        "methodology_version": methodology_version,
    }


# 1. fresh DB 自动建表和索引
def test_fresh_db_creates_table_and_index(store):
    conn = sqlite3.connect(str(store.db_path))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert EXPECTED_TABLES <= tables
    assert "idx_bwph_basket_date" in indexes


# 2. whole-batch upsert 成功
def test_whole_batch_upsert_succeeds(store):
    n = store.upsert_basket_weekly_pe_batch([
        _row(valuation_date="2026-07-03"),
        _row(valuation_date="2026-07-10"),
    ])
    assert n == 2
    got = store.get_basket_weekly_pe_history("SPY")
    assert [r["valuation_date"] for r in got] == ["2026-07-03", "2026-07-10"]


# 3. 中间坏行触发整批 rollback (DB-state-dependent rejection mid-transaction)
def test_bad_row_mid_batch_rolls_back_entire_batch(store):
    store.upsert_basket_weekly_pe_batch([
        _row(valuation_date="2026-07-03", methodology_version="v1",
             quality_tier="actual_only"),
    ])
    with pytest.raises(ValueError):
        store.upsert_basket_weekly_pe_batch([
            _row(valuation_date="2026-07-10", methodology_version="v1"),
            # This row downgrades the already-complete 07-03 row -> rejected
            # only once the DB is consulted mid-transaction.
            _row(valuation_date="2026-07-03", methodology_version="v1",
                 quality_tier="latest_consensus_tail",
                 hindsight_actual_quarters=2, hindsight_estimate_quarters=2),
        ])
    got = store.get_basket_weekly_pe_history("SPY")
    # Neither the new 07-10 row nor the downgrade survive: whole batch rolled back.
    assert [r["valuation_date"] for r in got] == ["2026-07-03"]
    assert got[0]["quality_tier"] == "actual_only"


# 4. coverage / quarter count / quality tier CHECK/fail-fast
@pytest.mark.parametrize("bad_kwargs", [
    {"mcap_coverage_ttm": 1.5},
    {"mcap_coverage_hindsight": -0.1},
    {"hindsight_actual_quarters": 5},
    {"quality_tier": "bogus_tier"},
    {"hindsight_actual_quarters": 1, "hindsight_estimate_quarters": 1},  # sums to 2, publishable tier
])
def test_invalid_row_fields_fail_fast(store, bad_kwargs):
    with pytest.raises(ValueError):
        store.upsert_basket_weekly_pe_batch([_row(**bad_kwargs)])
    assert store.get_basket_weekly_pe_history("SPY") == []


# 5. (basket, valuation_date) 幂等更新同 methodology version
def test_idempotent_update_same_methodology_version(store):
    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="latest_consensus_tail",
             hindsight_actual_quarters=3, hindsight_estimate_quarters=1,
             ttm_pe_gaap=22.0, methodology_version="v1"),
    ])
    first = store.get_basket_weekly_pe_history("SPY")[0]
    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="latest_consensus_tail",
             hindsight_actual_quarters=3, hindsight_estimate_quarters=1,
             ttm_pe_gaap=22.5, methodology_version="v1"),
    ])
    second = store.get_basket_weekly_pe_history("SPY")[0]
    assert second["ttm_pe_gaap"] == 22.5
    assert second["created_at"] == first["created_at"]


# 6. methodology version 不同且已有 row 时一律拒绝静默覆盖（team-lead 裁定：
# 不区分 quality_tier — 跨版本覆盖 tail/unpublishable 行同样会让历史序列在
# 版本边界静默混口径，必须走显式版本迁移：先按 methodology_version 精确删除
# 旧行，再重新 backfill）。三个 tier 各测一次。
def test_different_methodology_over_complete_row_rejected(store):
    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="actual_only", methodology_version="v1"),
    ])
    with pytest.raises(ValueError):
        store.upsert_basket_weekly_pe_batch([
            _row(quality_tier="actual_only", methodology_version="v2",
                 ttm_pe_gaap=99.0),
        ])
    got = store.get_basket_weekly_pe_history("SPY")
    assert len(got) == 1
    assert got[0]["methodology_version"] == "v1"
    assert got[0]["ttm_pe_gaap"] == 22.0


def test_different_methodology_over_tail_row_rejected(store):
    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="latest_consensus_tail",
             hindsight_actual_quarters=2, hindsight_estimate_quarters=2,
             methodology_version="v1"),
    ])
    with pytest.raises(ValueError):
        store.upsert_basket_weekly_pe_batch([
            _row(quality_tier="latest_consensus_tail",
                 hindsight_actual_quarters=2, hindsight_estimate_quarters=2,
                 methodology_version="v2", ttm_pe_gaap=99.0),
        ])
    got = store.get_basket_weekly_pe_history("SPY")
    assert len(got) == 1
    assert got[0]["methodology_version"] == "v1"
    assert got[0]["ttm_pe_gaap"] == 22.0


def test_different_methodology_over_unpublishable_row_rejected(store):
    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="unpublishable",
             hindsight_actual_quarters=1, hindsight_estimate_quarters=1,
             methodology_version="v1"),
    ])
    with pytest.raises(ValueError):
        store.upsert_basket_weekly_pe_batch([
            _row(quality_tier="unpublishable",
                 hindsight_actual_quarters=1, hindsight_estimate_quarters=1,
                 methodology_version="v2", ttm_pe_gaap=99.0),
        ])
    got = store.get_basket_weekly_pe_history("SPY")
    assert len(got) == 1
    assert got[0]["methodology_version"] == "v1"
    assert got[0]["ttm_pe_gaap"] == 22.0


# 7. read-only range query 不创建 DB 副作用、不写 last_updated
def test_read_query_is_side_effect_free(store):
    # A totally empty basket must read cleanly off the already-bootstrapped
    # schema without needing any prior write.
    assert store.get_basket_weekly_pe_history("QQQ") == []

    store.upsert_basket_weekly_pe_batch([_row()])
    before = store.get_basket_weekly_pe_history("SPY")[0]["last_updated"]
    # Multiple reads must not mutate last_updated.
    store.get_basket_weekly_pe_history("SPY")
    store.get_basket_weekly_pe_history("SPY", from_date="2026-07-01")
    after = store.get_basket_weekly_pe_history("SPY")[0]["last_updated"]
    assert before == after


# 8. (R5) 同 methodology 下 latest_consensus_tail 升级为 actual_only 成功且留审计痕迹
def test_r5_tail_upgrade_to_actual_only_succeeds_with_audit_trail(store, monkeypatch):
    # Real wall-clock time has only second resolution here; a fixed clock
    # makes the two writes' last_updated deterministically distinguishable
    # instead of depending on the two calls straddling a second boundary.
    ticks = iter([
        dt_module.datetime(2026, 7, 30, 4, 0, 0, tzinfo=dt_module.timezone.utc),
        dt_module.datetime(2026, 7, 30, 4, 5, 0, tzinfo=dt_module.timezone.utc),
    ])

    class _FixedDatetime(dt_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return next(ticks)

    monkeypatch.setattr(market_store, "datetime", _FixedDatetime)

    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="latest_consensus_tail",
             hindsight_actual_quarters=2, hindsight_estimate_quarters=2,
             methodology_version="v1"),
    ])
    first = store.get_basket_weekly_pe_history("SPY")[0]
    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="actual_only",
             hindsight_actual_quarters=4, hindsight_estimate_quarters=0,
             methodology_version="v1"),
    ])
    second = store.get_basket_weekly_pe_history("SPY")[0]
    assert second["quality_tier"] == "actual_only"
    assert second["created_at"] == first["created_at"]
    assert second["last_updated"] != first["last_updated"]


# 9. (R5) quality_tier 降级（actual -> estimate）被拒绝并写 warning
def test_r5_quality_tier_downgrade_rejected_and_warns(store, caplog):
    store.upsert_basket_weekly_pe_batch([
        _row(quality_tier="actual_only",
             hindsight_actual_quarters=4, hindsight_estimate_quarters=0,
             methodology_version="v1"),
    ])
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError):
            store.upsert_basket_weekly_pe_batch([
                _row(quality_tier="latest_consensus_tail",
                     hindsight_actual_quarters=3, hindsight_estimate_quarters=1,
                     methodology_version="v1"),
            ])
    assert any("downgrade" in r.message for r in caplog.records)
    got = store.get_basket_weekly_pe_history("SPY")
    assert got[0]["quality_tier"] == "actual_only"


# 10. 新表已注册表白名单，未注册路径 fail-fast
def test_table_is_whitelisted_and_unknown_table_fails_fast():
    _validate_table("basket_weekly_pe_history")  # must not raise
    with pytest.raises(ValueError):
        _validate_table("basket_weekly_pe_history_typo")


# ---------------------------------------------------------------------------
# Task 4 (R3 / issue048): append-only backfill run manifest
# ---------------------------------------------------------------------------

def _event(run_id="run-1", basket="SPY", event_seq=0, event_kind="run_started",
           **overrides):
    row = {
        "run_id": run_id,
        "basket": basket,
        "event_seq": event_seq,
        "event_kind": event_kind,
        "frequency": "weekly",
        "expected_from_date": "2021-07-30",
        "expected_to_date": "2026-07-30",
        "methodology_version": "1.0",
        "target_count": 3,
        "target_universe_json": ["AAA", "BBB", "CCC"],
        "payload_json": {"stage": "source"},
    }
    row.update(overrides)
    return row


def test_manifest_table_is_registered_and_bootstrapped(store):
    _validate_table("basket_pe_backfill_runs")
    names = {row[0] for row in store._get_conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "basket_pe_backfill_runs" in names


def test_manifest_events_append_and_read_back_in_order(store):
    assert store.append_basket_pe_run_events([
        _event(event_seq=0),
        _event(event_seq=1, event_kind="forced_refresh",
               payload_json={"symbol": "AAA", "pre_row_hash": "a" * 64,
                             "post_row_hash": "b" * 64}),
    ]) == 2
    assert store.append_basket_pe_run_events([
        _event(event_seq=2, event_kind="run_completed",
               payload_json={"weekly_rows": 12})]) == 1
    events = store.get_basket_pe_run_events(basket="SPY")
    assert [row["event_seq"] for row in events] == [0, 1, 2]
    assert events[0]["target_universe_json"] == '["AAA","BBB","CCC"]'
    assert json.loads(events[1]["payload_json"])["symbol"] == "AAA"


def test_manifest_is_append_only_and_rejects_rewriting_an_event(store):
    store.append_basket_pe_run_events([_event(event_seq=0)])
    with pytest.raises(ValueError):
        store.append_basket_pe_run_events([
            _event(event_seq=0, payload_json={"stage": "tampered"})])
    events = store.get_basket_pe_run_events(run_id="run-1")
    assert len(events) == 1
    assert json.loads(events[0]["payload_json"]) == {"stage": "source"}


def test_manifest_batch_is_atomic(store):
    with pytest.raises(ValueError):
        store.append_basket_pe_run_events([
            _event(event_seq=0),
            _event(event_seq=1, event_kind="not_a_kind"),
        ])
    assert store.get_basket_pe_run_events(run_id="run-1") == []


def test_manifest_requires_an_expected_date_range(store):
    with pytest.raises(ValueError):
        store.append_basket_pe_run_events([_event(expected_from_date=None)])
    with pytest.raises(ValueError):
        store.append_basket_pe_run_events([
            _event(expected_from_date="2026-07-30",
                   expected_to_date="2021-07-30")])


def test_manifest_reads_are_scoped_by_basket_and_run(store):
    store.append_basket_pe_run_events([
        _event(basket="SPY", event_seq=0),
        _event(basket="QQQ", event_seq=0),
        _event(run_id="run-2", basket="SPY", event_seq=0),
    ])
    assert len(store.get_basket_pe_run_events(basket="SPY")) == 2
    assert len(store.get_basket_pe_run_events(run_id="run-2")) == 1
    assert len(store.get_basket_pe_run_events(
        basket="SPY", run_id="run-1")) == 1


# ---------------------------------------------------------------------------
# Fix round 2 / F2: rows carry the run that wrote them
# ---------------------------------------------------------------------------

def test_row_records_the_run_that_wrote_it(store):
    store.upsert_basket_weekly_pe_batch([_row(run_id="run-1")])
    stored = store.get_basket_weekly_pe_history("SPY")
    assert stored[0]["run_id"] == "run-1"


def test_a_row_without_a_run_id_is_rejected(store):
    row = _row()
    row.pop("run_id", None)
    with pytest.raises(ValueError, match="run_id"):
        store.upsert_basket_weekly_pe_batch([row])


def test_r5_tail_upgrade_still_works_and_rebinds_the_run(store):
    """A later run re-computing a tail point is the R5 upgrade path.

    The row's ownership moves to the run that recomputed it -- that run is the
    one now accountable for the value.
    """
    store.upsert_basket_weekly_pe_batch([_row(
        run_id="run-1", quality_tier="latest_consensus_tail",
        hindsight_actual_quarters=2, hindsight_estimate_quarters=2)])
    store.upsert_basket_weekly_pe_batch([_row(
        run_id="run-2", quality_tier="actual_only",
        hindsight_actual_quarters=4, hindsight_estimate_quarters=0)])
    stored = store.get_basket_weekly_pe_history("SPY")
    assert len(stored) == 1
    assert stored[0]["quality_tier"] == "actual_only"
    assert stored[0]["run_id"] == "run-2"


# ---------------------------------------------------------------------------
# Boss review 2 / P1-A: the pre-run_id table must be migrated, not ignored
# ---------------------------------------------------------------------------

_PRE_RUN_ID_DDL = """CREATE TABLE basket_weekly_pe_history (
    basket TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    ttm_pe_gaap REAL,
    hindsight_ntm_pe_gaap REAL,
    ttm_total_mcap REAL,
    ttm_net_income REAL,
    hindsight_total_mcap REAL,
    hindsight_ntm_net_income REAL,
    n_members INTEGER NOT NULL,
    n_covered_ttm INTEGER NOT NULL,
    n_covered_hindsight INTEGER NOT NULL,
    mcap_coverage_ttm REAL NOT NULL,
    mcap_coverage_hindsight REAL NOT NULL,
    hindsight_actual_quarters INTEGER NOT NULL,
    hindsight_estimate_quarters INTEGER NOT NULL,
    composition_effective_date TEXT NOT NULL,
    composition_available_date TEXT NOT NULL,
    quality_tier TEXT NOT NULL,
    members_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (basket, valuation_date)
);"""


def _legacy_db(tmp_path, rows=0):
    """A database carrying the shipped pre-run_id table, as production does."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    with conn:
        conn.executescript(_PRE_RUN_ID_DDL)
        for index in range(rows):
            conn.execute(
                "INSERT INTO basket_weekly_pe_history VALUES "
                "('SPY', ?, 20.0, 18.0, 1.0, 1.0, 1.0, 1.0, 1, 1, 1, 1.0, 1.0,"
                " 4, 0, '2021-01-18', '2021-01-25', 'actual_only', '{}', '[]',"
                " '1.0', 'x', 'x')", [f"2026-01-{index + 1:02d}"])
    conn.close()
    return path


def test_an_empty_legacy_table_is_migrated_to_carry_run_id(tmp_path, monkeypatch):
    """CREATE TABLE IF NOT EXISTS does not upgrade a table that already exists.

    Production's table predates run_id and is empty. Left alone, the first
    cloud write would silently drop the column -- `_insert_validated` filters
    to the columns the table actually has -- and every row would land
    unattributable, defeating row-level certification on its first use.
    """
    path = _legacy_db(tmp_path)
    from src.data.market_store import _TABLE_COLUMNS
    with sqlite3.connect(path) as conn:
        legacy_columns = [row[1] for row in conn.execute(
            "PRAGMA table_info(basket_weekly_pe_history)")]
    monkeypatch.setitem(_TABLE_COLUMNS, "basket_weekly_pe_history", legacy_columns)
    store = MarketStore(path)
    try:
        columns = {row[1]: row for row in store._get_conn().execute(
            "PRAGMA table_info(basket_weekly_pe_history)")}
        assert "run_id" in columns
        assert columns["run_id"][3] == 1  # NOT NULL
        store.upsert_basket_weekly_pe_batch([_row(run_id="run-1")])
        assert store.get_basket_weekly_pe_history("SPY")[0]["run_id"] == "run-1"
    finally:
        store.close()


def test_populated_legacy_table_blocks_only_weekly_pe_writers(tmp_path):
    """Preserve ownerless rows without taking unrelated data writers offline."""
    path = _legacy_db(tmp_path, rows=2)
    store = MarketStore(path)
    try:
        before = store.get_basket_weekly_pe_history("SPY")
        store.upsert_daily_prices("AAPL", [{"date": "2026-09-11", "close": 100.0}])
        store.upsert_fmp_estimates("AAPL", [{
            "snapshot_date": "2026-09-11", "fiscal_date": "2026-12-31",
            "period_type": "Q", "snapshot_kind": "weekly", "eps_avg": 1.0,
        }])
        assert store.get_daily_prices("AAPL")[0]["close"] == 100.0
        assert store.get_fmp_estimates("AAPL", snapshot_date="2026-09-11")
        with pytest.raises(RuntimeError, match="run_id"):
            store.upsert_basket_weekly_pe_batch([_row()])
        rows = [_row(run_id="new")]
        event = _window_events(store, rows)
        with pytest.raises(RuntimeError, match="run_id"):
            store.commit_basket_weekly_pe_window(rows, event, lambda conn: True)
        assert store.get_basket_weekly_pe_history("SPY") == before
        assert not store._get_conn().in_transaction
    finally:
        store.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM basket_weekly_pe_history").fetchone()[0] == 2
        assert "run_id" not in {row[1] for row in conn.execute(
            "PRAGMA table_info(basket_weekly_pe_history)")}


def _window_events(store, rows, run_id="new", start="2021-07-10",
                   end="2026-07-10", expected=None):
    base = {
        "run_id": run_id, "basket": "SPY", "frequency": "weekly",
        "expected_from_date": start, "expected_to_date": end,
        "methodology_version": "v1", "target_count": 1,
        "target_universe_json": ["AAPL"],
    }
    store.append_basket_pe_run_events([{
        **base, "event_seq": 0, "event_kind": "run_started",
        "payload_json": {"expected_weeks": expected or [
            row["valuation_date"] for row in rows]},
    }])
    return {**base, "event_seq": 1, "event_kind": "run_completed",
            "payload_json": {"weekly_rows": len(rows)}}


def test_window_commit_prunes_old_rows_and_keeps_other_baskets(store):
    store.upsert_basket_weekly_pe_batch([
        _row(valuation_date="2021-07-09"),
        _row(valuation_date="2026-07-03"),
        _row(basket="QQQ", valuation_date="2021-07-09"),
    ])
    rows = [_row(valuation_date=day, run_id="new")
            for day in ("2026-07-03", "2026-07-10")]
    event = _window_events(store, rows)
    def certify(conn):
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM basket_weekly_pe_history "
                            "WHERE basket = 'SPY'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM basket_pe_backfill_runs "
                            "WHERE event_kind = 'run_completed'").fetchone()[0] == 1
        with sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True) as reader:
            assert reader.execute("SELECT COUNT(*) FROM basket_weekly_pe_history "
                                  "WHERE run_id = 'new'").fetchone()[0] == 0
            assert reader.execute("SELECT COUNT(*) FROM basket_pe_backfill_runs "
                                  "WHERE event_kind = 'run_completed'").fetchone()[0] == 0
        return True
    assert store.commit_basket_weekly_pe_window(rows, event, certify) == 2
    assert len(store.get_basket_weekly_pe_history("QQQ")) == 1
    assert store._get_conn().execute("PRAGMA query_only").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["reject", "raise", "terminal", "prune"])
def test_window_failure_rolls_back_pruning_rows_and_completion(store, failure):
    store.upsert_basket_weekly_pe_batch([
        _row(valuation_date="2021-07-09"), _row(valuation_date="2026-07-03")])
    before = store.get_basket_weekly_pe_history("SPY")
    rows = [_row(valuation_date=day, run_id="new")
            for day in ("2026-07-03", "2026-07-10")]
    event = _window_events(store, rows)
    if failure == "terminal":
        store._get_conn().execute(
            "CREATE TRIGGER reject_completed BEFORE INSERT ON basket_pe_backfill_runs "
            "WHEN NEW.event_kind = 'run_completed' BEGIN "
            "SELECT RAISE(ABORT, 'injected terminal failure'); END")
    if failure == "prune":
        store._get_conn().execute(
            "CREATE TRIGGER reject_prune BEFORE DELETE ON basket_weekly_pe_history "
            "BEGIN SELECT RAISE(ABORT, 'injected prune failure'); END")
    def certify(conn):
        if failure == "raise":
            raise RuntimeError("injected verifier failure")
        return failure == "terminal"
    with pytest.raises((RuntimeError, ValueError, sqlite3.IntegrityError)):
        store.commit_basket_weekly_pe_window(rows, event, certify)
    assert store.get_basket_weekly_pe_history("SPY") == before
    assert [e["event_kind"] for e in store.get_basket_pe_run_events()] == ["run_started"]
    assert store._get_conn().execute("PRAGMA query_only").fetchone()[0] == 0


def test_window_rejects_missing_week_before_pruning(store):
    rows = [_row(run_id="new")]
    event = _window_events(store, rows, expected=["2026-07-03", "2026-07-10"])
    with pytest.raises(ValueError, match="week"):
        store.commit_basket_weekly_pe_window(rows, event, lambda conn: True)
    assert store.get_basket_weekly_pe_history("SPY") == []


def test_window_rejects_downgrade_even_when_sample_day_changes(store):
    store.upsert_basket_weekly_pe_batch([_row(valuation_date="2026-07-10")])
    rows = [_row(valuation_date="2026-07-09", run_id="new",
                 quality_tier="latest_consensus_tail",
                 hindsight_actual_quarters=3, hindsight_estimate_quarters=1)]
    event = _window_events(store, rows, expected=["2026-07-10"])
    with pytest.raises(ValueError, match="downgrade"):
        store.commit_basket_weekly_pe_window(rows, event, lambda conn: True)
    assert store.get_basket_weekly_pe_history("SPY")[0]["quality_tier"] == "actual_only"


def test_window_replaces_old_sample_day_in_the_same_week(store):
    store.upsert_basket_weekly_pe_batch([_row(valuation_date="2026-07-10")])
    rows = [_row(valuation_date="2026-07-09", run_id="new")]
    event = _window_events(store, rows, expected=["2026-07-10"])
    store.commit_basket_weekly_pe_window(rows, event, lambda conn: True)
    assert [r["valuation_date"] for r in store.get_basket_weekly_pe_history("SPY")] == ["2026-07-09"]


@pytest.mark.parametrize("bad", ["empty", "basket", "owner", "range", "version"])
def test_window_rejects_invalid_batch_without_removing_existing_rows(store, bad):
    store.upsert_basket_weekly_pe_batch([_row()])
    before = store.get_basket_weekly_pe_history("SPY")
    rows = [_row(run_id="new")]
    event = _window_events(store, rows)
    if bad == "empty":
        rows = []
    else:
        field, value = {"basket": ("basket", "QQQ"), "owner": ("run_id", "wrong"),
                        "range": ("valuation_date", "2026-07-17"),
                        "version": ("methodology_version", "v2")}[bad]
        rows[0][field] = value
    with pytest.raises(ValueError):
        store.commit_basket_weekly_pe_window(rows, event, lambda conn: True)
    assert store.get_basket_weekly_pe_history("SPY") == before


def test_retained_window_cannot_rewind_over_a_newer_completed_run(store):
    rows = [_row(run_id="first")]
    event = _window_events(store, rows, run_id="first", end="2026-07-17")
    store.commit_basket_weekly_pe_window(rows, event, lambda conn: True)
    before = store.get_basket_weekly_pe_history("SPY")
    new_rows = [_row(run_id="new")]
    event = _window_events(store, new_rows, end="2026-07-10")
    with pytest.raises(ValueError, match="backwards"):
        store.commit_basket_weekly_pe_window(new_rows, event, lambda conn: True)
    assert store.get_basket_weekly_pe_history("SPY") == before
