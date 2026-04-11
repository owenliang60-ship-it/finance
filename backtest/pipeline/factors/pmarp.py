from __future__ import annotations

from typing import Any, Dict, List

from backtest.pipeline.factors._base import PipelineFactor
from backtest.pipeline.primitives.pit_data import PitData
from src.indicators.pmarp import analyze_pmarp


class PMARPPipelineFactor(PipelineFactor):
    name = "PMARP"

    def compute(
        self,
        pit_data: PitData,
        symbols: List[str],
        as_of_date: str,
        params: Dict[str, Any],
    ) -> Dict[str, float]:
        ema_period = int(params.get("ema_period", 20))
        lookback = int(params.get("lookback", 150))
        window = max(ema_period + lookback + 5, 180)
        price_dict = pit_data.bulk_price_windows(
            symbols,
            end_date=as_of_date,
            lookback_days=window,
            min_rows=ema_period + lookback,
        )
        scores: Dict[str, float] = {}
        for symbol, frame in price_dict.items():
            result = analyze_pmarp(frame, ema_period=ema_period, lookback=lookback)
            current = result.get("current")
            if current is not None:
                scores[symbol] = float(current)
        return scores
