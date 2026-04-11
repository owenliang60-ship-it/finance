from __future__ import annotations

from typing import Any, Dict, List

from backtest.pipeline.factors._base import PipelineFactor
from backtest.pipeline.primitives.pit_data import PitData
from src.indicators.rs_rating import compute_rs_rating_b


class RSRatingBPipelineFactor(PipelineFactor):
    name = "RS_Rating_B"

    def compute(
        self,
        pit_data: PitData,
        symbols: List[str],
        as_of_date: str,
        params: Dict[str, Any],
    ) -> Dict[str, float]:
        lookback_days = int(params.get("lookback_days", 100))
        price_dict = pit_data.bulk_price_windows(
            symbols,
            end_date=as_of_date,
            lookback_days=lookback_days,
            min_rows=70,
        )
        result_df = compute_rs_rating_b(price_dict)
        if result_df.empty:
            return {}
        return dict(zip(result_df["symbol"], result_df["rs_rank"].astype(float)))
