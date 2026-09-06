"""Validated current snapshot for the weekly derived Premium Pool."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from config.settings import DATA_DIR
from terminal.selection_compass import (
    BETA_MINIMUM,
    EPS_GROWTH_THRESHOLD,
    FOUR_QUARTER_GROWTH_THRESHOLD,
    _passes_minimum,
)

PREMIUM_POOL_NAME = "精选Premium池"
PREMIUM_POOL_SCHEMA_VERSION = 1
PREMIUM_POOL_MAX_AGE_DAYS = 8
PREMIUM_POOL_PATH = Path(os.environ.get(
    "FINANCE_PREMIUM_POOL_PATH", str(DATA_DIR / "pool" / "premium_pool.json"),
))


def criteria_payload() -> dict[str, Any]:
    return {
        "eps_yoy": EPS_GROWTH_THRESHOLD,
        "eps_qoq": EPS_GROWTH_THRESHOLD,
        "growth_avg_4q": FOUR_QUARTER_GROWTH_THRESHOLD,
        "beta_6m": BETA_MINIMUM,
        "turnaround": "four-quarter-endpoints-up",
    }


def build_artifact(*, result: dict, as_of: str, universe_symbols: list[str],
                   generated_at: str | None = None) -> dict:
    if not result.get("available"):
        raise ValueError("cannot publish an unavailable Premium Pool result")
    symbols = sorted({str(symbol).strip().upper() for symbol in universe_symbols})
    generated = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": PREMIUM_POOL_SCHEMA_VERSION,
        "name": PREMIUM_POOL_NAME,
        "as_of": as_of,
        "generated_at": generated,
        "criteria": criteria_payload(),
        "universe": {
            "name": "extended",
            "count": len(symbols),
            "symbols_sha256": hashlib.sha256(
                "\n".join(symbols).encode("utf-8")
            ).hexdigest(),
        },
        "coverage": result["coverage"],
        "members": result.get("members") or [],
    }


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing generated_at")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("generated_at must include timezone")
    return parsed.astimezone(timezone.utc)


def validate_premium_pool(payload: Any, *, now: datetime | None = None,
                          check_freshness: bool = True) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Premium Pool payload must be an object")
    if (payload.get("schema_version") != PREMIUM_POOL_SCHEMA_VERSION
            or payload.get("name") != PREMIUM_POOL_NAME):
        raise ValueError("Premium Pool schema/name mismatch")
    if payload.get("criteria") != criteria_payload():
        raise RuntimeError("Premium Pool criteria mismatch")
    if not isinstance(payload.get("as_of"), str):
        raise ValueError("Premium Pool as_of missing")
    date.fromisoformat(payload["as_of"])
    generated_at = _parse_timestamp(payload.get("generated_at"))
    if check_freshness:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = (current - generated_at).total_seconds()
        if age < -300:
            raise ValueError("Premium Pool generated_at is in the future")
        if age > PREMIUM_POOL_MAX_AGE_DAYS * 86400:
            raise TimeoutError("Premium Pool snapshot stale")
    universe = payload.get("universe")
    coverage = payload.get("coverage")
    members = payload.get("members")
    if not isinstance(universe, dict) or universe.get("name") != "extended":
        raise ValueError("Premium Pool universe invalid")
    universe_count = universe.get("count")
    universe_hash = universe.get("symbols_sha256")
    if (not isinstance(universe_count, int) or universe_count <= 0
            or not isinstance(universe_hash, str) or len(universe_hash) != 64):
        raise ValueError("Premium Pool universe metadata invalid")
    if not isinstance(coverage, dict) or not {
        "fundamental_ready", "beta_ready",
    }.issubset(coverage):
        raise ValueError("Premium Pool coverage invalid")
    for key in ("fundamental_ready", "beta_ready"):
        stat = coverage[key]
        if not isinstance(stat, dict):
            raise ValueError("Premium Pool coverage stat invalid")
        covered, total, ratio = stat.get("covered"), stat.get("total"), stat.get("ratio")
        if (not isinstance(covered, int) or not isinstance(total, int)
                or total != universe_count or covered < 0 or covered > total):
            raise ValueError("Premium Pool coverage counts invalid")
        expected_ratio = covered / total
        if (not isinstance(ratio, (int, float))
                or not math.isclose(float(ratio), expected_ratio, abs_tol=1e-12)
                or ratio < 0.95):
            raise ValueError("Premium Pool coverage ratio invalid")
    if not isinstance(members, list):
        raise ValueError("Premium Pool members invalid")
    if len(members) > universe_count:
        raise ValueError("Premium Pool member count exceeds universe")
    seen = set()
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("Premium Pool member must be an object")
        symbol = member.get("symbol")
        if (not isinstance(symbol, str) or not symbol or symbol != symbol.upper()
                or symbol in seen):
            raise ValueError("Premium Pool member symbol invalid or duplicated")
        seen.add(symbol)
        try:
            beta = float(member.get("beta_6m"))
        except (TypeError, ValueError):
            raise ValueError("Premium Pool member beta invalid")
        if not math.isfinite(beta) or not _passes_minimum(beta, BETA_MINIMUM):
            raise ValueError("Premium Pool member below beta threshold")
        required = {
            "eps_yoy_turnaround", "eps_qoq_turnaround", "growth_route",
            "quarter_date",
        }
        if not required.issubset(member):
            raise ValueError("Premium Pool member fields incomplete")
        if not isinstance(member["quarter_date"], str):
            raise ValueError("Premium Pool quarter_date invalid")
        date.fromisoformat(member["quarter_date"])
        for growth_key, turnaround_key in (
            ("eps_yoy_growth", "eps_yoy_turnaround"),
            ("eps_qoq_growth", "eps_qoq_turnaround"),
        ):
            turnaround = member.get(turnaround_key)
            growth = member.get(growth_key)
            if not isinstance(turnaround, bool):
                raise ValueError("Premium Pool EPS turnaround flag invalid")
            if turnaround:
                if growth is not None:
                    raise ValueError("Premium Pool turnaround EPS must not invent growth")
            else:
                try:
                    growth_value = float(growth)
                except (TypeError, ValueError):
                    raise ValueError("Premium Pool EPS growth invalid")
                if (not math.isfinite(growth_value)
                        or not _passes_minimum(growth_value, EPS_GROWTH_THRESHOLD)):
                    raise ValueError("Premium Pool member below EPS threshold")
        route = member.get("growth_route")
        average = member.get("growth_avg_4q")
        if route == "cagr":
            try:
                average_value = float(average)
            except (TypeError, ValueError):
                raise ValueError("Premium Pool CAGR average invalid")
            if (not math.isfinite(average_value)
                    or not _passes_minimum(
                        average_value, FOUR_QUARTER_GROWTH_THRESHOLD,
                    )):
                raise ValueError("Premium Pool member below growth threshold")
        elif route == "turnaround":
            if average is not None:
                raise ValueError("Premium Pool turnaround average must be null")
        else:
            raise ValueError("Premium Pool growth route invalid")


def load_premium_pool(path: str | Path = PREMIUM_POOL_PATH, *,
                      now: datetime | None = None) -> dict:
    target = Path(path)
    if not target.is_file():
        return {"available": False, "reason": "premium_pool_missing", "members": []}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        validate_premium_pool(payload, now=now)
    except RuntimeError:
        return {"available": False, "reason": "premium_pool_criteria_mismatch", "members": []}
    except TimeoutError:
        return {"available": False, "reason": "premium_pool_stale", "members": []}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"available": False, "reason": "premium_pool_invalid", "members": []}
    return {**payload, "available": True, "reason": None}


def publish_premium_pool(payload: dict, path: str | Path = PREMIUM_POOL_PATH) -> Path:
    validate_premium_pool(payload, check_freshness=False)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=".premium_pool.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        verify = json.loads(temp_path.read_text(encoding="utf-8"))
        validate_premium_pool(verify, check_freshness=False)
        os.replace(temp_path, target)
        temp_path = None
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return target
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
