"""Read-only fact pack over prosperity's public consensus functions (Task 2)."""
import hashlib

import pytest

from src.data.market_store import MarketStore
from terminal.valuation.facts import load_facts

AS_OF = "2026-11-22"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _facts(path, as_of=AS_OF):
    store = MarketStore(db_path=path, read_only=True)
    try:
        return load_facts(store, "ACME", as_of)
    finally:
        store.close()


def test_writable_store_is_rejected(valuation_db):
    db, _ = valuation_db
    store = MarketStore(db_path=db)
    try:
        with pytest.raises(ValueError, match="read_only=True"):
            load_facts(store, "ACME", AS_OF)
    finally:
        store.close()


def test_acme_fact_pack(valuation_db):
    db, _ = valuation_db
    before = _sha(db)
    facts = _facts(db)
    assert _sha(db) == before
    assert facts.symbol == "ACME" and facts.as_of == AS_OF
    assert facts.reporting_currency == "USD" and facts.is_adr is False
    assert facts.price == 80.0 and facts.price_date == "2026-11-20"
    assert facts.ttm_eps_usd == 8.0
    assert facts.ttm_quarters == ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")
    assert facts.shares == 100e6
    assert facts.ntm.value == 10.0
    assert facts.ntm.basis == "quarter_sum"
    assert facts.ntm_weights == (1.0, 1.0, 1.0, 1.0)
    assert facts.ntm_sums == {"eps_low": 8.0, "eps_high": 12.0, "rev_avg": 6400e6,
                              "rev_low": 6000e6, "rev_high": 7200e6}
    assert facts.next_q["fiscal_date"][:10] == "2026-12-31"
    assert facts.upcoming["eps_estimated"] == 2.5
    assert facts.yf_0q["eps_avg"] == 2.5
    assert facts.fy_pair[0]["fiscal_date"][:10] == "2026-12-31"
    assert facts.fy_pair[1]["fiscal_date"][:10] == "2027-12-31"
    assert len(facts.eps) == 12
    assert all(q.statement_fiscal for q in facts.eps)
    assert [r["date"] for r in facts.income] == sorted(r["date"] for r in facts.income)
    assert facts.closes[0] == ("2025-06-02", 80.0) and facts.closes[-1] == ("2026-11-20", 80.0)
    assert facts.price_jump_notes == ()


def test_thin_far_quarters_fall_back_to_fy_blend(valuation_db):
    _, seed = valuation_db
    facts = _facts(seed(thin=True))
    assert facts.ntm.basis == "ntm_fy_blend"
    assert facts.ntm_weights == (0.25, 0.75)
    assert facts.ntm_sums["rev_avg"] == 0.25 * 6100e6 + 0.75 * 6600e6
    assert facts.next_q["fiscal_date"][:10] == "2026-12-31"


def test_stale_snapshot_has_no_ntm(valuation_db):
    _, seed = valuation_db
    facts = _facts(seed(snapshot="2026-10-01"))
    assert facts.ntm.value is None
    assert facts.ntm.missing_reason == "snapshot_stale"
    assert facts.ntm_weights == ()
    assert facts.ntm_sums == {k: None for k in ("eps_low", "eps_high", "rev_avg", "rev_low", "rev_high")}


def test_stale_price_is_none_but_date_kept(valuation_db):
    _, seed = valuation_db

    def edit(rows):
        rows["prices"] = [p for p in rows["prices"] if p["date"] <= "2026-11-12"]

    facts = _facts(seed(edit=edit))
    assert facts.price is None
    assert facts.price_date == "2026-11-12"


def test_stale_yfinance_row_is_dropped(valuation_db):
    _, seed = valuation_db

    def edit(rows):
        rows["forward"] = [{"date": "2026-11-01", "period": "0q", "eps_avg": 2.5}]

    assert _facts(seed(edit=edit)).yf_0q is None
