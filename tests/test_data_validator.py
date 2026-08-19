"""Tests for src.data.data_validator — resolver migration (Task 20 matrix #1, #2)."""
import logging

from src.data import data_validator as dv


class TestCheckDataFreshnessSampling:
    """Matrix #1 (data_validator.py:137): get_symbols()[:5] sampling ->
    resolver eligible first 5."""

    def test_samples_from_resolver_eligible(self, monkeypatch):
        eligible = ["AAPL", "AMZN", "GOOG", "META", "MSFT", "NVDA", "TSLA"]
        monkeypatch.setattr(dv, "current_base_universe", lambda: list(eligible))
        monkeypatch.setattr(
            dv, "_get_market_db_freshness",
            lambda table: {"updated_at": "2026-08-18", "age_days": 1, "is_fresh": True},
        )
        monkeypatch.setattr(
            dv, "validate_price_data",
            lambda symbol: {"valid": True, "latest_date": "2026-08-18", "issues": []},
        )

        result = dv.check_data_freshness()

        sampled = list(result["details"]["price_samples"].keys())
        assert sampled  # non-empty
        assert set(sampled).issubset(set(eligible))
        assert len(sampled) == 5

    def test_falls_back_to_legacy_pool_when_resolver_raises(self, monkeypatch, caplog):
        def boom():
            raise RuntimeError("extended_membership empty — run bootstrap first")

        monkeypatch.setattr(dv, "current_base_universe", boom)
        monkeypatch.setattr(dv, "get_symbols", lambda: ["LEGACY1", "LEGACY2"])
        monkeypatch.setattr(
            dv, "_get_market_db_freshness",
            lambda table: {"updated_at": None, "age_days": -1, "is_fresh": False},
        )
        monkeypatch.setattr(
            dv, "validate_price_data",
            lambda symbol: {"valid": False, "latest_date": None, "issues": []},
        )

        with caplog.at_level(logging.WARNING, logger="src.data.data_validator"):
            result = dv.check_data_freshness()

        assert set(result["details"]["price_samples"].keys()) == {"LEGACY1", "LEGACY2"}
        assert any(
            "current_base_universe unavailable" in rec.message for rec in caplog.records
        )


class TestValidateAllDataPoolCount:
    """Matrix #2 (data_validator.py:190): pool_count -> resolver eligible count."""

    def test_pool_count_equals_eligible_count(self, monkeypatch):
        eligible = ["AAPL", "MSFT", "GOOG"]
        monkeypatch.setattr(dv, "current_base_universe", lambda: list(eligible))
        monkeypatch.setattr(
            dv, "validate_price_data",
            lambda symbol: {"valid": True, "latest_date": "2026-08-18", "issues": []},
        )
        monkeypatch.setattr(dv, "get_profile", lambda symbol: {"symbol": symbol})
        monkeypatch.setattr(dv, "get_ratios", lambda symbol: {"symbol": symbol})

        result = dv.validate_all_data()

        assert result["summary"]["pool_count"] == len(eligible)

    def test_falls_back_to_legacy_pool_when_resolver_raises(self, monkeypatch, caplog):
        def boom():
            raise RuntimeError("extended_membership empty — run bootstrap first")

        monkeypatch.setattr(dv, "current_base_universe", boom)
        monkeypatch.setattr(dv, "get_symbols", lambda: ["LEGACY1", "LEGACY2", "LEGACY3"])
        monkeypatch.setattr(
            dv, "validate_price_data",
            lambda symbol: {"valid": True, "latest_date": "2026-08-18", "issues": []},
        )
        monkeypatch.setattr(dv, "get_profile", lambda symbol: {"symbol": symbol})
        monkeypatch.setattr(dv, "get_ratios", lambda symbol: {"symbol": symbol})

        with caplog.at_level(logging.WARNING, logger="src.data.data_validator"):
            result = dv.validate_all_data()

        assert result["summary"]["pool_count"] == 3
        assert any(
            "current_base_universe unavailable" in rec.message for rec in caplog.records
        )
