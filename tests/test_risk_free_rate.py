"""
Tests for risk-free rate cache.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import terminal.options.risk_free_rate as rfr_module
from terminal.options.risk_free_rate import (
    get_risk_free_rate,
    refresh_risk_free_rates,
    DEFAULT_RATE,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset module-level cache before each test."""
    rfr_module._mem_cache = {}
    rfr_module._cache_loaded = False
    yield
    rfr_module._mem_cache = {}
    rfr_module._cache_loaded = False


class TestGetRiskFreeRate:
    def test_exact_date_match(self):
        rfr_module._mem_cache = {"2026-01-15": 0.043}
        rfr_module._cache_loaded = True
        assert get_risk_free_rate("2026-01-15") == 0.043

    def test_weekend_fallback_to_friday(self):
        """Saturday should fall back to Friday."""
        rfr_module._mem_cache = {"2026-01-16": 0.042}  # Friday
        rfr_module._cache_loaded = True
        # 2026-01-17 is Saturday
        assert get_risk_free_rate("2026-01-17") == 0.042

    def test_sunday_fallback_to_friday(self):
        rfr_module._mem_cache = {"2026-01-16": 0.042}  # Friday
        rfr_module._cache_loaded = True
        # 2026-01-18 is Sunday
        assert get_risk_free_rate("2026-01-18") == 0.042

    def test_holiday_fallback(self):
        """Multi-day gap (holiday) should still find previous trading day."""
        rfr_module._mem_cache = {"2026-01-14": 0.041}  # Wednesday
        rfr_module._cache_loaded = True
        # Gap of 3 days
        assert get_risk_free_rate("2026-01-17") == 0.041

    def test_empty_cache_returns_default(self):
        rfr_module._mem_cache = {}
        rfr_module._cache_loaded = True
        assert get_risk_free_rate("2026-01-15") == DEFAULT_RATE

    def test_loads_from_disk_if_not_cached(self, tmp_path):
        cache_file = tmp_path / "risk_free_rates.json"
        cache_file.write_text(json.dumps({"2026-01-15": 0.044}))

        with patch.object(rfr_module, "_CACHE_FILE", cache_file):
            rate = get_risk_free_rate("2026-01-15")
        assert rate == 0.044


class TestRefreshRiskFreeRates:
    def test_no_api_key_returns_zero(self):
        with patch.dict("os.environ", {}, clear=True):
            # Remove FRED_API_KEY if present
            import os
            os.environ.pop("FRED_API_KEY", None)
            result = refresh_risk_free_rates()
        assert result == 0

    def test_successful_fetch(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "observations": [
                {"date": "2026-01-13", "value": "4.50"},
                {"date": "2026-01-14", "value": "4.48"},
                {"date": "2026-01-15", "value": "."},  # no data marker
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        cache_dir = tmp_path / "macro"
        cache_file = cache_dir / "risk_free_rates.json"

        with patch.dict("os.environ", {"FRED_API_KEY": "test_key"}), \
             patch("terminal.options.risk_free_rate.requests.get", return_value=mock_resp), \
             patch.object(rfr_module, "_CACHE_DIR", cache_dir), \
             patch.object(rfr_module, "_CACHE_FILE", cache_file):
            count = refresh_risk_free_rates()

        assert count == 2  # "." entry filtered out
        assert rfr_module._mem_cache["2026-01-13"] == 0.045  # 4.50/100
        assert abs(rfr_module._mem_cache["2026-01-14"] - 0.0448) < 1e-10

        # Verify disk cache
        assert cache_file.exists()
        disk_data = json.loads(cache_file.read_text())
        assert "2026-01-13" in disk_data

    def test_api_failure_returns_zero(self):
        import requests
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key"}), \
             patch(
                 "terminal.options.risk_free_rate.requests.get",
                 side_effect=requests.exceptions.Timeout("timeout"),
             ):
            result = refresh_risk_free_rates()
        assert result == 0
