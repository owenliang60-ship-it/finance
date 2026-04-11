from __future__ import annotations

from typing import Dict, List

import pandas as pd

from backtest.pipeline.factors.registry import get_factor
from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.spec import ComboSpec, FactorInput
from backtest.pipeline.types import SignalComputationResult


class SignalEngine:
    def __init__(self, pit_data: PitData):
        self.pit_data = pit_data

    def compute(
        self,
        factors: List[FactorInput],
        combo: ComboSpec,
        universe_df: pd.DataFrame,
    ) -> SignalComputationResult:
        dates = sorted(universe_df["date"].unique().tolist())
        factor_frames: Dict[str, pd.DataFrame] = {}

        for factor_input in factors:
            factor = get_factor(factor_input.name)
            rows = []
            for as_of_date in dates:
                symbols = (
                    universe_df.loc[universe_df["date"] == as_of_date, "symbol"]
                    .astype(str)
                    .tolist()
                )
                scores = factor.compute(
                    pit_data=self.pit_data,
                    symbols=symbols,
                    as_of_date=as_of_date,
                    params=factor_input.params,
                )
                transformed = self._transform_scores(
                    scores=scores,
                    symbols=symbols,
                    transform=factor_input.transform,
                    direction=factor_input.direction,
                )
                rows.extend(
                    {"date": as_of_date, "symbol": symbol, "value": value}
                    for symbol, value in transformed.items()
                )

            frame = pd.DataFrame(rows).pivot(index="date", columns="symbol", values="value").sort_index()
            factor_frames[factor_input.name] = frame

        combo_frame = self._combine_frames(
            factor_frames=factor_frames,
            factors=factors,
            combo_method=combo.method,
        )
        return SignalComputationResult(
            factor_frames=factor_frames,
            combo_frame=combo_frame,
        )

    def _transform_scores(
        self,
        scores: Dict[str, float],
        symbols: List[str],
        transform: str,
        direction: str,
    ) -> Dict[str, float]:
        series = pd.Series({symbol: scores.get(symbol) for symbol in symbols}, dtype=float)
        if direction == "lower_is_better":
            series = -series

        valid = series.dropna()
        if valid.empty:
            return {symbol: float("nan") for symbol in symbols}

        if transform == "raw":
            transformed = series
        elif transform == "rank_pct":
            transformed = valid.rank(method="average", pct=True)
            transformed = transformed.reindex(series.index)
        elif transform == "zscore":
            std = float(valid.std(ddof=1))
            if len(valid) == 1 or std <= 1e-12:
                transformed = pd.Series(0.0, index=valid.index)
            else:
                transformed = (valid - float(valid.mean())) / std
            transformed = transformed.reindex(series.index)
        else:
            raise ValueError(f"Unsupported transform: {transform}")
        return {symbol: (float(value) if pd.notna(value) else float("nan")) for symbol, value in transformed.items()}

    def _combine_frames(
        self,
        factor_frames: Dict[str, pd.DataFrame],
        factors: List[FactorInput],
        combo_method: str,
    ) -> pd.DataFrame:
        if combo_method == "single":
            return factor_frames[factors[0].name].copy()

        ordered_frames = [factor_frames[factor.name].copy() for factor in factors]
        if combo_method == "weighted_sum":
            weighted = []
            for frame, factor in zip(ordered_frames, factors):
                weighted.append(frame * factor.weight)
            total = weighted[0].copy()
            mask = total.notna()
            for frame in weighted[1:]:
                total = total.add(frame, fill_value=0.0)
                mask &= frame.notna()
            return total.where(mask)

        if combo_method == "rank_average":
            ranked_frames = []
            for frame in ordered_frames:
                ranked = frame.rank(axis=1, pct=True, method="average")
                ranked_frames.append(ranked)
            total = ranked_frames[0].copy()
            mask = total.notna()
            for frame in ranked_frames[1:]:
                total = total.add(frame, fill_value=0.0)
                mask &= frame.notna()
            return (total / len(ranked_frames)).where(mask)

        raise ValueError(f"Unsupported combo method: {combo_method}")
