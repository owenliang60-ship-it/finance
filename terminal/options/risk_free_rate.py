"""
Risk-Free Rate Cache — 从 FRED DGS3MO 获取 3 月 T-bill 利率

用途: BS IV solver 需要无风险利率，backfill 时一次性拉取历史数据缓存。
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

# Fallback rate when FRED is unavailable
DEFAULT_RATE = 0.045

# Cache file location
_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "macro"
_CACHE_FILE = _CACHE_DIR / "risk_free_rates.json"

# In-memory cache to avoid repeated file IO during backfill
_mem_cache: Dict[str, float] = {}
_cache_loaded = False


def refresh_risk_free_rates(start_date: str = "2020-01-01") -> int:
    """Fetch historical 3-Month T-bill rates from FRED and cache to disk.

    Args:
        start_date: Start date for historical data (YYYY-MM-DD)

    Returns:
        Number of data points fetched
    """
    global _mem_cache, _cache_loaded

    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        logger.warning("FRED_API_KEY not set — using default rate %.3f", DEFAULT_RATE)
        _cache_loaded = True
        return 0

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": "DGS3MO",
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
        "sort_order": "asc",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        observations = data.get("observations", [])
        rates = {}
        for obs in observations:
            if obs.get("value") and obs["value"] != ".":
                try:
                    # FRED returns percentage (e.g. 4.5 = 4.5%), convert to decimal
                    rates[obs["date"]] = float(obs["value"]) / 100.0
                except (ValueError, TypeError):
                    continue

        if rates:
            # Save to disk
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(_CACHE_FILE, "w") as f:
                json.dump(rates, f)

            # Load into memory
            _mem_cache = rates
            _cache_loaded = True

            logger.info(
                "Risk-free rates cached: %d data points (%s to %s)",
                len(rates),
                min(rates.keys()),
                max(rates.keys()),
            )
            return len(rates)

    except requests.exceptions.RequestException as e:
        logger.warning("FRED API request failed: %s — using default rate", e)
    except (KeyError, ValueError) as e:
        logger.warning("FRED data parsing failed: %s — using default rate", e)

    _cache_loaded = True
    return 0


def _ensure_loaded():
    """Load cache from disk if not already in memory."""
    global _mem_cache, _cache_loaded
    if _cache_loaded:
        return
    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE, "r") as f:
                _mem_cache = json.load(f)
            _cache_loaded = True
            return
        except (json.JSONDecodeError, IOError):
            pass
    _cache_loaded = True


def get_risk_free_rate(date: str) -> float:
    """Get risk-free rate for a specific date.

    Falls back to most recent trading day for weekends/holidays.
    Returns DEFAULT_RATE if no data available.

    Args:
        date: Date string YYYY-MM-DD

    Returns:
        Annualized risk-free rate as decimal (e.g. 0.045)
    """
    _ensure_loaded()

    if not _mem_cache:
        return DEFAULT_RATE

    # Exact match
    if date in _mem_cache:
        return _mem_cache[date]

    # Fallback: search backwards up to 7 days for most recent trading day
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        for i in range(1, 8):
            prev = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
            if prev in _mem_cache:
                return _mem_cache[prev]
    except ValueError:
        pass

    return DEFAULT_RATE
