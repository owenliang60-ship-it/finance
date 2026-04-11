from __future__ import annotations

from typing import Dict, Type

from backtest.pipeline.factors._base import PipelineFactor
from backtest.pipeline.factors.attention_zscore import AttentionZScorePipelineFactor
from backtest.pipeline.factors.pmarp import PMARPPipelineFactor
from backtest.pipeline.factors.rs_rating_b import RSRatingBPipelineFactor


ALL_FACTORS: Dict[str, Type[PipelineFactor]] = {
    "RS_Rating_B": RSRatingBPipelineFactor,
    "PMARP": PMARPPipelineFactor,
    "Attention_ZScore": AttentionZScorePipelineFactor,
}


def get_factor(name: str) -> PipelineFactor:
    if name not in ALL_FACTORS:
        available = ", ".join(sorted(ALL_FACTORS.keys()))
        raise KeyError(f"Unknown pipeline factor {name!r}. Available: {available}")
    return ALL_FACTORS[name]()
