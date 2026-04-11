from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.spec import PortfolioSpec


class PortfolioBuilder:
    def __init__(self, pit_data: PitData):
        self.pit_data = pit_data

    def build_target_weights(
        self,
        score_frame: pd.DataFrame,
        portfolio: PortfolioSpec,
    ) -> pd.DataFrame:
        rows: List[dict[str, float | str]] = []

        for as_of_date, row in score_frame.sort_index().iterrows():
            valid = row.dropna().astype(float)
            if valid.empty:
                continue

            selected = self._select(valid, portfolio)
            if selected.empty:
                continue

            weights = self._weight(selected, str(as_of_date), portfolio)
            rows.extend(
                {"date": str(as_of_date), "symbol": symbol, "weight": weight}
                for symbol, weight in weights.items()
                if weight > 0
            )

        if not rows:
            return pd.DataFrame(index=score_frame.index)

        frame = (
            pd.DataFrame(rows)
            .pivot(index="date", columns="symbol", values="weight")
            .sort_index()
        )
        return frame

    def _select(self, scores: pd.Series, portfolio: PortfolioSpec) -> pd.Series:
        ranked = scores.sort_values(ascending=False)
        if portfolio.selection == "top_n":
            if portfolio.top_n is None:
                raise ValueError("portfolio.top_n is required for top_n selection")
            return ranked.head(portfolio.top_n)

        if portfolio.selection == "threshold":
            if portfolio.threshold is None:
                raise ValueError("portfolio.threshold is required for threshold selection")
            return ranked[ranked >= portfolio.threshold]

        raise ValueError(f"Unsupported selection: {portfolio.selection}")

    def _weight(
        self,
        selected: pd.Series,
        as_of_date: str,
        portfolio: PortfolioSpec,
    ) -> Dict[str, float]:
        if portfolio.weighting == "equal":
            base = {symbol: 1.0 / len(selected) for symbol in selected.index}
            return self._cap_and_normalize(base, portfolio.max_position_weight)

        if portfolio.weighting == "inv_vol":
            return self._inv_vol_weights(
                symbols=selected.index.tolist(),
                as_of_date=as_of_date,
                lookback_days=portfolio.vol_lookback_days,
                max_position_weight=portfolio.max_position_weight,
            )

        raise ValueError(f"Unsupported weighting: {portfolio.weighting}")

    def _inv_vol_weights(
        self,
        symbols: List[str],
        as_of_date: str,
        lookback_days: int,
        max_position_weight: float,
    ) -> Dict[str, float]:
        windows = self.pit_data.bulk_price_windows(
            symbols=symbols,
            end_date=as_of_date,
            lookback_days=max(lookback_days + 1, 21),
            min_rows=min(max(lookback_days // 2, 10), lookback_days + 1),
        )
        vol_map: Dict[str, float] = {}
        for symbol, frame in windows.items():
            returns = frame["close"].pct_change().dropna()
            if len(returns) >= 2:
                vol_map[symbol] = float(returns.std(ddof=1) * np.sqrt(252))

        valid_vols = [value for value in vol_map.values() if value > 1e-9]
        if not valid_vols:
            return self._cap_and_normalize(
                {symbol: 1.0 / len(symbols) for symbol in symbols},
                max_position_weight,
            )

        median_vol = float(np.median(valid_vols))
        raw = {
            symbol: 1.0 / max(vol_map.get(symbol, median_vol), 1e-6)
            for symbol in symbols
        }
        return self._cap_and_normalize(raw, max_position_weight)

    def _cap_and_normalize(
        self,
        raw_weights: Dict[str, float],
        max_position_weight: float,
    ) -> Dict[str, float]:
        positive = {symbol: weight for symbol, weight in raw_weights.items() if weight > 0}
        if not positive:
            return {}

        total = sum(positive.values())
        if total <= 1e-12:
            return {}

        target = {symbol: weight / total for symbol, weight in positive.items()}
        final = {symbol: 0.0 for symbol in target}
        remaining = 1.0
        active = set(target)

        while active and remaining > 1e-12:
            active_total = sum(target[symbol] for symbol in active)
            if active_total <= 1e-12:
                break

            saturated: List[str] = []
            for symbol in list(active):
                proposed = remaining * (target[symbol] / active_total)
                allocation = min(proposed, max_position_weight)
                final[symbol] += allocation
                if allocation >= max_position_weight - 1e-12:
                    saturated.append(symbol)

            used = sum(final.values())
            remaining = max(0.0, 1.0 - used)

            if not saturated:
                break
            for symbol in saturated:
                active.discard(symbol)

        # If aggregate cap capacity is below 100%, leave the residual in cash.
        return {symbol: weight for symbol, weight in final.items() if weight > 1e-12}
