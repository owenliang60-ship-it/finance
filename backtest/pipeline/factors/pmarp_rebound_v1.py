from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backtest.pipeline.factors._base import PipelineFactor
from backtest.pipeline.primitives.pit_data import PitData
from src.indicators.pmarp import calculate_pmarp
from src.indicators.rvol import calculate_rvol_series


class PMARPReboundV1PipelineFactor(PipelineFactor):
    """Event-style rebound factor carried through a fixed holding window.

    Entry logic:
    - PMARP crosses up through 2
    - RVOL z-score is above the confirmation threshold on the trigger day
    - market proxy passes a simple long-term EMA regime check on the trigger day

    Once triggered, the factor keeps the name alive for `holding_window_days`
    so the existing cross-sectional pipeline can approximate an event strategy
    without a dedicated event engine.
    """

    name = "PMARP_Rebound_V1"

    def compute_panel(
        self,
        pit_data: PitData,
        universe_df: pd.DataFrame,
        params: Dict[str, Any],
    ) -> pd.DataFrame:
        if universe_df.empty:
            return pd.DataFrame()

        trigger_threshold = float(params.get("trigger_threshold", 2.0))
        pmarp_ema_period = int(params.get("pmarp_ema_period", 20))
        pmarp_lookback = int(params.get("pmarp_lookback", 150))
        rvol_lookback = int(params.get("rvol_lookback", 120))
        rvol_threshold = float(params.get("rvol_threshold", 2.0))
        recent_peak_window = int(params.get("recent_peak_window", 0))
        recent_peak_threshold = float(params.get("recent_peak_threshold", rvol_threshold))
        vol_lookback = int(params.get("vol_lookback", 60))
        max_trailing_volatility = params.get("max_trailing_volatility")
        max_trailing_volatility = (
            float(max_trailing_volatility)
            if max_trailing_volatility is not None
            else None
        )
        holding_window_days = int(params.get("holding_window_days", 30))
        regime_symbol = str(params.get("regime_symbol", "SPY"))
        regime_mode = str(params.get("regime_mode", "benchmark_ema"))
        regime_fast_ema = int(params.get("regime_fast_ema", 120))
        regime_slow_ema = int(params.get("regime_slow_ema", 144))
        regime_slope_lookback = int(params.get("regime_slope_lookback", 20))
        regime_breadth_threshold = float(params.get("regime_breadth_threshold", 0.55))
        confirm_mode = str(params.get("confirm_mode", "hard"))
        confirm_floor = float(params.get("confirm_floor", 0.0))
        score_mode = str(params.get("score_mode", "signal_rvol"))

        target_dates = pd.Index(
            sorted(universe_df["date"].astype(str).unique().tolist()),
            name="date",
        )
        symbols = sorted(universe_df["symbol"].astype(str).unique().tolist())
        price_dict = pit_data.bulk_history(symbols, end_date=str(target_dates.max()))
        close_panel = pd.DataFrame(
            {
                symbol: frame.set_index(frame["date"].astype(str))["close"].astype(float)
                for symbol, frame in price_dict.items()
                if not frame.empty
            }
        ).sort_index()
        regime_by_date = self._resolve_regime_map(
            pit_data=pit_data,
            universe_df=universe_df,
            price_dict=price_dict,
            close_panel=close_panel,
            end_date=str(target_dates.max()),
            regime_mode=regime_mode,
            regime_symbol=regime_symbol,
            fast_ema=regime_fast_ema,
            slow_ema=regime_slow_ema,
            slope_lookback=regime_slope_lookback,
            breadth_threshold=regime_breadth_threshold,
        )

        panel: Dict[str, pd.Series] = {}
        for symbol, frame in price_dict.items():
            series = self._build_symbol_panel(
                frame=frame,
                regime_by_date=regime_by_date,
                trigger_threshold=trigger_threshold,
                rvol_lookback=rvol_lookback,
                rvol_threshold=rvol_threshold,
                recent_peak_window=recent_peak_window,
                recent_peak_threshold=recent_peak_threshold,
                vol_lookback=vol_lookback,
                max_trailing_volatility=max_trailing_volatility,
                confirm_mode=confirm_mode,
                confirm_floor=confirm_floor,
                pmarp_ema_period=pmarp_ema_period,
                pmarp_lookback=pmarp_lookback,
                holding_window_days=holding_window_days,
                score_mode=score_mode,
            )
            if series is None:
                continue
            panel[symbol] = series.reindex(target_dates)

        if not panel:
            return pd.DataFrame(index=target_dates)
        return pd.DataFrame(panel, index=target_dates).sort_index()

    def compute(
        self,
        pit_data: PitData,
        symbols: List[str],
        as_of_date: str,
        params: Dict[str, Any],
    ) -> Dict[str, float]:
        trigger_threshold = float(params.get("trigger_threshold", 2.0))
        pmarp_ema_period = int(params.get("pmarp_ema_period", 20))
        pmarp_lookback = int(params.get("pmarp_lookback", 150))
        rvol_lookback = int(params.get("rvol_lookback", 120))
        rvol_threshold = float(params.get("rvol_threshold", 2.0))
        recent_peak_window = int(params.get("recent_peak_window", 0))
        recent_peak_threshold = float(params.get("recent_peak_threshold", rvol_threshold))
        vol_lookback = int(params.get("vol_lookback", 60))
        max_trailing_volatility = params.get("max_trailing_volatility")
        max_trailing_volatility = (
            float(max_trailing_volatility)
            if max_trailing_volatility is not None
            else None
        )
        holding_window_days = int(params.get("holding_window_days", 30))
        regime_symbol = str(params.get("regime_symbol", "SPY"))
        regime_mode = str(params.get("regime_mode", "benchmark_ema"))
        regime_fast_ema = int(params.get("regime_fast_ema", 120))
        regime_slow_ema = int(params.get("regime_slow_ema", 144))
        regime_slope_lookback = int(params.get("regime_slope_lookback", 20))
        confirm_mode = str(params.get("confirm_mode", "hard"))
        confirm_floor = float(params.get("confirm_floor", 0.0))
        score_mode = str(params.get("score_mode", "signal_rvol"))
        if regime_mode != "benchmark_ema":
            raise ValueError(
                "compute() only supports regime_mode='benchmark_ema'; pipeline batch runs should use compute_panel()."
            )

        lookback_days = max(
            pmarp_ema_period + pmarp_lookback + holding_window_days + 5,
            rvol_lookback + holding_window_days + 5,
            regime_slow_ema + regime_slope_lookback + holding_window_days + 5,
        )
        price_dict = pit_data.bulk_price_windows(
            symbols,
            end_date=as_of_date,
            lookback_days=lookback_days,
            min_rows=max(pmarp_ema_period + pmarp_lookback, rvol_lookback + 1),
        )
        regime_window = pit_data.window(
            regime_symbol,
            end_date=as_of_date,
            lookback_days=lookback_days,
        )
        regime_by_date = self._build_regime_map(
            regime_window,
            fast_ema=regime_fast_ema,
            slow_ema=regime_slow_ema,
            slope_lookback=regime_slope_lookback,
        )

        scores: Dict[str, float] = {}
        for symbol, frame in price_dict.items():
            score = self._score_symbol(
                frame=frame,
                regime_by_date=regime_by_date,
                trigger_threshold=trigger_threshold,
                rvol_lookback=rvol_lookback,
                rvol_threshold=rvol_threshold,
                recent_peak_window=recent_peak_window,
                recent_peak_threshold=recent_peak_threshold,
                vol_lookback=vol_lookback,
                max_trailing_volatility=max_trailing_volatility,
                confirm_mode=confirm_mode,
                confirm_floor=confirm_floor,
                pmarp_ema_period=pmarp_ema_period,
                pmarp_lookback=pmarp_lookback,
                holding_window_days=holding_window_days,
                score_mode=score_mode,
            )
            if score is not None:
                scores[symbol] = score
        return scores

    def _build_regime_map(
        self,
        frame: pd.DataFrame,
        fast_ema: int,
        slow_ema: int,
        slope_lookback: int,
    ) -> Dict[str, bool]:
        if frame.empty or len(frame) < max(fast_ema, slow_ema) + slope_lookback:
            return {}

        ordered = frame.sort_values("date").reset_index(drop=True).copy()
        close = ordered["close"].astype(float)
        ema_fast = close.ewm(span=fast_ema, adjust=False).mean()
        ema_slow = close.ewm(span=slow_ema, adjust=False).mean()
        fast_slope = ema_fast - ema_fast.shift(slope_lookback)

        passed = (
            (close > ema_fast)
            & (ema_fast > ema_slow)
            & (fast_slope > 0)
        )
        return {
            str(date_value): bool(flag)
            for date_value, flag in zip(ordered["date"], passed.fillna(False))
        }

    def _resolve_regime_map(
        self,
        pit_data: PitData,
        universe_df: pd.DataFrame,
        price_dict: Dict[str, pd.DataFrame],
        close_panel: pd.DataFrame,
        end_date: str,
        regime_mode: str,
        regime_symbol: str,
        fast_ema: int,
        slow_ema: int,
        slope_lookback: int,
        breadth_threshold: float,
    ) -> Dict[str, bool]:
        if regime_mode == "benchmark_ema":
            regime_history = pit_data.history(regime_symbol, end_date=end_date)
            return self._build_regime_map(
                regime_history,
                fast_ema=fast_ema,
                slow_ema=slow_ema,
                slope_lookback=slope_lookback,
            )
        if regime_mode == "universe_equal_weight_ema":
            return self._build_equal_weight_regime_map(
                close_panel=close_panel,
                universe_df=universe_df,
                fast_ema=fast_ema,
                slow_ema=slow_ema,
                slope_lookback=slope_lookback,
            )
        if regime_mode == "universe_breadth":
            return self._build_breadth_regime_map(
                price_dict=price_dict,
                universe_df=universe_df,
                fast_ema=fast_ema,
                slow_ema=slow_ema,
                breadth_threshold=breadth_threshold,
            )
        raise ValueError(f"Unsupported regime_mode: {regime_mode}")

    def _build_equal_weight_regime_map(
        self,
        close_panel: pd.DataFrame,
        universe_df: pd.DataFrame,
        fast_ema: int,
        slow_ema: int,
        slope_lookback: int,
    ) -> Dict[str, bool]:
        if close_panel.empty or universe_df.empty:
            return {}

        active_mask = (
            universe_df.assign(active=True)
            .pivot(index="date", columns="symbol", values="active")
            .fillna(False)
        )
        active_mask.index = active_mask.index.astype(str)
        active_mask.columns = active_mask.columns.astype(str)
        returns = close_panel.pct_change()
        common_dates = returns.index.intersection(active_mask.index)
        common_symbols = returns.columns.intersection(active_mask.columns)
        if len(common_dates) == 0 or len(common_symbols) == 0:
            return {}

        returns = returns.loc[common_dates, common_symbols]
        active_mask = active_mask.loc[common_dates, common_symbols]
        masked_returns = returns.where(active_mask)
        ew_returns = masked_returns.mean(axis=1).fillna(0.0)
        synthetic_close = 100.0 * (1.0 + ew_returns).cumprod()
        regime_frame = pd.DataFrame(
            {"date": synthetic_close.index.astype(str), "close": synthetic_close.values}
        )
        return self._build_regime_map(
            regime_frame,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            slope_lookback=slope_lookback,
        )

    def _build_breadth_regime_map(
        self,
        price_dict: Dict[str, pd.DataFrame],
        universe_df: pd.DataFrame,
        fast_ema: int,
        slow_ema: int,
        breadth_threshold: float,
    ) -> Dict[str, bool]:
        if not price_dict or universe_df.empty:
            return {}

        trend_states: Dict[str, pd.Series] = {}
        for symbol, frame in price_dict.items():
            ordered = frame.sort_values("date").reset_index(drop=True)
            close = ordered["close"].astype(float)
            ema_fast = close.ewm(span=fast_ema, adjust=False).mean()
            ema_slow = close.ewm(span=slow_ema, adjust=False).mean()
            state = ((close > ema_fast) & (ema_fast > ema_slow)).astype(float)
            trend_states[symbol] = pd.Series(state.values, index=ordered["date"].astype(str))

        state_panel = pd.DataFrame(trend_states).sort_index()
        active_mask = (
            universe_df.assign(active=True)
            .pivot(index="date", columns="symbol", values="active")
            .fillna(False)
        )
        active_mask.index = active_mask.index.astype(str)
        active_mask.columns = active_mask.columns.astype(str)
        common_dates = state_panel.index.intersection(active_mask.index)
        common_symbols = state_panel.columns.intersection(active_mask.columns)
        if len(common_dates) == 0 or len(common_symbols) == 0:
            return {}

        state_panel = state_panel.loc[common_dates, common_symbols]
        active_mask = active_mask.loc[common_dates, common_symbols]
        breadth = state_panel.where(active_mask).mean(axis=1)
        passed = (breadth >= breadth_threshold).fillna(False)
        return passed.astype(bool).to_dict()

    def _score_symbol(
        self,
        frame: pd.DataFrame,
        regime_by_date: Dict[str, bool],
        trigger_threshold: float,
        rvol_lookback: int,
        rvol_threshold: float,
        recent_peak_window: int,
        recent_peak_threshold: float,
        vol_lookback: int,
        max_trailing_volatility: Optional[float],
        confirm_mode: str,
        confirm_floor: float,
        pmarp_ema_period: int,
        pmarp_lookback: int,
        holding_window_days: int,
        score_mode: str,
    ) -> Optional[float]:
        ordered = frame.sort_values("date").reset_index(drop=True).copy()
        if len(ordered) < max(pmarp_ema_period + pmarp_lookback, rvol_lookback + 1):
            return None

        pmarp_series = calculate_pmarp(
            ordered["close"].astype(float),
            ema_period=pmarp_ema_period,
            lookback=pmarp_lookback,
        )
        rvol_series = calculate_rvol_series(
            ordered["volume"].astype(float),
            lookback=rvol_lookback,
        )
        vol_series = (
            ordered["close"].astype(float).pct_change().rolling(vol_lookback).std() * (252 ** 0.5)
        )
        confirm_mask, trigger_strength = self._build_confirm_context(
            rvol_series=rvol_series,
            rvol_threshold=rvol_threshold,
            recent_peak_window=recent_peak_window,
            recent_peak_threshold=recent_peak_threshold,
            confirm_mode=confirm_mode,
            confirm_floor=confirm_floor,
        )

        start_idx = max(1, len(ordered) - holding_window_days - 1)
        latest_signal_idx: Optional[int] = None
        latest_signal_rvol: Optional[float] = None

        for idx in range(start_idx, len(ordered)):
            prev_value = pmarp_series.iloc[idx - 1]
            curr_value = pmarp_series.iloc[idx]
            signal_strength = trigger_strength.iloc[idx]
            if pd.isna(prev_value) or pd.isna(curr_value) or pd.isna(signal_strength):
                continue
            if not (prev_value <= trigger_threshold < curr_value):
                continue

            date_str = str(ordered.iloc[idx]["date"])
            if not regime_by_date.get(date_str, False):
                continue
            if not bool(confirm_mask.iloc[idx]):
                continue
            if max_trailing_volatility is not None:
                vol_value = vol_series.iloc[idx]
                if pd.isna(vol_value) or float(vol_value) > max_trailing_volatility:
                    continue

            latest_signal_idx = idx
            latest_signal_rvol = float(signal_strength)

        if latest_signal_idx is None or latest_signal_rvol is None:
            return None

        age = len(ordered) - 1 - latest_signal_idx
        if age > holding_window_days:
            return None

        current_rvol = rvol_series.iloc[-1]
        if pd.isna(current_rvol):
            current_rvol = latest_signal_rvol
        current_rvol = float(current_rvol)

        if score_mode == "signal_rvol":
            return latest_signal_rvol
        if score_mode == "current_rvol":
            return current_rvol
        if score_mode == "binary":
            return 1.0
        if score_mode == "recency_weighted":
            return latest_signal_rvol * (1.0 - age / max(holding_window_days + 1, 1))
        raise ValueError(f"Unsupported score_mode: {score_mode}")

    def _build_symbol_panel(
        self,
        frame: pd.DataFrame,
        regime_by_date: Dict[str, bool],
        trigger_threshold: float,
        rvol_lookback: int,
        rvol_threshold: float,
        recent_peak_window: int,
        recent_peak_threshold: float,
        vol_lookback: int,
        max_trailing_volatility: Optional[float],
        confirm_mode: str,
        confirm_floor: float,
        pmarp_ema_period: int,
        pmarp_lookback: int,
        holding_window_days: int,
        score_mode: str,
    ) -> Optional[pd.Series]:
        ordered = frame.sort_values("date").reset_index(drop=True).copy()
        min_rows = max(pmarp_ema_period + pmarp_lookback, rvol_lookback + 1)
        if len(ordered) < min_rows:
            return None

        close = ordered["close"].astype(float)
        volume = ordered["volume"].astype(float)
        pmarp_series = calculate_pmarp(
            close,
            ema_period=pmarp_ema_period,
            lookback=pmarp_lookback,
        )
        rvol_series = calculate_rvol_series(
            volume,
            lookback=rvol_lookback,
        )
        vol_series = close.pct_change().rolling(vol_lookback).std() * (252 ** 0.5)
        regime_series = ordered["date"].astype(str).map(regime_by_date).fillna(False)
        confirm_mask, trigger_strength = self._build_confirm_context(
            rvol_series=rvol_series,
            rvol_threshold=rvol_threshold,
            recent_peak_window=recent_peak_window,
            recent_peak_threshold=recent_peak_threshold,
            confirm_mode=confirm_mode,
            confirm_floor=confirm_floor,
        )
        trigger_mask = (
            (pmarp_series.shift(1) <= trigger_threshold)
            & (pmarp_series > trigger_threshold)
            & confirm_mask
            & regime_series
        )
        if max_trailing_volatility is not None:
            trigger_mask = trigger_mask & (vol_series <= max_trailing_volatility).fillna(False)

        trigger_values = pd.Series(float("nan"), index=ordered.index, dtype=float)
        trigger_values.loc[trigger_mask] = trigger_strength.loc[trigger_mask].astype(float)
        carried_signal = trigger_values.ffill(limit=holding_window_days)
        active_mask = carried_signal.notna()

        if score_mode == "signal_rvol":
            score_series = carried_signal
        elif score_mode == "current_rvol":
            score_series = rvol_series.where(active_mask)
        elif score_mode == "binary":
            score_series = active_mask.astype(float).where(active_mask)
        elif score_mode == "recency_weighted":
            last_trigger_idx = pd.Series(float("nan"), index=ordered.index, dtype=float)
            last_trigger_idx.loc[trigger_mask] = ordered.index[trigger_mask].astype(float)
            last_trigger_idx = last_trigger_idx.ffill(limit=holding_window_days)
            age = ordered.index.to_series().astype(float) - last_trigger_idx
            score_series = carried_signal * (1.0 - age / max(holding_window_days + 1, 1))
        else:
            raise ValueError(f"Unsupported score_mode: {score_mode}")

        score_series.index = ordered["date"].astype(str)
        return score_series

    def _build_confirm_context(
        self,
        rvol_series: pd.Series,
        rvol_threshold: float,
        recent_peak_window: int,
        recent_peak_threshold: float,
        confirm_mode: str,
        confirm_floor: float,
    ) -> tuple[pd.Series, pd.Series]:
        if confirm_mode == "hard":
            confirm_mask = rvol_series >= rvol_threshold
            trigger_strength = rvol_series.astype(float)
        elif confirm_mode == "soft":
            confirm_mask = rvol_series >= confirm_floor
            trigger_strength = rvol_series.astype(float)
        elif confirm_mode == "recent_peak_soft":
            if recent_peak_window <= 0:
                raise ValueError(
                    "recent_peak_window must be positive for confirm_mode='recent_peak_soft'"
                )
            recent_peak = rvol_series.shift(1).rolling(recent_peak_window, min_periods=1).max()
            confirm_mask = (recent_peak >= recent_peak_threshold) & (rvol_series >= confirm_floor)
            trigger_strength = recent_peak.astype(float)
        else:
            raise ValueError(f"Unsupported confirm_mode: {confirm_mode}")

        return confirm_mask.fillna(False), trigger_strength
