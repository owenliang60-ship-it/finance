"""Tests for src/data/delisted_universe_manager.py."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.delisted_universe_manager import (
    get_delisted_candidate_symbols,
    get_extended_true_symbols,
    get_delisted_large_cap_symbols,
    load_delisted_candidate_registry,
    load_delisted_large_caps,
    write_delisted_large_caps,
)


@pytest.fixture
def tmp_registry_files(tmp_path, monkeypatch):
    candidates = tmp_path / "delisted_large_cap_candidates.json"
    overlay = tmp_path / "delisted_large_caps.json"
    monkeypatch.setattr(
        "src.data.delisted_universe_manager.DELISTED_LARGE_CAP_CANDIDATES_FILE",
        candidates,
    )
    monkeypatch.setattr(
        "src.data.delisted_universe_manager.DELISTED_LARGE_CAPS_FILE",
        overlay,
    )
    return candidates, overlay


def test_get_extended_true_symbols_returns_active_extended_when_overlay_missing(
    tmp_registry_files,
):
    del tmp_registry_files

    with patch(
        "src.data.extended_universe_manager.get_extended_symbols",
        return_value=["AAPL", "NVDA"],
    ):
        result = get_extended_true_symbols()

    assert result == ["AAPL", "NVDA"]


def test_get_extended_true_symbols_unions_overlay_and_deduplicates(tmp_registry_files):
    _, overlay = tmp_registry_files
    overlay.write_text(
        json.dumps(
            {
                "updated": "2026-04-22",
                "symbols": ["twtr", "AAPL", "vmw"],
            }
        ),
        encoding="utf-8",
    )

    with patch(
        "src.data.extended_universe_manager.get_extended_symbols",
        return_value=["AAPL", "NVDA"],
    ):
        result = get_extended_true_symbols()

    assert result == ["AAPL", "NVDA", "TWTR", "VMW"]


def test_load_delisted_candidate_registry_raises_on_missing_symbol(tmp_registry_files):
    candidates, _ = tmp_registry_files
    candidates.write_text(
        json.dumps(
            {
                "candidates": [
                    {"company_name": "Twitter"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="symbol"):
        load_delisted_candidate_registry()


def test_load_delisted_large_caps_raises_on_malformed_symbols(tmp_registry_files):
    _, overlay = tmp_registry_files
    overlay.write_text(
        json.dumps(
            {
                "symbols": ["TWTR", ""],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="symbol"):
        load_delisted_large_caps()


def test_write_delisted_large_caps_normalizes_symbols_and_keeps_metadata(tmp_registry_files):
    _, overlay = tmp_registry_files
    payload = write_delisted_large_caps(
        ["twtr", "ATVI", "twtr"],
        metadata={"source": "manual_audit"},
    )

    assert payload["symbols"] == ["ATVI", "TWTR"]
    assert payload["count"] == 2
    assert payload["source"] == "manual_audit"

    loaded = json.loads(overlay.read_text(encoding="utf-8"))
    assert loaded["symbols"] == ["ATVI", "TWTR"]


def test_get_delisted_candidate_symbols_returns_sorted_unique_symbols(tmp_registry_files):
    candidates, _ = tmp_registry_files
    candidates.write_text(
        json.dumps(
            {
                "candidates": [
                    {"symbol": "twtr", "source": "manual"},
                    {"symbol": "ATVI", "source": "manual"},
                    {"symbol": "TWTR", "source": "duplicate"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert get_delisted_candidate_symbols() == ["ATVI", "TWTR"]
    assert get_delisted_large_cap_symbols() == []
