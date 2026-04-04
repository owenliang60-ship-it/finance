"""
Holdings data models — Position, WatchlistEntry, InvestmentBucket

Uses dataclasses for zero-dependency type safety.
OPRMS constants imported from Knowledge Desk (knowledge/oprms/models.py).
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from knowledge.oprms.models import DNARating, TimingRating


# ---------------------------------------------------------------------------
# OPRMS Constants — derived from Knowledge Desk canonical definitions
# ---------------------------------------------------------------------------

OPRMS_DNA_LIMITS = {r.value: r.max_position_pct for r in DNARating}

OPRMS_TIMING_COEFFICIENTS = {r.value: r.coefficient_range for r in TimingRating}

OPRMS_TIMING_DEFAULTS = {r.value: r.midpoint for r in TimingRating}


# ---------------------------------------------------------------------------
# Investment Bucket
# ---------------------------------------------------------------------------

class InvestmentBucket(str, Enum):
    """Four investment bucket classification (from BidClub framework)."""
    COMPOUNDER = "Long-term Compounder"       # quality + durable advantages, 3+ yr
    CATALYST = "Catalyst-Driven Long"          # event-driven, 6-18 months
    SHORT = "Short Position"                   # overvaluation, defined risk
    SECULAR_SHORT = "Secular Short"            # structural decline, 3-5+ yr


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """A single portfolio position with OPRMS ratings and kill conditions."""

    # Identity
    symbol: str
    company_name: str = ""
    sector: str = ""
    industry: str = ""

    # OPRMS ratings
    dna_rating: str = "C"          # S / A / B / C
    timing_rating: str = "C"       # S / A / B / C

    # Classification
    investment_bucket: str = InvestmentBucket.COMPOUNDER.value

    # Position sizing
    cost_basis: float = 0.0        # average cost per share
    shares: float = 0.0            # number of shares held
    current_price: float = 0.0     # latest market price
    current_weight: float = 0.0    # actual portfolio weight (0-1)
    target_weight: float = 0.0     # OPRMS-derived target weight (0-1)

    # Risk management
    kill_conditions: List[str] = field(default_factory=list)
    memo_id: str = ""              # link to investment memo file/ID

    # Timestamps
    entry_date: str = ""           # YYYY-MM-DD
    last_review_date: str = ""     # YYYY-MM-DD

    # Misc
    notes: str = ""

    # DB-backed fields (SQLite lifecycle)
    position_id: Optional[int] = None          # DB primary key
    status: str = "OPEN"                        # OPEN / CLOSED
    close_date: Optional[str] = None
    realized_pnl: Optional[float] = None

    @property
    def max_weight(self) -> float:
        """Hard cap from DNA rating."""
        return OPRMS_DNA_LIMITS.get(self.dna_rating, 0.02)

    @property
    def market_value(self) -> float:
        """Current market value of the position."""
        return self.shares * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized P&L in dollars."""
        return self.shares * (self.current_price - self.cost_basis)

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L as percentage of cost basis."""
        if self.cost_basis <= 0:
            return 0.0
        return (self.current_price - self.cost_basis) / self.cost_basis

    @property
    def weight_vs_target(self) -> float:
        """Drift from target weight. Positive = overweight."""
        return self.current_weight - self.target_weight

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "sector": self.sector,
            "industry": self.industry,
            "dna_rating": self.dna_rating,
            "timing_rating": self.timing_rating,
            "investment_bucket": self.investment_bucket,
            "cost_basis": self.cost_basis,
            "shares": self.shares,
            "current_price": self.current_price,
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "kill_conditions": self.kill_conditions,
            "memo_id": self.memo_id,
            "entry_date": self.entry_date,
            "last_review_date": self.last_review_date,
            "notes": self.notes,
            "position_id": self.position_id,
            "status": self.status,
            "close_date": self.close_date,
            "realized_pnl": self.realized_pnl,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        """Deserialize from dict."""
        return cls(
            symbol=data.get("symbol", ""),
            company_name=data.get("company_name", ""),
            sector=data.get("sector", ""),
            industry=data.get("industry", ""),
            dna_rating=data.get("dna_rating", "C"),
            timing_rating=data.get("timing_rating", "C"),
            investment_bucket=data.get("investment_bucket", InvestmentBucket.COMPOUNDER.value),
            cost_basis=data.get("cost_basis", 0.0),
            shares=data.get("shares", 0.0),
            current_price=data.get("current_price", 0.0),
            current_weight=data.get("current_weight", 0.0),
            target_weight=data.get("target_weight", 0.0),
            kill_conditions=data.get("kill_conditions", []),
            memo_id=data.get("memo_id", ""),
            entry_date=data.get("entry_date", ""),
            last_review_date=data.get("last_review_date", ""),
            notes=data.get("notes", ""),
            position_id=data.get("position_id"),
            status=data.get("status", "OPEN"),
            close_date=data.get("close_date"),
            realized_pnl=data.get("realized_pnl"),
        )


# ---------------------------------------------------------------------------
# Watchlist Entry
# ---------------------------------------------------------------------------

@dataclass
class WatchlistEntry:
    """A stock being tracked but not yet held."""

    symbol: str
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    dna_rating: str = "C"
    investment_bucket: str = InvestmentBucket.COMPOUNDER.value
    target_entry_price: float = 0.0
    thesis_summary: str = ""
    kill_conditions: List[str] = field(default_factory=list)
    added_date: str = ""           # YYYY-MM-DD
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "sector": self.sector,
            "industry": self.industry,
            "dna_rating": self.dna_rating,
            "investment_bucket": self.investment_bucket,
            "target_entry_price": self.target_entry_price,
            "thesis_summary": self.thesis_summary,
            "kill_conditions": self.kill_conditions,
            "added_date": self.added_date,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WatchlistEntry":
        return cls(
            symbol=data.get("symbol", ""),
            company_name=data.get("company_name", ""),
            sector=data.get("sector", ""),
            industry=data.get("industry", ""),
            dna_rating=data.get("dna_rating", "C"),
            investment_bucket=data.get("investment_bucket", InvestmentBucket.COMPOUNDER.value),
            target_entry_price=data.get("target_entry_price", 0.0),
            thesis_summary=data.get("thesis_summary", ""),
            kill_conditions=data.get("kill_conditions", []),
            added_date=data.get("added_date", ""),
            notes=data.get("notes", ""),
        )
