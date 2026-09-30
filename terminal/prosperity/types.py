"""Data structures of the M4 input packet (immutable except SymbolHistory)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class EpsQuarter:
    fiscal_date: str
    announce_date: str
    eps_actual: Optional[float]
    eps_estimated: Optional[float]
    labels: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PreAnnouncement:
    value: Optional[float]
    source: Optional[str]
    snapshot_date: Optional[str]
    price_pre_announce: Optional[float]
    missing_reason: Optional[str]


@dataclass(frozen=True)
class NtmResult:
    value: Optional[float]
    basis: Optional[str]
    quarters: Tuple[Tuple[str, float, Optional[int]], ...]
    snapshot_date: Optional[str]
    missing_reason: Optional[str]


@dataclass(frozen=True)
class RevisionResult:
    delta_eps: Optional[float]
    window_weeks: int
    quarters: Tuple[str, ...]
    base_snapshot: Optional[str]
    current_snapshot: Optional[str]
    missing_reason: Optional[str]


@dataclass(frozen=True)
class ConsensusInputs:
    pre_announce: PreAnnouncement
    ntm: NtmResult
    ttm_eps: Optional[float]
    ttm_quarters: Tuple[str, ...]
    revision: RevisionResult
    unit_factor: Optional[float]
    missing_reasons: Mapping[str, str]


@dataclass(frozen=True)
class QuarterInputs:
    fiscal_date: str
    fiscal_year: Optional[str]
    period: Optional[str]
    period_days: Optional[int]
    reported_currency: Optional[str]
    available_on: Optional[str]
    availability_basis: Optional[str]
    revenue: Optional[float]
    cost_of_revenue: Optional[float]
    gross_profit: Optional[float]
    net_income: Optional[float]
    operating_cash_flow: Optional[float]
    capital_expenditure: Optional[float]
    free_cash_flow: Optional[float]
    labels: Tuple[str, ...]
    nulled: Tuple[str, ...]
    observed_on: Optional[str] = None   # UTC date an archive/live read proved the row; None in approximate replay


@dataclass
class SymbolHistory:
    symbol: str
    income: List[dict]
    balance: List[dict]
    cashflow: List[dict]
    vintage: Dict[str, List[dict]]
    earnings: List[dict]
    estimates: List[dict]
    splits: List[dict]
    closes: List[Tuple[str, float]]
    market_caps: List[Tuple[str, float]]
    profile: Optional[dict]
    is_adr: Optional[bool]
    # statement → [(date removed from the current table, archived_at)]; strict replay only
    removed: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)


@dataclass(frozen=True)
class InputPacket:
    symbol: str
    as_of: str
    membership_basis: str
    sector: Optional[str]
    industry: Optional[str]
    current_fiscal: Optional[str]
    quarter_bucket: Optional[str]
    data_age_days: Optional[int]
    reported_this_week: bool
    quarters: Tuple[QuarterInputs, ...]
    eps: Tuple[EpsQuarter, ...]
    consensus: ConsensusInputs
    price_asof: Optional[float]
    price_date: Optional[str]
    market_cap_asof: Optional[float]
    beta: Optional[float]
    pit: Mapping[str, str]
    pit_basis: str
    flags: Tuple[str, ...]
    archive: Mapping[str, Any]
    # profile ipoDate unless market-cap history predates it (then untrusted); static, not sliced by as_of
    listing_date: Optional[str] = None
