"""Unified universe resolution layer (R1-R3, R13).

Every downstream consumer (backfill, events, health, verifier, morning
report, migration matrix) resolves its symbol set through `resolve_universe`
or `current_base_universe` instead of guessing at Extended/Core/watchlist
semantics independently. Base universe reads DB SSOT (security_master +
extended_membership) — never `extended_universe.json`, which is a
rebuildable cache owned by T17 (R4-P1-1).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, Optional, Tuple

VALID_BASES = ("extended", "none")


@dataclass(frozen=True)
class ResolvedUniverse:
    base: str
    symbols: Tuple[str, ...]
    provenance: Dict[str, str]
    generated_at: str


def _default_symbol_loader() -> Iterable[str]:
    from src.data.market_store import get_store
    return get_store().get_active_members()


def _default_eligibility_loader() -> Dict[str, bool]:
    from src.data.market_store import get_store
    return get_store().get_security_eligibility()


def resolve_universe(
    base: str = "extended",
    overlays: Iterable[str] = (),
    *,
    eligible_only: bool = True,
    symbol_loader: Optional[Callable[[], Iterable[str]]] = None,
    eligibility_loader: Optional[Callable[[], Dict[str, bool]]] = None,
    overlay_loaders: Optional[Dict[str, Callable[[], Iterable[str]]]] = None,
) -> ResolvedUniverse:
    if base not in VALID_BASES:
        raise ValueError(f"unknown base: {base!r} (must be one of {VALID_BASES})")

    provenance: Dict[str, str] = {}

    if base == "extended":
        loader = symbol_loader or _default_symbol_loader
        base_symbols = {s.upper() for s in loader()}
        if eligible_only:
            elig_loader = eligibility_loader or _default_eligibility_loader
            eligibility = elig_loader()  # fail-loud propagates (empty SM, T3)
            base_symbols = {s for s in base_symbols if eligibility.get(s, False)}
        for s in base_symbols:
            provenance[s] = "base"
    # base == "none": no base symbols, no loader invocation at all

    overlay_loaders = overlay_loaders or {}
    for name in overlays:
        if name not in overlay_loaders:
            raise ValueError(f"unknown overlay: {name!r}")
        for s in overlay_loaders[name]():
            symbol = s.upper()
            if symbol not in provenance:  # base provenance always wins, no pollution
                provenance[symbol] = f"overlay:{name}"

    symbols = tuple(sorted(provenance.keys()))
    generated_at = datetime.now(timezone.utc).isoformat()
    return ResolvedUniverse(base=base, symbols=symbols, provenance=provenance,
                            generated_at=generated_at)


def current_base_universe(store=None) -> list:
    """Active Extended membership ∩ SM eligible — DB SSOT (R3-P1-1, R4-P1-1).

    T10/T11/T14/T18's unified entry point. Deliberately distinct from the SM
    full set (which includes historical/delisted identities). Never reads
    extended_universe.json — that cache is a derived artifact rebuilt by T17.
    """
    if store is None:
        from src.data.market_store import get_store
        store = get_store()

    active = store.get_active_members()
    if not active:
        raise RuntimeError("extended_membership empty — run bootstrap first")

    eligibility = store.get_security_eligibility()  # fail-loud on empty SM (T3)
    active_set = {s.upper() for s in active}
    return sorted(s for s in active_set if eligibility.get(s, False))
