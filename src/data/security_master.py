"""Security classification for the extended primary universe (R1).

Pure logic, no DB access and no network calls. Classifies raw FMP profile
payloads into eligible/blocked securities (`classify_security`) and cross
validates share-class groupings via CIK + normalized company name
(`resolve_share_classes`). Callers (bootstrap / entrant flows) own I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional

# Trailing tokens stripped from a normalized company name (already lowercased
# and stripped of punctuation) before identity comparison.
_NAME_SUFFIX_TOKENS = {"inc", "corp", "corporation", "class", "co", "ltd", "plc"}
_NAME_STRIP_TABLE = str.maketrans("", "", ".,'&")


def _field(profile: dict, *candidates: str) -> Any:
    """Return the first present, non-None value among candidate field names.

    FMP payload field names have drifted across vendor versions (e.g.
    `exchangeShortName` vs `exchange`, `mktCap` vs `marketCap`); callers pass
    every known candidate in priority order.
    """
    for name in candidates:
        value = profile.get(name)
        if value is not None:
            return value
    return None


def _normalize_name(name: Optional[str]) -> str:
    """Normalize a company name for cross-symbol identity comparison."""
    if not name:
        return ""
    cleaned = name.lower().translate(_NAME_STRIP_TABLE)
    tokens = [t for t in cleaned.split() if t]
    while tokens and tokens[-1] in _NAME_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


@dataclass
class SecurityRecord:
    symbol: str
    cik: Optional[str]
    company_name: Optional[str]
    exchange: Optional[str]
    is_etf: bool
    is_fund: bool
    is_adr: bool
    share_class_of: Optional[str]
    eligible: bool
    reason: str


def classify_security(profile: dict) -> SecurityRecord:
    """Classify a single FMP profile payload. No cross-symbol grouping here."""
    symbol = profile.get("symbol")
    cik = _field(profile, "cik")
    company_name = _field(profile, "companyName", "company_name")
    exchange = _field(profile, "exchangeShortName", "exchange")
    is_etf = bool(_field(profile, "isEtf") or False)
    is_fund = bool(_field(profile, "isFund") or False)
    is_adr = bool(_field(profile, "isAdr") or False)

    base = dict(
        symbol=symbol,
        cik=cik,
        company_name=company_name,
        exchange=exchange,
        is_etf=is_etf,
        is_fund=is_fund,
        is_adr=is_adr,
        share_class_of=None,
    )

    if is_etf:
        return SecurityRecord(**base, eligible=False, reason="etf")
    if is_fund:
        return SecurityRecord(**base, eligible=False, reason="fund")
    if not company_name or not cik:
        return SecurityRecord(**base, eligible=False, reason="missing_profile")

    return SecurityRecord(**base, eligible=True, reason="ok")


def _pick_by_metric(members, profiles_by_symbol: dict, *candidates: str) -> Optional[str]:
    """Return the symbol with the unique highest metric value, or None.

    None is returned both when no member has the metric and when the top
    value is tied across members — either case must fall through to the
    next tie-break tier (or to needs_review_primary).
    """
    values = {}
    for m in members:
        profile = profiles_by_symbol.get(m.symbol, {})
        v = _field(profile, *candidates)
        if v is not None:
            values[m.symbol] = v
    if not values:
        return None
    max_value = max(values.values())
    winners = [sym for sym, v in values.items() if v == max_value]
    return winners[0] if len(winners) == 1 else None


def resolve_share_classes(records, overrides: dict, profiles_by_symbol: dict) -> list:
    """Group classify_security() output by CIK and settle primary/secondary status.

    Only records already classified as reason=="ok" participate in grouping;
    etf/fund/missing_profile records pass through unchanged. Primary/secondary
    resolution order: share_class_overrides.json (by CIK) -> higher mktCap ->
    higher volAvg -> needs_review_primary for the whole group (no auto pick).
    """
    overrides = overrides or {}
    groupable = [r for r in records if r.reason == "ok"]

    groups: dict = {}
    for r in groupable:
        groups.setdefault(r.cik, []).append(r)

    updates: dict = {}

    for cik, members in groups.items():
        if len(members) < 2:
            continue

        names = {_normalize_name(m.company_name) for m in members}
        if len(names) > 1:
            # Same CIK, different company names: vendor CIK data is not
            # trustworthy for this group — block all members, don't guess.
            for m in members:
                updates[m.symbol] = replace(
                    m, eligible=False, reason="identity_conflict", share_class_of=None
                )
            continue

        member_symbols = {m.symbol for m in members}
        override_symbol = overrides.get(cik)
        primary_symbol = override_symbol if override_symbol in member_symbols else None

        if primary_symbol is None:
            primary_symbol = _pick_by_metric(members, profiles_by_symbol, "mktCap", "marketCap")
        if primary_symbol is None:
            primary_symbol = _pick_by_metric(members, profiles_by_symbol, "volAvg", "averageVolume")

        if primary_symbol is None:
            for m in members:
                updates[m.symbol] = replace(
                    m, eligible=False, reason="needs_review_primary", share_class_of=None
                )
            continue

        for m in members:
            if m.symbol == primary_symbol:
                updates[m.symbol] = replace(m, eligible=True, reason="ok", share_class_of=None)
            else:
                updates[m.symbol] = replace(
                    m, eligible=False, reason="secondary_share_class", share_class_of=primary_symbol
                )

    return [updates.get(r.symbol, r) for r in records]
