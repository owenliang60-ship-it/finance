from __future__ import annotations

import math
from dataclasses import asdict
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, t as t_dist

from backtest.factor_study.forward_returns import (
    build_excess_return_matrix,
    build_return_matrix,
)
from backtest.metrics import compute_metrics
from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.report import build_report_html, build_report_markdown
from backtest.pipeline.spec import StrategySpec
from backtest.pipeline.types import BacktestRunResult, EvaluationOutput


class EvaluationEngine:
    def __init__(self, pit_data: PitData):
        self.pit_data = pit_data

    def evaluate(
        self,
        spec: StrategySpec,
        factor_frames: Dict[str, pd.DataFrame],
        combo_frame: pd.DataFrame,
        run_is: BacktestRunResult,
        run_oos: BacktestRunResult,
        warnings: List[str],
    ) -> EvaluationOutput:
        lag = spec.resolved_newey_west_lag_days()
        is_frames = {
            name: self._slice_frame(frame, None, spec.period.train_end.isoformat())
            for name, frame in {"combo": combo_frame, **factor_frames}.items()
        }
        oos_frames = {
            name: self._slice_frame(
                frame,
                spec.period.train_end.isoformat(),
                spec.period.test_end.isoformat(),
            )
            for name, frame in {"combo": combo_frame, **factor_frames}.items()
        }

        factor_metrics = {
            "is": self._evaluate_signal_bundle(
                frames=is_frames,
                benchmark_symbol=spec.benchmark,
                rebalance=spec.portfolio.rebalance,
                nw_lag_days=lag,
            ),
            "oos": self._evaluate_signal_bundle(
                frames=oos_frames,
                benchmark_symbol=spec.benchmark,
                rebalance=spec.portfolio.rebalance,
                nw_lag_days=lag,
            ),
        }

        strategy_metrics = {
            "is": self._evaluate_backtest_run(run_is),
            "oos": self._evaluate_backtest_run(run_oos),
        }
        metrics = {
            "factor": factor_metrics,
            "strategy": strategy_metrics,
            "gates": self._build_gates(
                strategy_is=strategy_metrics["is"],
                strategy_oos=strategy_metrics["oos"],
                factor_oos=factor_metrics["oos"].get("combo", {}),
                max_annual_turnover=spec.portfolio.max_annual_turnover,
            ),
        }
        return EvaluationOutput(
            metrics=metrics,
            report_markdown=build_report_markdown(spec.to_dict(), metrics, warnings),
            report_html=build_report_html(spec.to_dict(), metrics, warnings),
        )

    def _evaluate_signal_bundle(
        self,
        frames: Dict[str, pd.DataFrame],
        benchmark_symbol: str,
        rebalance: str,
        nw_lag_days: int,
    ) -> Dict[str, Dict[str, object]]:
        result: Dict[str, Dict[str, object]] = {}
        for name, frame in frames.items():
            metrics = self._evaluate_signal_frame(
                frame=frame,
                benchmark_symbol=benchmark_symbol,
                rebalance=rebalance,
                nw_lag_days=nw_lag_days,
            )
            if metrics:
                result[name] = metrics
        return result

    def _evaluate_signal_frame(
        self,
        frame: pd.DataFrame,
        benchmark_symbol: str,
        rebalance: str,
        nw_lag_days: int,
    ) -> Dict[str, object]:
        frame = frame.sort_index()
        if frame.empty:
            return {}

        computation_dates = [str(value) for value in frame.index.tolist()]
        symbols = [str(symbol) for symbol in frame.columns.tolist()]
        if not computation_dates or not symbols:
            return {}

        start_date = computation_dates[0]
        end_date = computation_dates[-1]
        price_dict = self.pit_data.bulk_history(symbols, start_date=start_date, end_date=end_date)
        if not price_dict:
            return {}

        horizons = self._horizons(rebalance)
        raw_returns = build_return_matrix(price_dict, computation_dates, horizons)
        benchmark_df = self.pit_data.benchmark_prices(benchmark_symbol, start_date, end_date)
        excess_returns = (
            build_excess_return_matrix(price_dict, benchmark_df, computation_dates, horizons)
            if not benchmark_df.empty
            else raw_returns
        )

        by_horizon: Dict[str, Dict[str, float | int]] = {}
        ic_decay: Dict[str, float] = {}

        for horizon in horizons:
            stats = self._cross_sectional_stats(
                score_frame=frame,
                return_frame=raw_returns[horizon],
                excess_return_frame=excess_returns[horizon],
                nw_lag_days=nw_lag_days,
            )
            if not stats:
                continue
            by_horizon[str(horizon)] = stats
            ic_decay[str(horizon)] = float(stats["ic_mean"])

        if not by_horizon:
            return {}

        primary_horizon = str(horizons[0])
        primary = by_horizon.get(primary_horizon, next(iter(by_horizon.values())))
        return {
            "primary_horizon": int(primary_horizon),
            "ic_mean": float(primary["ic_mean"]),
            "ic_tstat": float(primary["ic_tstat"]),
            "top_bottom_spread": float(primary["top_bottom_spread"]),
            "top_decile_excess_return": float(primary["top_decile_excess_return"]),
            "ic_decay": ic_decay,
            "by_horizon": by_horizon,
        }

    def _cross_sectional_stats(
        self,
        score_frame: pd.DataFrame,
        return_frame: pd.DataFrame,
        excess_return_frame: pd.DataFrame,
        nw_lag_days: int,
    ) -> Dict[str, float | int]:
        common_dates = [date for date in score_frame.index if date in return_frame.index]
        ic_series: List[float] = []
        top_bottom_spreads: List[float] = []
        top_excess_returns: List[float] = []

        for date in common_dates:
            scores = score_frame.loc[date]
            raw_returns = return_frame.loc[date]
            excess_returns = excess_return_frame.loc[date]

            mask = scores.notna() & raw_returns.notna() & excess_returns.notna()
            s = scores[mask].astype(float)
            r = raw_returns[mask].astype(float)
            ex = excess_returns[mask].astype(float)
            if len(s) < 2:
                continue

            corr, _ = spearmanr(s.values, r.values)
            if corr is not None and not math.isnan(float(corr)):
                ic_series.append(float(corr))

            n_quantiles = min(10, len(s))
            if n_quantiles < 2:
                continue
            try:
                labels = pd.qcut(
                    s.rank(method="first"),
                    q=n_quantiles,
                    labels=range(1, n_quantiles + 1),
                )
            except ValueError:
                continue

            top = r[labels == n_quantiles]
            bottom = r[labels == 1]
            top_excess = ex[labels == n_quantiles]
            if not top.empty and not bottom.empty:
                top_bottom_spreads.append(float(top.mean() - bottom.mean()))
            if not top_excess.empty:
                top_excess_returns.append(float(top_excess.mean()))

        if not ic_series:
            return {}

        t_stat, p_value = newey_west_tstat(ic_series, nw_lag_days)
        return {
            "ic_mean": float(np.mean(ic_series)),
            "ic_tstat": t_stat,
            "p_value": p_value,
            "n_ic_obs": len(ic_series),
            "top_bottom_spread": float(np.mean(top_bottom_spreads)) if top_bottom_spreads else 0.0,
            "top_decile_excess_return": float(np.mean(top_excess_returns)) if top_excess_returns else 0.0,
        }

    def _evaluate_backtest_run(
        self,
        run: BacktestRunResult,
    ) -> Dict[str, float | int]:
        nav_series = list(run.nav.itertuples(index=False, name=None)) if not run.nav.empty else []
        benchmark_series = (
            list(run.benchmark_nav.itertuples(index=False, name=None))
            if not run.benchmark_nav.empty
            else None
        )
        metrics = compute_metrics(
            nav_series=nav_series,
            benchmark_nav=benchmark_series,
            total_costs=run.total_costs,
            n_trades=run.n_trades,
            annual_turnover=run.annual_turnover,
        )
        payload = asdict(metrics)

        benchmark_cagr = 0.0
        if benchmark_series:
            benchmark_cagr = compute_metrics(benchmark_series).cagr
        payload["benchmark_cagr"] = benchmark_cagr
        payload["excess_cagr"] = payload["cagr"] - benchmark_cagr
        payload["ir"] = payload["information_ratio"]
        return payload

    def _build_gates(
        self,
        strategy_is: Dict[str, float | int],
        strategy_oos: Dict[str, float | int],
        factor_oos: Dict[str, object],
        max_annual_turnover: Optional[float],
    ) -> Dict[str, Dict[str, object]]:
        is_sharpe = float(strategy_is.get("sharpe_ratio", 0.0))
        oos_sharpe = float(strategy_oos.get("sharpe_ratio", 0.0))
        oos_ic = float(factor_oos.get("ic_mean", 0.0)) if factor_oos else 0.0
        turnover = float(strategy_oos.get("annual_turnover", 0.0))
        ratio = oos_sharpe / is_sharpe if abs(is_sharpe) > 1e-9 else None
        if is_sharpe <= 0:
            ratio_pass = oos_sharpe > 0
        else:
            ratio_pass = ratio is not None and ratio >= 0.5

        gates: Dict[str, Dict[str, object]] = {
            "is_sharpe_positive": {"value": is_sharpe, "pass": is_sharpe > 0},
            "oos_sharpe_positive": {"value": oos_sharpe, "pass": oos_sharpe > 0},
            "oos_ic_positive": {"value": oos_ic, "pass": oos_ic > 0},
            "oos_vs_is_sharpe_ratio_gte_0_5": {
                "value": ratio,
                "threshold": 0.5,
                "pass": ratio_pass,
            },
        }
        if max_annual_turnover is None:
            gates["annual_turnover_within_limit"] = {
                "value": turnover,
                "threshold": None,
                "pass": "n/a",
            }
        else:
            gates["annual_turnover_within_limit"] = {
                "value": turnover,
                "threshold": max_annual_turnover,
                "pass": turnover <= max_annual_turnover,
            }
        return gates

    def _horizons(self, rebalance: str) -> List[int]:
        if rebalance == "weekly":
            return [5, 10, 21]
        return [21, 42, 63]

    def _slice_frame(
        self,
        frame: pd.DataFrame,
        after_date: Optional[str],
        end_date: Optional[str],
    ) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        sliced = frame.copy()
        index_series = pd.Series(sliced.index.astype(str), index=sliced.index)
        if after_date is not None:
            sliced = sliced.loc[index_series > after_date]
            index_series = pd.Series(sliced.index.astype(str), index=sliced.index)
        if end_date is not None:
            sliced = sliced.loc[index_series <= end_date]
        return sliced


def newey_west_tstat(values: Iterable[float], lag: int) -> tuple[float, float]:
    series = np.asarray(list(values), dtype=float)
    if len(series) < 2:
        return 0.0, 1.0

    lag = max(0, min(int(lag), len(series) - 1))
    mean = float(series.mean())
    centered = series - mean

    gamma0 = float(np.dot(centered, centered) / len(series))
    long_run_var = gamma0
    for k in range(1, lag + 1):
        gamma = float(np.dot(centered[k:], centered[:-k]) / len(series))
        weight = 1.0 - (k / (lag + 1))
        long_run_var += 2.0 * weight * gamma

    if long_run_var <= 1e-12:
        return 0.0, 1.0

    se = math.sqrt(long_run_var / len(series))
    if se <= 1e-12:
        return 0.0, 1.0

    t_stat = mean / se
    p_value = float(t_dist.sf(abs(t_stat), df=len(series) - 1) * 2.0)
    return float(t_stat), p_value
