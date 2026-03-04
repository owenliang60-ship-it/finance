"""
Hypothesis templates for autonomous factor mining.
"""

from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class FactorHypothesis:
    """Reusable hypothesis template for candidate generation."""

    hypothesis_id: str
    name: str
    description: str
    seed_expressions: Tuple[str, ...]


DEFAULT_HYPOTHESES: Tuple[FactorHypothesis, ...] = (
    FactorHypothesis(
        hypothesis_id="trend_following",
        name="Trend Following",
        description="Momentum/trend continuation from price and moving averages.",
        seed_expressions=(
            "ret_n(close, 20)",
            "sub(ema(close, 20), ema(close, 50))",
            "macd(close, 12, 26, 9)",
        ),
    ),
    FactorHypothesis(
        hypothesis_id="mean_reversion",
        name="Mean Reversion",
        description="Revert-to-mean signals from stretched moves.",
        seed_expressions=(
            "sub(50, rsi(close, 14))",
            "mul(-1, zscore(ret_n(close, 5), 60))",
        ),
    ),
    FactorHypothesis(
        hypothesis_id="volatility_state",
        name="Volatility State",
        description="Volatility compression/expansion transitions.",
        seed_expressions=(
            "zscore(vol_n(close, 20), 120)",
            "div(vol_n(close, 10), vol_n(close, 60))",
        ),
    ),
    FactorHypothesis(
        hypothesis_id="volume_price_interaction",
        name="Volume x Price",
        description="Joint price/volume behavior as participation signal.",
        seed_expressions=(
            "mul(ret_n(close, 5), zscore(volume, 20))",
            "rank_cs(mul(ret_n(close, 20), zscore(volume, 60)))",
        ),
    ),
)


def list_hypotheses() -> List[FactorHypothesis]:
    """Return default hypothesis templates."""
    return list(DEFAULT_HYPOTHESES)

