"""Security classification for the extended primary universe (R1).

Pure logic, no DB access and no network calls. Classifies raw FMP profile
payloads into eligible/blocked securities (`classify_security`) and cross
validates share-class groupings via CIK + normalized company name
(`resolve_share_classes`). Callers (bootstrap / entrant flows) own I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Optional

# Descriptor tokens stripped from a normalized company name wherever they
# occur (not just at the end) — real payloads embed share-class wording
# mid-string, e.g. "CoreWeave, Inc. Class A Common Stock" or "Wise Group plc
# Class A Ordinary Shares" (both real FMP entries in the main repo).
_NAME_DESCRIPTOR_TOKENS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "class", "common", "preferred", "ordinary", "shares",
    "share", "stock", "stk",
}
_NAME_STRIP_TABLE = str.maketrans("", "", ".,'&")

# FMP's profile endpoint does not expose a reliable security-type field.  For
# listed preferred/debt instruments it does, however, expose an explicit
# security title and/or the NYSE preferred suffix.  Keep this list narrow:
# ADR "Depositary Shares" and partnership "Common Units" are valid equities
# in this universe and must not be swept up by a broad token match.
_PREFERRED_SYMBOL_RE = re.compile(r"-P[A-Z0-9]*$", re.IGNORECASE)
_NON_COMMON_NAME_RE = re.compile(
    r"(?:\bpfd\b|\bpreferred\b|\bincome\s+capital\s+obligations?\b|"
    r"\bdebentures?\b|\bsubordinated\s+notes?\b|\bnotes?\s+20\d{2}\b|"
    r"\d+(?:\.\d+)?\s*%)",
    re.IGNORECASE,
)

PRIMARY_METRIC_VOLUME = "volume"
PRIMARY_METRIC_MARKET_CAP = "market_cap"


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
    """Normalize a company name for cross-symbol identity comparison.

    Descriptor tokens (entity suffixes + share-class wording) are dropped
    wherever they appear, not only at the end, since real payloads embed
    them mid-string (e.g. "... Class A Common Stock"). A leftover
    single-letter token (the class letter itself, "a"/"b"/...) is dropped
    too, since it never distinguishes the underlying company identity.
    """
    if not name:
        return ""
    cleaned = name.lower().translate(_NAME_STRIP_TABLE)
    tokens = [t for t in cleaned.split() if t and t not in _NAME_DESCRIPTOR_TOKENS]
    tokens = [t for t in tokens if len(t) > 1]
    return " ".join(tokens)


def _is_explicit_non_common_instrument(symbol: Optional[str],
                                       company_name: Optional[str]) -> bool:
    """Fail closed only on explicit preferred/debt evidence.

    Market-cap and company-description fields are issuer-level in FMP and
    cannot identify the listed security.  The ticker/title signals above are
    security-level; ambiguous records continue into CIK grouping/review.
    """
    return bool(
        _PREFERRED_SYMBOL_RE.search(symbol or "")
        or _NON_COMMON_NAME_RE.search(company_name or "")
    )


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
    if _is_explicit_non_common_instrument(symbol, company_name):
        return SecurityRecord(
            **base, eligible=False, reason="non_common_instrument")

    return SecurityRecord(**base, eligible=True, reason="ok")


def _metric_values(members, profiles_by_symbol: dict, *candidates: str) -> dict:
    """Return {symbol: value} for members whose profile has the metric."""
    values = {}
    for m in members:
        profile = profiles_by_symbol.get(m.symbol, {})
        v = _field(profile, *candidates)
        if v is not None:
            values[m.symbol] = v
    return values


def _unique_max_symbol(values: dict) -> Optional[str]:
    """Return the symbol with the unique highest value, or None if tied/empty."""
    if not values:
        return None
    max_value = max(values.values())
    winners = [sym for sym, v in values.items() if v == max_value]
    return winners[0] if len(winners) == 1 else None


def _pick_primary_and_metric(members, profiles_by_symbol: dict):
    """Return `(winner, deciding_metric)` for the frozen metric cascade."""
    volume_values = _metric_values(
        members, profiles_by_symbol, "volAvg", "averageVolume")
    winner = _unique_max_symbol(volume_values)
    if winner is not None:
        return winner, PRIMARY_METRIC_VOLUME

    market_cap_values = _metric_values(
        members, profiles_by_symbol, "mktCap", "marketCap")
    winner = _unique_max_symbol(market_cap_values)
    return winner, (PRIMARY_METRIC_MARKET_CAP if winner is not None else None)


def selection_metric_for_share_classes(members, profiles_by_symbol: dict) -> Optional[str]:
    """Which metric actually decided this group, or None if neither did.

    Entrant settlement reuses this result to decide whether an established
    primary lost on evidence or merely lacked the metric that selected the
    entrant. Keeping the ruling here prevents its safety guard from drifting
    away from the primary-selection cascade again.
    """
    return _pick_primary_and_metric(members, profiles_by_symbol)[1]


def _pick_primary_by_metrics(members, profiles_by_symbol: dict) -> Optional[str]:
    """Choose the primary listing by liquidity, then market cap as fallback.

    FMP copies issuer-level market cap onto preferred/depositary classes; it
    therefore cannot safely outrank the common listing.  Average volume is
    security-level and identifies the traded primary.  If volume is missing
    or tied, a unique market-cap leader remains a useful fallback; otherwise
    the caller parks the whole group for review.
    """
    return _pick_primary_and_metric(members, profiles_by_symbol)[0]


def resolve_share_classes(records, overrides: dict, profiles_by_symbol: dict) -> list:
    """Group classify_security() output by CIK and settle primary/secondary status.

    Only records already classified as reason=="ok" participate in grouping;
    etf/fund/non-common/missing_profile records pass through unchanged.
    Primary/secondary resolution order: share_class_overrides.json (by CIK)
    -> higher volAvg -> higher mktCap fallback -> if everything is
    missing/tied throughout, needs_review_primary for the whole group (no
    auto pick).
    """
    overrides = overrides or {}
    groupable = [r for r in records if r.reason == "ok"]

    groups: dict = {}
    for r in groupable:
        groups.setdefault(r.cik, []).append(r)

    updates: dict = {}

    for cik, members in groups.items():
        # A present null override is an audited verdict that this CIK group has
        # no eligible common-equity representative (e.g. only preferred/debt
        # instruments remain in the screener). It must also block a singleton.
        if cik in overrides and overrides[cik] is None:
            for m in members:
                updates[m.symbol] = replace(
                    m, eligible=False, reason="identity_conflict",
                    share_class_of=None)
            continue
        if len(members) < 2:
            continue

        member_symbols = {m.symbol for m in members}
        override_symbol = overrides.get(cik)
        primary_symbol = override_symbol if override_symbol in member_symbols else None

        if primary_symbol is None:
            names = {_normalize_name(m.company_name) for m in members}
            if len(names) > 1:
                # Without a human verdict, same-CIK/different-name data is not
                # trustworthy enough to auto-select. A valid explicit override
                # is intentionally checked first so audited common-equity +
                # preferred/debt groups can be resolved deterministically.
                for m in members:
                    updates[m.symbol] = replace(
                        m, eligible=False, reason="identity_conflict",
                        share_class_of=None
                    )
                continue
            primary_symbol = _pick_primary_by_metrics(members, profiles_by_symbol)

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
