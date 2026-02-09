"""
MacroSnapshot — flat dataclass holding a point-in-time macro environment snapshot.

All fields are Optional so partial data (e.g., FRED unavailable) still works.
Derived values (term premium, real rate, regimes) are pre-computed.
"""
from dataclasses import dataclass, field, fields, asdict
from typing import Optional
import json


@dataclass
class MacroSnapshot:
    """Point-in-time macro environment snapshot for pipeline injection."""
    fetched_at: str = ""

    # ── Yield Curve ──
    us2y: Optional[float] = None
    us5y: Optional[float] = None
    us10y: Optional[float] = None
    us30y: Optional[float] = None
    spread_10y_2y: Optional[float] = None     # FRED T10Y2Y (pre-calculated)
    spread_10y_3m: Optional[float] = None     # FRED T10Y3M (pre-calculated)
    term_premium: Optional[float] = None      # 30Y - 2Y (derived)

    # ── Fed & Inflation ──
    fed_funds: Optional[float] = None
    cpi_yoy: Optional[float] = None
    real_rate_10y: Optional[float] = None     # 10Y - CPI (derived)

    # ── Economy ──
    gdp_growth: Optional[float] = None
    unemployment: Optional[float] = None

    # ── Volatility & Risk ──
    vix: Optional[float] = None
    vix_regime: str = "UNKNOWN"               # LOW / NORMAL / ELEVATED / PANIC
    hy_spread: Optional[float] = None         # High Yield spread in percentage points

    # ── Dollar & Carry Trade ──
    dxy: Optional[float] = None
    dxy_trend: str = "UNKNOWN"                # STRENGTHENING / WEAKENING / STABLE
    usdjpy: Optional[float] = None
    japan_rate: Optional[float] = None

    # ── Liquidity ──
    fed_balance_sheet_t: Optional[float] = None  # In trillions USD

    # ── 30-day Trends (basis points for rates, raw for others) ──
    us2y_30d_chg_bp: Optional[int] = None
    us5y_30d_chg_bp: Optional[int] = None
    us10y_30d_chg_bp: Optional[int] = None
    us30y_30d_chg_bp: Optional[int] = None
    vix_30d_chg: Optional[float] = None
    dxy_30d_chg: Optional[float] = None

    # ── Regime Assessment ──
    regime: str = "NEUTRAL"                   # RISK_ON / NEUTRAL / RISK_OFF / CRISIS
    regime_confidence: str = "low"            # low / medium / high
    regime_rationale: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "MacroSnapshot":
        """Deserialize from dict, ignoring unknown fields."""
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_str: str) -> "MacroSnapshot":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @property
    def data_source_count(self) -> int:
        """Count how many primary data points are populated (non-None numeric fields)."""
        count = 0
        for field_name in [
            "us2y", "us5y", "us10y", "us30y", "spread_10y_2y", "spread_10y_3m",
            "fed_funds", "cpi_yoy", "gdp_growth", "unemployment",
            "vix", "hy_spread", "dxy", "usdjpy", "japan_rate", "fed_balance_sheet_t",
        ]:
            if getattr(self, field_name) is not None:
                count += 1
        return count

    def format_for_prompt(self) -> str:
        """Format as markdown block for injection into analysis prompts."""
        lines = ["### Macro Environment"]

        # Yield curve
        if self.us2y is not None:
            chg = f" ({self.us2y_30d_chg_bp:+d}bp 30d)" if self.us2y_30d_chg_bp is not None else ""
            lines.append(f"- US 2Y: {self.us2y:.2f}%{chg}")
        if self.us5y is not None:
            chg = f" ({self.us5y_30d_chg_bp:+d}bp 30d)" if self.us5y_30d_chg_bp is not None else ""
            lines.append(f"- US 5Y: {self.us5y:.2f}%{chg}")
        if self.us10y is not None:
            chg = f" ({self.us10y_30d_chg_bp:+d}bp 30d)" if self.us10y_30d_chg_bp is not None else ""
            lines.append(f"- US 10Y: {self.us10y:.2f}%{chg}")
        if self.us30y is not None:
            chg = f" ({self.us30y_30d_chg_bp:+d}bp 30d)" if self.us30y_30d_chg_bp is not None else ""
            lines.append(f"- US 30Y: {self.us30y:.2f}%{chg}")
        if self.spread_10y_2y is not None:
            lines.append(f"- 10Y-2Y Spread: {self.spread_10y_2y:+.2f}%")
        if self.spread_10y_3m is not None:
            lines.append(f"- 10Y-3M Spread: {self.spread_10y_3m:+.2f}%")
        if self.term_premium is not None:
            lines.append(f"- Term Premium (30Y-2Y): {self.term_premium:+.2f}%")

        # Fed & inflation
        if self.fed_funds is not None:
            lines.append(f"- Fed Funds: {self.fed_funds:.2f}%")
        if self.cpi_yoy is not None:
            lines.append(f"- CPI YoY: {self.cpi_yoy:.1f}%")
        if self.real_rate_10y is not None:
            lines.append(f"- Real Rate (10Y-CPI): {self.real_rate_10y:+.1f}%")

        # Economy
        if self.gdp_growth is not None:
            lines.append(f"- GDP Growth: {self.gdp_growth:.1f}%")
        if self.unemployment is not None:
            lines.append(f"- Unemployment: {self.unemployment:.1f}%")

        # Volatility
        if self.vix is not None:
            chg = f" ({self.vix_30d_chg:+.1f} 30d)" if self.vix_30d_chg is not None else ""
            lines.append(f"- VIX: {self.vix:.1f} ({self.vix_regime}){chg}")
        if self.hy_spread is not None:
            # FRED BAMLH0A0HYM2 is in percentage points (e.g. 3.15 = 315bp)
            lines.append(f"- HY Spread: {self.hy_spread * 100:.0f}bp ({self.hy_spread:.2f}%)")

        # Dollar & carry
        if self.dxy is not None:
            chg = f" ({self.dxy_30d_chg:+.1f} 30d)" if self.dxy_30d_chg is not None else ""
            lines.append(f"- DXY: {self.dxy:.1f} ({self.dxy_trend}){chg}")
        if self.usdjpy is not None:
            lines.append(f"- USD/JPY: {self.usdjpy:.1f}")
        if self.japan_rate is not None:
            lines.append(f"- BOJ Rate: {self.japan_rate:.2f}%")

        # Liquidity
        if self.fed_balance_sheet_t is not None:
            lines.append(f"- Fed Balance Sheet: ${self.fed_balance_sheet_t:.2f}T")

        # Regime
        lines.append(f"- **Regime: {self.regime}** (confidence: {self.regime_confidence})")
        if self.regime_rationale:
            lines.append(f"  - {self.regime_rationale}")

        return "\n".join(lines)
