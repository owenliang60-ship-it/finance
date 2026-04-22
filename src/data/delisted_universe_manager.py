"""
Delisted universe overlay manager for survivorship-sensitive studies.

This module keeps delisted large-cap symbols separate from the active
extended universe cache. Research code can opt into the combined
`extended_true` universe without polluting active scanners/cron jobs.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POOL_DIR = _PROJECT_ROOT / "data" / "pool"

DELISTED_LARGE_CAP_CANDIDATES_FILE = _POOL_DIR / "delisted_large_cap_candidates.json"
DELISTED_LARGE_CAPS_FILE = _POOL_DIR / "delisted_large_caps.json"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _normalize_symbol(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("symbol must be a non-empty string")
    return value.strip().upper()


def _validate_candidate_registry(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {"updated": None, "count": 0, "candidates": []}

    candidates = payload.get("candidates")
    if candidates is None:
        raise ValueError("candidate registry missing 'candidates' list")
    if not isinstance(candidates, list):
        raise ValueError("'candidates' must be a list")

    normalized = []
    for idx, item in enumerate(candidates):
        if not isinstance(item, dict):
            raise ValueError(f"candidate #{idx} must be an object")
        row = dict(item)
        row["symbol"] = _normalize_symbol(row.get("symbol"))
        normalized.append(row)

    return {
        "updated": payload.get("updated"),
        "count": len(normalized),
        "candidates": normalized,
    }


def _validate_delisted_overlay(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {"updated": None, "count": 0, "symbols": []}

    symbols = payload.get("symbols")
    if symbols is None:
        raise ValueError("delisted overlay missing 'symbols' list")
    if not isinstance(symbols, list):
        raise ValueError("'symbols' must be a list")

    cleaned = sorted({_normalize_symbol(symbol) for symbol in symbols})
    validated = dict(payload)
    validated["symbols"] = cleaned
    validated["count"] = len(cleaned)
    return validated


def load_delisted_candidate_registry(
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load and validate the audited candidate registry."""
    return _validate_candidate_registry(
        _read_json(path or DELISTED_LARGE_CAP_CANDIDATES_FILE)
    )


def get_delisted_candidate_symbols(path: Optional[Path] = None) -> List[str]:
    """Return sorted candidate symbols from the audited registry."""
    registry = load_delisted_candidate_registry(path=path)
    return sorted({row["symbol"] for row in registry["candidates"]})


def load_delisted_large_caps(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and validate the executable delisted overlay."""
    return _validate_delisted_overlay(_read_json(path or DELISTED_LARGE_CAPS_FILE))


def get_delisted_large_cap_symbols(path: Optional[Path] = None) -> List[str]:
    """Return sorted executable delisted overlay symbols."""
    payload = load_delisted_large_caps(path=path)
    return payload["symbols"]


def write_delisted_large_caps(
    symbols: List[str],
    path: Optional[Path] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist the executable overlay as a sorted symbol list."""
    cleaned = sorted({_normalize_symbol(symbol) for symbol in symbols})
    payload: Dict[str, Any] = {
        "updated": date.today().isoformat(),
        "count": len(cleaned),
        "symbols": cleaned,
    }
    if metadata:
        payload.update(metadata)
        payload["count"] = len(cleaned)
        payload["symbols"] = cleaned
    target = path or DELISTED_LARGE_CAPS_FILE
    _write_json(target, payload)
    return payload


def get_extended_true_symbols(overlay_path: Optional[Path] = None) -> List[str]:
    """Return active extended symbols plus delisted overlay symbols."""
    from src.data.extended_universe_manager import get_extended_symbols

    active = set(get_extended_symbols())
    overlay = set(get_delisted_large_cap_symbols(path=overlay_path))
    return sorted(active | overlay)
