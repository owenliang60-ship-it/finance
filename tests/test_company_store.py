"""Tests for terminal.company_store — SQLite backend."""
import json
import pytest
from pathlib import Path

from terminal.company_store import CompanyStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh CompanyStore in a temp directory."""
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

class TestCompanies:
    def test_upsert_and_get(self, store):
        store.upsert_company("AAPL", company_name="Apple Inc.", sector="Technology")
        c = store.get_company("AAPL")
        assert c is not None
        assert c["symbol"] == "AAPL"
        assert c["company_name"] == "Apple Inc."
        assert c["sector"] == "Technology"

    def test_upsert_preserves_existing(self, store):
        """Empty fields in update should not overwrite existing data."""
        store.upsert_company("AAPL", company_name="Apple Inc.", sector="Technology")
        store.upsert_company("AAPL", exchange="NASDAQ")
        c = store.get_company("AAPL")
        assert c["company_name"] == "Apple Inc."  # preserved
        assert c["exchange"] == "NASDAQ"  # updated

    def test_upsert_case_insensitive(self, store):
        store.upsert_company("aapl", company_name="Apple")
        c = store.get_company("AAPL")
        assert c is not None
        assert c["symbol"] == "AAPL"

    def test_get_nonexistent(self, store):
        assert store.get_company("FAKE") is None

    def test_list_companies(self, store):
        store.upsert_company("AAPL", company_name="Apple")
        store.upsert_company("MSFT", company_name="Microsoft")
        store.upsert_company("GOOG", company_name="Google")
        companies = store.list_companies()
        assert len(companies) == 3
        symbols = [c["symbol"] for c in companies]
        assert symbols == ["AAPL", "GOOG", "MSFT"]  # sorted

    def test_list_in_pool_only(self, store):
        store.upsert_company("AAPL", in_pool=True)
        store.upsert_company("MSFT", in_pool=False)
        pool = store.list_companies(in_pool_only=True)
        assert len(pool) == 1
        assert pool[0]["symbol"] == "AAPL"

    def test_list_has_oprms_only(self, store):
        store.upsert_company("AAPL")
        store.upsert_company("MSFT")
        store.save_oprms_rating("MSFT", dna="S", timing="A", timing_coeff=0.9)
        rated = store.list_companies(has_oprms_only=True)
        assert len(rated) == 1
        assert rated[0]["symbol"] == "MSFT"

    def test_sync_pool(self, store):
        store.upsert_company("AAPL", in_pool=True)
        store.upsert_company("MSFT", in_pool=True)
        # Sync with new pool — GOOG in, MSFT out
        count = store.sync_pool(["AAPL", "GOOG"])
        assert count == 2
        aapl = store.get_company("AAPL")
        assert aapl["in_pool"] == 1
        msft = store.get_company("MSFT")
        assert msft["in_pool"] == 0
        goog = store.get_company("GOOG")
        assert goog is not None
        assert goog["in_pool"] == 1


# ---------------------------------------------------------------------------
# OPRMS Ratings
# ---------------------------------------------------------------------------

class TestOPRMS:
    def test_save_and_get_current(self, store):
        store.upsert_company("AAPL")
        rid = store.save_oprms_rating(
            "AAPL", dna="S", timing="A", timing_coeff=0.9,
            evidence=["Strong moat", "Growing TAM"],
            investment_bucket="Long-term Compounder",
            verdict="BUY",
            position_pct=13.5,
        )
        assert rid > 0
        oprms = store.get_current_oprms("AAPL")
        assert oprms is not None
        assert oprms["dna"] == "S"
        assert oprms["timing"] == "A"
        assert oprms["timing_coeff"] == 0.9
        assert oprms["evidence"] == ["Strong moat", "Growing TAM"]
        assert oprms["investment_bucket"] == "Long-term Compounder"
        assert oprms["verdict"] == "BUY"
        assert oprms["position_pct"] == 13.5
        assert oprms["is_current"] == 1

    def test_new_rating_replaces_current(self, store):
        store.upsert_company("AAPL")
        store.save_oprms_rating("AAPL", dna="A", timing="B", timing_coeff=0.5)
        store.save_oprms_rating("AAPL", dna="S", timing="A", timing_coeff=0.9)
        current = store.get_current_oprms("AAPL")
        assert current["dna"] == "S"  # newer
        history = store.get_oprms_history("AAPL")
        assert len(history) == 2
        assert history[0]["dna"] == "S"  # newest first
        assert history[1]["dna"] == "A"

    def test_get_no_oprms(self, store):
        store.upsert_company("AAPL")
        assert store.get_current_oprms("AAPL") is None

    def test_conviction_modifier(self, store):
        store.upsert_company("AAPL")
        store.save_oprms_rating(
            "AAPL", dna="A", timing="B", timing_coeff=0.5,
            conviction_modifier=1.2,
        )
        oprms = store.get_current_oprms("AAPL")
        assert oprms["conviction_modifier"] == 1.2


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------

class TestAnalyses:
    def test_save_and_get_latest(self, store):
        store.upsert_company("AAPL")
        aid = store.save_analysis("AAPL", {
            "analysis_date": "2026-02-13",
            "depth": "deep",
            "debate_verdict": "BUY — 高信心",
            "executive_summary": "Apple is well positioned...",
            "key_forces": ["AI integration", "Services growth"],
            "oprms_dna": "S",
            "oprms_timing": "A",
            "oprms_timing_coeff": 0.9,
            "oprms_position_pct": 13.5,
            "price_at_analysis": 245.50,
            "regime_at_analysis": "RISK_ON",
        })
        assert aid > 0
        analysis = store.get_latest_analysis("AAPL")
        assert analysis is not None
        assert analysis["debate_verdict"] == "BUY — 高信心"
        assert analysis["key_forces"] == ["AI integration", "Services growth"]
        assert analysis["oprms_dna"] == "S"
        assert analysis["price_at_analysis"] == 245.50

    def test_multiple_analyses(self, store):
        store.upsert_company("AAPL")
        store.save_analysis("AAPL", {"analysis_date": "2026-02-10"})
        store.save_analysis("AAPL", {"analysis_date": "2026-02-13"})
        analyses = store.get_analyses("AAPL")
        assert len(analyses) == 2
        # Newest first
        assert analyses[0]["analysis_date"] == "2026-02-13"

    def test_get_no_analysis(self, store):
        store.upsert_company("AAPL")
        assert store.get_latest_analysis("AAPL") is None

    def test_lens_fields(self, store):
        store.upsert_company("AAPL")
        store.save_analysis("AAPL", {
            "lens_quality_compounder": '{"stars": "4.5", "verdict": "BUY"}',
            "lens_deep_value": '{"stars": "3.0", "verdict": "HOLD"}',
        })
        analysis = store.get_latest_analysis("AAPL")
        assert "4.5" in analysis["lens_quality_compounder"]
        assert "HOLD" in analysis["lens_deep_value"]

    def test_debate_fields_roundtrip(self, store):
        store.upsert_company("AAPL")
        store.save_analysis("AAPL", {
            "analysis_date": "2026-02-20",
            "debate_conviction_modifier": 1.15,
            "debate_final_action": "执行",
            "debate_key_disagreement": "周期 vs 成长",
        })
        analysis = store.get_latest_analysis("AAPL")
        assert analysis["debate_conviction_modifier"] == 1.15
        assert analysis["debate_final_action"] == "执行"
        assert analysis["debate_key_disagreement"] == "周期 vs 成长"

    def test_debate_fields_default_null(self, store):
        store.upsert_company("AAPL")
        store.save_analysis("AAPL", {"analysis_date": "2026-02-20"})
        analysis = store.get_latest_analysis("AAPL")
        assert analysis["debate_conviction_modifier"] is None
        assert analysis["debate_final_action"] is None
        assert analysis["debate_key_disagreement"] is None

    def test_analyses_limit(self, store):
        store.upsert_company("AAPL")
        for i in range(5):
            store.save_analysis("AAPL", {"analysis_date": f"2026-02-{10+i}"})
        limited = store.get_analyses("AAPL", limit=3)
        assert len(limited) == 3


# ---------------------------------------------------------------------------
# Kill Conditions
# ---------------------------------------------------------------------------

class TestKillConditions:
    def test_save_and_get(self, store):
        store.upsert_company("AAPL")
        count = store.save_kill_conditions("AAPL", [
            {"description": "Revenue growth < 5% for 2 quarters"},
            {"description": "CEO departure", "source_lens": "event_driven"},
        ])
        assert count == 2
        kc = store.get_kill_conditions("AAPL")
        assert len(kc) == 2
        descriptions = {k["description"] for k in kc}
        assert "Revenue growth < 5% for 2 quarters" in descriptions
        assert "CEO departure" in descriptions

    def test_replace_conditions(self, store):
        store.upsert_company("AAPL")
        store.save_kill_conditions("AAPL", [
            {"description": "Old condition"},
        ])
        store.save_kill_conditions("AAPL", [
            {"description": "New condition 1"},
            {"description": "New condition 2"},
        ])
        kc = store.get_kill_conditions("AAPL")
        assert len(kc) == 2
        descriptions = [k["description"] for k in kc]
        assert "Old condition" not in descriptions

    def test_include_inactive(self, store):
        store.upsert_company("AAPL")
        store.save_kill_conditions("AAPL", [{"description": "Old"}])
        store.save_kill_conditions("AAPL", [{"description": "New"}])
        all_kc = store.get_kill_conditions("AAPL", active_only=False)
        assert len(all_kc) == 2


# ---------------------------------------------------------------------------
# Dashboard + Stats
# ---------------------------------------------------------------------------

class TestDashboardAndStats:
    def test_dashboard_data(self, store):
        store.upsert_company("AAPL", company_name="Apple", sector="Tech")
        store.upsert_company("MSFT", company_name="Microsoft", sector="Tech")
        store.save_oprms_rating("AAPL", dna="S", timing="A", timing_coeff=0.9,
                                verdict="BUY", position_pct=13.5)
        store.save_analysis("AAPL", {
            "analysis_date": "2026-02-13",
            "debate_verdict": "BUY",
            "executive_summary": "Strong buy",
        })

        data = store.get_dashboard_data()
        assert len(data) == 2
        aapl = next(d for d in data if d["symbol"] == "AAPL")
        assert aapl["dna"] == "S"
        assert aapl["analysis_date"] == "2026-02-13"
        msft = next(d for d in data if d["symbol"] == "MSFT")
        assert msft["dna"] is None  # no OPRMS

    def test_stats(self, store):
        store.upsert_company("AAPL", in_pool=True)
        store.upsert_company("MSFT", in_pool=True)
        store.upsert_company("GOOG")
        store.save_oprms_rating("AAPL", dna="S", timing="A", timing_coeff=0.9)
        store.save_oprms_rating("MSFT", dna="A", timing="B", timing_coeff=0.5)
        store.save_analysis("AAPL", {"analysis_date": "2026-02-13"})

        stats = store.get_stats()
        assert stats["total_companies"] == 3
        assert stats["in_pool"] == 2
        assert stats["rated"] == 2
        assert stats["analyzed"] == 1
        assert stats["dna_distribution"]["S"] == 1
        assert stats["dna_distribution"]["A"] == 1

    def test_empty_stats(self, store):
        stats = store.get_stats()
        assert stats["total_companies"] == 0
        assert stats["rated"] == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestMemorySQL:
    """Tests for memory-related SQL behavior."""

    def test_update_situation_summary_targets_latest(self, store):
        """update_situation_summary should update the most recent analysis."""
        store.upsert_company("AAPL")
        store.save_analysis("AAPL", {"analysis_date": "2026-02-10"})
        store.save_analysis("AAPL", {"analysis_date": "2026-02-15"})

        store.update_situation_summary("AAPL", '{"price": 250}')

        analyses = store.get_analyses("AAPL")
        # Latest (Feb 15) should have the summary
        assert analyses[0]["situation_summary"] == '{"price": 250}'
        # Older (Feb 10) should remain None
        assert analyses[1]["situation_summary"] is None

    def test_get_analyses_with_memory(self, store):
        """get_analyses_with_memory should only return rows with situation_summary."""
        store.upsert_company("AAPL")
        store.save_analysis("AAPL", {"analysis_date": "2026-02-10"})
        store.save_analysis("AAPL", {"analysis_date": "2026-02-15"})

        # Only add memory to the older one
        older = store.get_analyses("AAPL")[-1]
        store.update_situation_summary("AAPL", '{"price": 200}', analysis_id=older["id"])

        memory_rows = store.get_analyses_with_memory("AAPL")
        assert len(memory_rows) == 1
        assert memory_rows[0]["analysis_date"] == "2026-02-10"


class TestEdgeCases:
    def test_db_path_creates_parent(self, tmp_path):
        db_path = tmp_path / "deep" / "nested" / "company.db"
        s = CompanyStore(db_path=db_path)
        s.upsert_company("AAPL")
        assert s.get_company("AAPL") is not None
        s.close()

    def test_evidence_roundtrip(self, store):
        """Evidence with unicode should survive JSON serialization."""
        store.upsert_company("PDD")
        evidence = ["拼多多海外扩张强劲", "Temu 月活增长", "管理层执行力"]
        store.save_oprms_rating("PDD", dna="A", timing="A", timing_coeff=0.8,
                                evidence=evidence)
        oprms = store.get_current_oprms("PDD")
        assert oprms["evidence"] == evidence

    def test_null_fields(self, store):
        store.upsert_company("AAPL")
        store.save_analysis("AAPL", {})  # minimal
        analysis = store.get_latest_analysis("AAPL")
        assert analysis is not None
        assert analysis["lens_quality_compounder"] is None
        assert analysis["debate_verdict"] is None

    def test_close_and_reopen(self, tmp_path):
        db_path = tmp_path / "company.db"
        s1 = CompanyStore(db_path=db_path)
        s1.upsert_company("AAPL", company_name="Apple")
        s1.close()

        s2 = CompanyStore(db_path=db_path)
        c = s2.get_company("AAPL")
        assert c["company_name"] == "Apple"
        s2.close()
