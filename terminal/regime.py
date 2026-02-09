"""
Macro regime interface — uses MacroSnapshot from FRED data pipeline.

Decision tree:
  CRISIS  → VIX > 45  OR  (VIX > 35 AND deep curve inversion)
  RISK_OFF → (VIX > 25 AND curve inverted) OR GDP < 0 OR HY spread > 500bp
  RISK_ON  → VIX < 18 AND curve positive AND GDP > 2%
  NEUTRAL  → default
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    """Market regime classification."""
    RISK_ON = "risk_on"           # Expansion, bullish, add risk
    NEUTRAL = "neutral"           # Normal conditions
    RISK_OFF = "risk_off"         # Contraction, defensive, reduce risk
    CRISIS = "crisis"             # Severe stress, capital preservation


@dataclass
class RegimeAssessment:
    """Current macro regime assessment."""
    regime: MarketRegime
    confidence: str = "low"       # low / medium / high
    rationale: str = ""
    data_sources: int = 0         # Number of data sources used
    assessed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "data_sources": self.data_sources,
            "assessed_at": self.assessed_at,
        }


def get_current_regime() -> RegimeAssessment:
    """
    Get current market regime assessment using FRED macro data.

    Falls back to NEUTRAL with low confidence if macro data is unavailable.
    """
    try:
        from terminal.macro_fetcher import get_macro_snapshot
        snapshot = get_macro_snapshot()
    except Exception as e:
        logger.warning("Macro snapshot unavailable: %s", e)
        snapshot = None

    if snapshot is None:
        return RegimeAssessment(
            regime=MarketRegime.NEUTRAL,
            confidence="low",
            rationale="Macro data unavailable — defaulting to NEUTRAL.",
            data_sources=0,
            assessed_at=datetime.now().isoformat(),
        )

    # Map snapshot regime string to enum
    regime_map = {
        "RISK_ON": MarketRegime.RISK_ON,
        "NEUTRAL": MarketRegime.NEUTRAL,
        "RISK_OFF": MarketRegime.RISK_OFF,
        "CRISIS": MarketRegime.CRISIS,
    }
    regime = regime_map.get(snapshot.regime, MarketRegime.NEUTRAL)

    return RegimeAssessment(
        regime=regime,
        confidence=snapshot.regime_confidence,
        rationale=snapshot.regime_rationale,
        data_sources=snapshot.data_source_count,
        assessed_at=snapshot.fetched_at or datetime.now().isoformat(),
    )


def get_regime_adjustment(regime: MarketRegime) -> float:
    """
    Suggested position sizing multiplier based on regime.

    In risk-off or crisis, reduce position sizes.
    """
    return {
        MarketRegime.RISK_ON: 1.0,
        MarketRegime.NEUTRAL: 1.0,
        MarketRegime.RISK_OFF: 0.7,
        MarketRegime.CRISIS: 0.4,
    }[regime]
