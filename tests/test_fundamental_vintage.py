"""Tests for MarketStore fundamental vintage: change-only append write +
strict/approximate read interfaces (T5, R7/R8)."""
import pytest

from src.data.market_store import MarketStore


@pytest.fixture
def tmp_store(tmp_path):
    """Create a fresh MarketStore backed by a temp DB."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


def _seed_income_current(store, symbol, fiscal, accepted, revenue):
    """走真实 upsert_income 落 income_quarterly（current 表），使 approximate_as_reported
    测试读到的是货真价实的 current-table 内容而非直插桩数据。"""
    store.upsert_income(symbol, [{
        "date": fiscal,
        "acceptedDate": accepted,
        "revenue": revenue,
    }])


def test_change_only_append(tmp_store):
    row = {"date": "2026-06-30", "revenue": 100, "acceptedDate": "2026-08-01 16:00:00"}
    assert tmp_store.record_vintage("AAPL", "income", [row], "2026-08-24T10:00:00Z", "latest_known") == 1
    assert tmp_store.record_vintage("AAPL", "income", [row], "2026-08-31T10:00:00Z", "latest_known") == 0


def test_record_vintage_rejects_pure_date_observed_at(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 1}],
                                 "2026-08-24", "latest_known")     # R3-m2：写入侧强制 timestamp


def test_restatement_two_versions_coexist(tmp_store):
    tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 100}], "2026-08-24T10:00:00Z", "latest_known")
    tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 95}], "2026-09-07T10:00:00Z", "revised")
    assert tmp_store.known_as_of("AAPL", "income", "2026-08-30")[0]["revenue"] == 100   # 纯日期→当日 23:59:59.999999Z
    new = tmp_store.known_as_of("AAPL", "income", "2026-09-08")[0]
    assert new["revenue"] == 95 and new["_vintage_quality"] == "revised"


def test_pure_date_asof_includes_same_day_observation(tmp_store):
    # R3-m2 边界：as_of="2026-08-24" 必须包含当日 10:00 的观测（规范化到当日末尾）
    tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 100}], "2026-08-24T10:00:00Z", "latest_known")
    assert tmp_store.known_as_of("AAPL", "income", "2026-08-24")[0]["revenue"] == 100


def test_known_as_of_before_golive_returns_empty_not_fallback(tmp_store):
    tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 100}], "2026-08-24T10:00:00Z", "latest_known")
    assert tmp_store.known_as_of("AAPL", "income", "2026-08-01") == []


def test_same_day_double_revision_both_stored(tmp_store):
    # R2-P2-2：同一天两版不同值 → 两行并存（timestamp 主键不撞）
    tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 100}],
                             "2026-09-07T10:00:00Z", "revised")
    n = tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 96}],
                                 "2026-09-07T18:30:00Z", "revised")
    assert n == 1
    assert tmp_store.known_as_of("AAPL", "income", "2026-09-07T12:00:00Z")[0]["revenue"] == 100
    assert tmp_store.known_as_of("AAPL", "income", "2026-09-08")[0]["revenue"] == 96


def test_approximate_reads_current_and_is_tagged(tmp_store):
    _seed_income_current(tmp_store, "AMAT", fiscal="2026-06-30",
                         accepted="2026-08-13 16:03:36", revenue=7)   # helper: 走现有 upsert_income
    out = tmp_store.approximate_as_reported("AMAT", "income", "2026-08-20")
    assert out["approximate"] is True and out["rows"][0]["revenue"] == 7
    assert tmp_store.approximate_as_reported("AMAT", "income", "2026-08-01")["rows"] == []


def test_batch_duplicate_fiscal_date_rejected_atomically(tmp_store):
    # Fix-round-1 Finding 1: two rows in the SAME call mapping to the same
    # (symbol, statement, fiscal_date, observed_at) PK must be rejected
    # BEFORE any write — whole batch atomically, not silently resolved by
    # letting the second row clobber the first via INSERT OR REPLACE.
    rows = [
        {"date": "2026-06-30", "revenue": 100},
        {"date": "2026-06-30", "revenue": 200},
    ]
    with pytest.raises(ValueError):
        tmp_store.record_vintage("AAPL", "income", rows, "2026-08-24T10:00:00Z", "latest_known")
    assert tmp_store.known_as_of("AAPL", "income", "2026-08-24") == []


def test_pure_date_asof_boundary_includes_exact_end_of_day_timestamp(tmp_store):
    # Fix-round-1 Finding 2: a vintage observed at exactly "<date>T23:59:59Z"
    # (no fractional seconds) must still be included under a pure-date as_of.
    # The naive inclusive comparison against "<date>T23:59:59.999999Z"
    # excluded it (lexicographically "Z" > "."); the fix compares with an
    # exclusive next-day bound instead.
    tmp_store.record_vintage("AAPL", "income", [{"date": "2026-06-30", "revenue": 100}],
                             "2026-08-24T23:59:59Z", "latest_known")
    assert tmp_store.known_as_of("AAPL", "income", "2026-08-24")[0]["revenue"] == 100


def test_batch_identical_duplicate_rows_deduped_not_rejected(tmp_store):
    # Fix-round-2 Finding 1: two BYTE-IDENTICAL rows for the same fiscal_date
    # in one batch (idempotent upstream retry / paginated-response overlap)
    # must NOT hard-fail the whole batch — they fall through to the existing
    # change-only hash-skip and get deduped, counted once. Only a fiscal_date
    # collision with DIFFERING content (see
    # test_batch_duplicate_fiscal_date_rejected_atomically) is an error.
    row = {"date": "2026-06-30", "revenue": 100}
    n = tmp_store.record_vintage("AAPL", "income", [row, dict(row)],
                                 "2026-08-24T10:00:00Z", "latest_known")
    assert n == 1
    assert tmp_store.known_as_of("AAPL", "income", "2026-08-24")[0]["revenue"] == 100
