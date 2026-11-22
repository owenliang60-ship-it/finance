"""Hand-written decision ledger (TOML, read-only) and its schema validation.

Every failure raises LedgerError naming the field path. Unknown keys fail at every level, so a
typo (e.g. `is_lower_bnd`) cannot silently fall back to a default and let a fact into arithmetic.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Optional, Tuple

from terminal.valuation.kernel import fin

EPS_BASES = ("non_gaap", "ifrs", "gaap")
PERIOD_TYPES = ("quarter", "fiscal_year", "run_rate")
DENOMINATORS = ("total_revenue", "segment", "other")
REQUIRED = ("schema", "symbol", "reporting_currency", "eps_basis")
TOP_KEYS = REQUIRED + ("cyclical", "t1_domains", "assumptions", "fx", "sources", "segments", "eps_facts",
                       "drivers", "guidance", "cycles", "acks")
# section → (required keys, optional keys)
ENTRIES = {
    "assumptions": ((), ("incremental_margin", "note")),
    "fx": (("local_per_usd",), ("as_of",)),
    "sources": (("id", "url", "fetched_at", "raw", "sha256"), ()),
    "segments": (("unit", "names"), ("quarters",)),
    "segments.quarters": (("fiscal_date", "values", "source", "quote"), ("prior_year_restated",)),
    "eps_facts": (("fiscal_date", "value", "source", "quote"), ()),
    "drivers": (("id", "label", "base", "min", "max"), ("basis_note",)),
    "guidance": (("id", "label", "fiscal_date", "value", "period_type", "denominator", "is_lower_bound",
                  "source", "quote"), ()),
    "cycles": (("id", "label", "start", "end"), ()),
    "acks": (("key",), ("note", "date")),
}
DATE_KEYS = ("as_of", "fetched_at", "fiscal_date", "start", "end", "date")
NUM_KEYS = ("incremental_margin", "local_per_usd", "unit", "value", "base", "min", "max")
NUM_LIST_KEYS = ("values", "prior_year_restated")
POSITIVE_KEYS = ("local_per_usd", "unit")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class Ledger:
    path: Path
    symbol: str
    reporting_currency: str
    eps_basis: str
    cyclical: bool
    t1_domains: Tuple[str, ...]
    boss_margin: Optional[float]
    fx_local_per_usd: Optional[float]
    sources: Mapping[str, Mapping]
    segment_unit: Optional[float]
    segment_names: Tuple[str, ...]
    segment_quarters: Tuple[Mapping, ...]
    eps_facts: Tuple[Mapping, ...]
    drivers: Tuple[Mapping, ...]
    guidance: Tuple[Mapping, ...]
    cycles: Tuple[Mapping, ...]
    acks: frozenset          # ack key 集合


def _field(where: str, key: str, value):
    if key in DATE_KEYS:
        if isinstance(value, date) and not hasattr(value, "hour"):
            value = value.isoformat()
        if not isinstance(value, str) or not DATE_RE.match(value):
            raise LedgerError(f"{where}: not a YYYY-MM-DD date")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise LedgerError(f"{where}: not a valid date") from None
        return value
    if key in NUM_KEYS:
        num = fin(value)
        if num is None or (key in POSITIVE_KEYS and num <= 0):
            raise LedgerError(f"{where}: not a finite{' positive' if key in POSITIVE_KEYS else ''} number")
        return num
    if key in NUM_LIST_KEYS:
        if not isinstance(value, list) or not value:
            raise LedgerError(f"{where}: must be a non-empty list of numbers")
        nums = tuple(fin(v) for v in value)
        if any(v is None for v in nums):
            raise LedgerError(f"{where}: every element must be a finite number")
        return nums
    if key == "is_lower_bound":
        if not isinstance(value, bool):
            raise LedgerError(f"{where}: must be true or false")
        return value
    if key == "period_type" and value not in PERIOD_TYPES:
        raise LedgerError(f"{where}: must be one of {PERIOD_TYPES}")
    if key == "denominator" and value not in DENOMINATORS:
        raise LedgerError(f"{where}: must be one of {DENOMINATORS}")
    if key == "names":
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
            raise LedgerError(f"{where}: must be a non-empty list of names")
        return tuple(value)
    if not isinstance(value, str) or not value:
        raise LedgerError(f"{where}: must be a non-empty string")
    return value


def _entry(where: str, section: str, table) -> dict:
    if not isinstance(table, dict):
        raise LedgerError(f"{where}: must be a table")
    required, optional = ENTRIES[section]
    for key in table:
        if key not in required and key not in optional:
            raise LedgerError(f"{where}.{key}: unknown key '{key}'")
    for key in required:
        if key not in table:
            raise LedgerError(f"{where}.{key}: required key missing")
    return {key: _field(f"{where}.{key}", key, value) for key, value in table.items() if key != "quarters"}


def _entries(raw: dict, section: str, key: str):
    items = raw.get(key, [])
    if not isinstance(items, list):
        raise LedgerError(f"{key}: must be an array of tables ([[{key}]])")
    return tuple(_entry(f"{key}[{i}]", section, t) for i, t in enumerate(items))


def _unique(items, where: str, key: str):
    seen = set()
    for i, item in enumerate(items):
        if item[key] in seen:
            raise LedgerError(f"{where}[{i}].{key}: duplicate '{item[key]}'")
        seen.add(item[key])


def _validate(raw: dict, path: Path) -> Ledger:
    for key in raw:
        if key not in TOP_KEYS:
            raise LedgerError(f"unknown top-level key '{key}'")
    for key in REQUIRED:
        if key not in raw:
            raise LedgerError(f"{key}: required key missing")
    if raw["schema"] != 1 or isinstance(raw["schema"], bool):
        raise LedgerError(f"schema: must be 1 (got {raw['schema']!r})")
    symbol = _field("symbol", "symbol", raw["symbol"])
    currency = _field("reporting_currency", "reporting_currency", raw["reporting_currency"])
    basis = raw["eps_basis"]
    if basis not in EPS_BASES:
        raise LedgerError(f"eps_basis: must be one of {EPS_BASES} (got {basis!r})")
    if basis == "non_gaap" and currency != "USD":
        raise LedgerError(f"eps_basis: non_gaap requires reporting_currency USD (got {currency})")
    cyclical = raw.get("cyclical", False)
    if not isinstance(cyclical, bool):
        raise LedgerError("cyclical: must be true or false")
    domains = raw.get("t1_domains", [])
    if not isinstance(domains, list) or not all(isinstance(d, str) and d for d in domains):
        raise LedgerError("t1_domains: must be a list of domain names")
    assumptions = _entry("assumptions", "assumptions", raw.get("assumptions", {}))
    fx = _entry("fx", "fx", raw["fx"]) if "fx" in raw else {}

    sources = _entries(raw, "sources", "sources")
    _unique(sources, "sources", "id")
    segments = _entry("segments", "segments", raw["segments"]) if "segments" in raw else {}
    quarters = raw.get("segments", {}).get("quarters", [])
    if not isinstance(quarters, list):
        raise LedgerError("segments.quarters: must be an array of tables")
    seg_quarters = tuple(_entry(f"segments.quarters[{i}]", "segments.quarters", q) for i, q in enumerate(quarters))
    names = segments.get("names", ())
    for i, q in enumerate(seg_quarters):
        for key in NUM_LIST_KEYS:
            if key in q and len(q[key]) != len(names):
                raise LedgerError(f"segments.quarters[{i}].{key}: {len(q[key])} values for {len(names)} names")
    eps_facts = _entries(raw, "eps_facts", "eps_facts")
    drivers = _entries(raw, "drivers", "drivers")
    guidance = _entries(raw, "guidance", "guidance")
    cycles = _entries(raw, "cycles", "cycles")
    acks = _entries(raw, "acks", "acks")
    for where, items in (("drivers", drivers), ("guidance", guidance), ("cycles", cycles)):
        _unique(items, where, "id")
    for i, d in enumerate(drivers):
        if not d["min"] <= d["base"] <= d["max"]:
            raise LedgerError(f"drivers[{i}]: requires min <= base <= max")
    for i, c in enumerate(cycles):
        if c["start"] > c["end"]:
            raise LedgerError(f"cycles[{i}]: start is after end")
    ids = {s["id"] for s in sources}
    for where, items in (("segments.quarters", seg_quarters), ("eps_facts", eps_facts), ("guidance", guidance)):
        for i, item in enumerate(items):
            if item["source"] not in ids:
                raise LedgerError(f"{where}[{i}].source: unknown source '{item['source']}'")
    return Ledger(
        path=path, symbol=symbol, reporting_currency=currency, eps_basis=basis, cyclical=cyclical,
        t1_domains=tuple(domains), boss_margin=assumptions.get("incremental_margin"),
        fx_local_per_usd=fx.get("local_per_usd"), sources={s["id"]: s for s in sources},
        segment_unit=segments.get("unit"), segment_names=names, segment_quarters=seg_quarters,
        eps_facts=eps_facts, drivers=drivers, guidance=guidance, cycles=cycles,
        acks=frozenset(a["key"] for a in acks),
    )


def load_ledger(path: Path) -> Ledger:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"{path}: cannot read ledger: {exc}") from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = str(exc)
        if "line" not in msg:
            msg += f" (line {text.count(chr(10)) + 1})"
        raise LedgerError(f"{path}: invalid TOML: {msg}") from None
    try:
        return _validate(raw, path)
    except LedgerError as exc:
        raise LedgerError(f"{path}: {exc}") from None
