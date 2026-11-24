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

from terminal.valuation.config import MAX_SCENARIOS, SEG_ADJ_RANGE, WEIGHT_TOL
from terminal.valuation.kernel import fin

EPS_BASES = ("non_gaap", "ifrs", "gaap")
PERIOD_TYPES = ("quarter", "fiscal_year", "run_rate")
DENOMINATORS = ("total_revenue", "segment", "other")
TONES = ("bull", "base", "bear")
RESERVED_SCENARIO_IDS = ("consensus", "custom")
REQUIRED = ("schema", "symbol", "reporting_currency", "eps_basis")
TOP_KEYS = REQUIRED + ("cyclical", "t1_domains", "assumptions", "fx", "sources", "segments", "eps_facts",
                       "drivers", "guidance", "cycles", "acks", "scenarios")
# section → (required keys, optional keys)
ENTRIES = {
    "assumptions": ((), ("incremental_margin", "consensus_weight", "note")),
    "fx": (("local_per_usd",), ("as_of",)),
    "sources": (("id", "url", "fetched_at", "raw", "sha256"), ()),
    "segments": (("unit", "names"), ("quarters", "axis_rationale", "roles", "colors", "secondary_note")),
    "segments.quarters": (("fiscal_date", "values", "source", "quote"), ("prior_year_restated", "pct_derived")),
    "eps_facts": (("fiscal_date", "value", "source", "quote"), ()),
    "drivers": (("id", "label", "base", "min", "max"), ("basis_note",)),
    "guidance": (("id", "label", "fiscal_date", "value", "period_type", "denominator", "is_lower_bound",
                  "source", "quote"), ()),
    "cycles": (("id", "label", "start", "end"), ()),
    "acks": (("key",), ("note", "date")),
    "scenarios": (("id", "label", "tone", "thesis", "seg_adjs", "net_margin", "pe_mid", "pe_half", "weight"), ()),
}
DATE_KEYS = ("as_of", "fetched_at", "fiscal_date", "start", "end", "date")
NUM_KEYS = ("incremental_margin", "local_per_usd", "unit", "value", "base", "min", "max", "consensus_weight",
            "net_margin", "pe_mid", "pe_half", "weight")
NUM_LIST_KEYS = ("values", "prior_year_restated", "seg_adjs")
BOOL_KEYS = ("is_lower_bound", "pct_derived")
NAME_LIST_KEYS = ("names", "roles")
POSITIVE_KEYS = ("local_per_usd", "unit")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")   # 写进 SVG 属性，必须严格


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
    segment_axis_rationale: Optional[str] = None
    segment_roles: Tuple[str, ...] = ()
    segment_colors: Tuple[str, ...] = ()
    segment_secondary_note: Optional[str] = None
    scenarios: Tuple[Mapping, ...] = ()
    consensus_weight: Optional[float] = None


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
    if key in BOOL_KEYS:
        if not isinstance(value, bool):
            raise LedgerError(f"{where}: must be true or false")
        return value
    if key == "tone" and value not in TONES:
        raise LedgerError(f"{where}: must be one of {TONES}")
    if key == "colors":
        if not isinstance(value, list) or not all(isinstance(v, str) and COLOR_RE.match(v) for v in value):
            raise LedgerError(f"{where}: must be a list of #rrggbb colors")
        return tuple(value)
    if key == "period_type" and value not in PERIOD_TYPES:
        raise LedgerError(f"{where}: must be one of {PERIOD_TYPES}")
    if key == "denominator" and value not in DENOMINATORS:
        raise LedgerError(f"{where}: must be one of {DENOMINATORS}")
    if key in NAME_LIST_KEYS:
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


def _scenarios(raw: dict, n_adjs: int, consensus_weight: Optional[float]):
    items = _entries(raw, "scenarios", "scenarios")
    if len(items) > MAX_SCENARIOS:
        raise LedgerError(f"scenarios: at most {MAX_SCENARIOS} entries (got {len(items)})")
    lo, hi = SEG_ADJ_RANGE
    for i, s in enumerate(items):
        where = f"scenarios[{i}]"
        if s["id"] in RESERVED_SCENARIO_IDS:
            raise LedgerError(f"{where}.id: '{s['id']}' is reserved")
        if len(s["seg_adjs"]) != n_adjs:
            raise LedgerError(f"{where}.seg_adjs: {len(s['seg_adjs'])} values, expected {n_adjs}")
        if not all(lo <= a <= hi for a in s["seg_adjs"]):
            raise LedgerError(f"{where}.seg_adjs: every value must be within [{lo}, {hi}]")
        if not -1 < s["net_margin"] <= 1:
            raise LedgerError(f"{where}.net_margin: must satisfy -1 < net_margin <= 1")
        if not s["pe_mid"] > 0:
            raise LedgerError(f"{where}.pe_mid: must be > 0")
        if not 0 <= s["pe_half"] < s["pe_mid"]:
            raise LedgerError(f"{where}.pe_half: must satisfy 0 <= pe_half < pe_mid")
        if not 0 <= s["weight"] <= 100:
            raise LedgerError(f"{where}.weight: must be within [0, 100]")
    _unique(items, "scenarios", "id")
    _unique(items, "scenarios", "tone")
    if items:
        total = consensus_weight or 0.0
        for s in items:
            total += s["weight"]
        if abs(total - 100) > WEIGHT_TOL:
            raise LedgerError(f"scenarios: weights (consensus_weight + Σ weight) sum to {total:g}, not 100")
    return items


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
    for key in ("roles", "colors"):
        if key in segments and len(segments[key]) != len(names):
            raise LedgerError(f"segments.{key}: {len(segments[key])} values for {len(names)} names")
    consensus_weight = assumptions.get("consensus_weight")
    if consensus_weight is not None and not 0 <= consensus_weight <= 100:
        raise LedgerError("assumptions.consensus_weight: must be within [0, 100]")
    scenarios = _scenarios(raw, len(names) if "segments" in raw else 1, consensus_weight)
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
        segment_axis_rationale=segments.get("axis_rationale"), segment_roles=segments.get("roles", ()),
        segment_colors=segments.get("colors", ()), segment_secondary_note=segments.get("secondary_note"),
        scenarios=scenarios, consensus_weight=consensus_weight,
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
