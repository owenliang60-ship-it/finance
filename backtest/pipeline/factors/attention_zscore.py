from __future__ import annotations

from typing import Any, Dict, List

from backtest.pipeline.factors._base import PipelineFactor
from backtest.pipeline.primitives.pit_data import PitData
from src.indicators.social_attention import attention_zscore


class AttentionZScorePipelineFactor(PipelineFactor):
    name = "Attention_ZScore"

    def compute(
        self,
        pit_data: PitData,
        symbols: List[str],
        as_of_date: str,
        params: Dict[str, Any],
    ) -> Dict[str, float]:
        window = int(params.get("window", 20))
        history_days = max(window + 5, 10)
        scores: Dict[str, float] = {}
        for symbol in symbols:
            mentions_history = pit_data.social_mentions_history_as_of(
                symbol=symbol,
                end_date=as_of_date,
                lookback_days=history_days,
            )
            score = attention_zscore(mentions_history, window=window)
            if score is not None:
                scores[symbol] = float(score)
        return scores
