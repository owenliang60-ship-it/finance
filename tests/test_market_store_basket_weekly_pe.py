"""Storage contract for basket_weekly_pe_history (plan §3.3, Task 2).

Covers: DDL/index bootstrap, atomic whole-batch upsert, CHECK/fail-fast
validation, methodology_version silent-overwrite protection, the R5 tail
evolution contract (estimate -> actual upgrade legal / downgrade rejected),
read-only query side-effect freedom, and table-whitelist registration.
"""
import datetime as dt_module
import logging
import sqlite3

import pytest

from src.data import market_store
from src.data.market_store import MarketStore, _validate_table

EXPECTED_TABLES = {"basket_weekly_pe_history"}


@pytest.fixture
def store(tmp_path):
    value = MarketStore(tmp_path / "market.db")
    yield value
    value.close()


def _row(basket="SPY", valuation_date="2026-07-10", methodology_version="v1",
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
