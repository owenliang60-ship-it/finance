"""Tests for MarketStore's fundamental backfill manifest — dataset-granular
(run_id, symbol, dataset) job ledger that the T10 runner drives (T9, R6)."""
import pytest

from src.data.market_store import MarketStore


@pytest.fixture
def tmp_store(tmp_path):
    """Create a fresh MarketStore backed by a temp DB."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


# ---- Brief's mandatory tests ----

def test_create_freezes_symbol_dataset_grid(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL", "MSFT"], ["income", "balance"], {})
    assert tmp_store.run_progress("r1")["pending"] == 4


def test_create_empty_symbols_raises(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.create_backfill_run("r1", [], ["income"], {})


def test_rerun_with_different_universe_rejected(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL"], ["income"], {})
    with pytest.raises(ValueError):
        tmp_store.create_backfill_run("r1", ["MSFT"], ["income"], {})


def test_resume_skips_terminal_and_retries_failed_under_cap(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL"], ["income", "balance", "cashflow"], {})
    with tmp_store.transaction() as conn:
        tmp_store.complete_job_in_conn(conn, "r1", "AAPL", "income", "done")
        tmp_store.complete_job_in_conn(conn, "r1", "AAPL", "balance", "provider_empty")
        tmp_store.complete_job_in_conn(conn, "r1", "AAPL", "cashflow", "fetch_failed", error="502")
    assert tmp_store.claim_pending_jobs("r1", "AAPL") == ["cashflow"]   # 终态不重入，失败可重试


# ---- Additional coverage: CONTROLLER RULING #2 + idempotent resume ----

def test_run_progress_unknown_run_id_raises(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.run_progress("does-not-exist")


def test_get_backfill_run_unknown_run_id_raises(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.get_backfill_run("does-not-exist")


def test_rerun_with_same_universe_is_idempotent(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL", "MSFT"], ["income"], {"x": 1})
    with tmp_store.transaction() as conn:
        tmp_store.complete_job_in_conn(conn, "r1", "AAPL", "income", "done")
    # Re-create with the same symbols (order/case-insensitive) must be a no-op:
    # it must not raise and must not reset AAPL's already-completed job.
    tmp_store.create_backfill_run("r1", ["msft", "aapl"], ["income"], {"x": 1})
    prog = tmp_store.run_progress("r1")
    assert prog["done"] == 1
    assert prog["pending"] == 1
    assert prog["total_jobs"] == 2


# ---- Terminal-state cap, claim/in_progress, reset, run header lifecycle ----

def test_fetch_failed_becomes_terminal_after_max_attempts(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL"], ["income"], {})
    for _ in range(3):
        claimed = tmp_store.claim_pending_jobs("r1", "AAPL")
        assert claimed == ["income"]
        with tmp_store.transaction() as conn:
            tmp_store.complete_job_in_conn(conn, "r1", "AAPL", "income", "fetch_failed", error="502")
    # attempts now == 3 -> terminal, no longer reclaimable
    assert tmp_store.claim_pending_jobs("r1", "AAPL") == []
    prog = tmp_store.run_progress("r1")
    assert prog["fetch_failed"] == 1
    assert prog["is_complete"] is True


def test_claim_marks_in_progress_and_reset_returns_to_pending(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL"], ["income", "balance"], {})
    claimed = tmp_store.claim_pending_jobs("r1", "AAPL")
    assert sorted(claimed) == ["balance", "income"]
    prog = tmp_store.run_progress("r1")
    assert prog["in_progress"] == 2
    assert prog["pending"] == 0

    reset_count = tmp_store.reset_in_progress_jobs("r1")
    assert reset_count == 2
    prog = tmp_store.run_progress("r1")
    assert prog["in_progress"] == 0
    assert prog["pending"] == 2


def test_run_progress_is_complete_false_while_pending(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL"], ["income", "balance"], {})
    with tmp_store.transaction() as conn:
        tmp_store.complete_job_in_conn(conn, "r1", "AAPL", "income", "done")
    prog = tmp_store.run_progress("r1")
    assert prog["is_complete"] is False
    assert prog["total_symbols"] == 1
    assert prog["total_jobs"] == 2


def test_finish_run_and_get_backfill_run_header(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL"], ["income"], {"limit_quarters": 8})
    header = tmp_store.get_backfill_run("r1")
    assert header["status"] == "running"
    assert header["finished_at"] is None
    assert header["run_id"] == "r1"
    assert len(header["universe_hash"]) == 64   # sha256 hex digest

    tmp_store.finish_run("r1", "complete")
    header = tmp_store.get_backfill_run("r1")
    assert header["status"] == "complete"
    assert header["finished_at"] is not None


def test_finish_run_invalid_status_raises(tmp_store):
    tmp_store.create_backfill_run("r1", ["AAPL"], ["income"], {})
    with pytest.raises(ValueError):
        tmp_store.finish_run("r1", "not-a-real-status")


def test_finish_run_unknown_run_id_raises(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.finish_run("does-not-exist", "complete")
