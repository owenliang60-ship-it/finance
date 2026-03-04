"""
Slow-lane evaluator:
- turn candidate factor into a tradable Top-N strategy
- run no-lookahead simulation with delayed activation (t signal -> t+1 active)
- compare against SPY/QQQ/VOO with unified metrics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from backtest.config import FREQ_DAYS
from backtest.metrics import TRADING_DAYS_PER_YEAR, compute_metrics
from factor_research_codex1.evaluation.expression_engine import DSLExpressionEngine
from factor_research_codex1.miner.generator import CandidateFactor


@dataclass
class SlowLaneConfig:
    """Config for slow-lane strategy validation."""

    top_n: int = 20
    rebalance_freq: str = "W"
    weighting: str = "equal"  # equal | score_weighted
    transaction_cost_bps: float = 5.0
    initial_capital: float = 1_000_000.0
    benchmarks: List[str] = field(default_factory=lambda: ["SPY", "QQQ", "VOO"])

    @property
    def cost_rate(self) -> float:
        return self.transaction_cost_bps / 10_000.0


@dataclass
class BenchmarkComparison:
    """Relative performance against one benchmark."""

    benchmark: str
    benchmark_total_return: float
    benchmark_cagr: float
    excess_total_return: float
    excess_cagr: float
    alpha: float
    beta: float
    information_ratio: float
    tracking_error: float


@dataclass
class CandidateSlowMetrics:
    """Slow-lane summary for a candidate."""

    candidate_id: str
    expression: str
    normalized_expression: str
    hypothesis_id: str
    source: str
    n_days: int
    n_rebalances: int
    n_trades: int
    first_signal_date: str
    first_activation_date: str
    strategy_total_return: float
    strategy_cagr: float
    strategy_sharpe: float
    strategy_max_drawdown: float
    strategy_annual_turnover: float
    strategy_total_costs: float
    benchmark_comparisons: List[BenchmarkComparison] = field(default_factory=list)
    avg_excess_cagr: float = 0.0
    avg_information_ratio: float = 0.0
    quality_score: float = 0.0


class SlowLaneEvaluator:
    """
    Strategy-level validation for factor candidates.

    Adapter interface expected:
    - load_all()
    - get_trading_dates()
    - slice_to_date(date)
    - get_prices_at(date)
    - get_benchmark_nav(symbol)
    """

    def __init__(
        self,
        adapter,
        config: Optional[SlowLaneConfig] = None,
        engine: Optional[DSLExpressionEngine] = None,
    ):
        self.adapter = adapter
        self.config = config or SlowLaneConfig()
        self.engine = engine or DSLExpressionEngine()

    def evaluate_candidates(
        self,
        candidates: Sequence[CandidateFactor],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_candidates: Optional[int] = None,
    ) -> List[CandidateSlowMetrics]:
        """
        Evaluate a list of candidates with strategy simulation + benchmark comparison.
        """
        if not candidates:
            return []

        # ensure data cache populated
        self.adapter.load_all()
        dates = self.adapter.get_trading_dates()
        if start_date:
            dates = [d for d in dates if d >= start_date]
        if end_date:
            dates = [d for d in dates if d <= end_date]
        if len(dates) < 3:
            return []

        freq_days = FREQ_DAYS.get(self.config.rebalance_freq, 5)
        signal_dates = dates[::freq_days]
        date_to_idx = {d: i for i, d in enumerate(dates)}
        signal_to_activation: Dict[str, str] = {}
        for s in signal_dates:
            idx = date_to_idx[s]
            if idx + 1 < len(dates):
                signal_to_activation[s] = dates[idx + 1]

        # Preload shared context once for all candidates.
        signal_slices = {s: self.adapter.slice_to_date(s) for s in signal_dates}
        price_cache = {d: self.adapter.get_prices_at(d) for d in dates}
        benchmark_cache = self._prefetch_benchmarks()

        target = candidates[:max_candidates] if max_candidates is not None else candidates
        results: List[CandidateSlowMetrics] = []
        for c in target:
            results.append(
                self._evaluate_single(
                    candidate=c,
                    trading_dates=dates,
                    signal_dates=signal_dates,
                    signal_to_activation=signal_to_activation,
                    signal_slices=signal_slices,
                    price_cache=price_cache,
                    benchmark_cache=benchmark_cache,
                )
            )

        self._attach_quality_scores(results)
        results.sort(key=lambda x: x.quality_score, reverse=True)
        return results

    def to_dataframe(self, metrics: Sequence[CandidateSlowMetrics]) -> pd.DataFrame:
        """Convert slow-lane metrics to DataFrame."""
        rows = []
        for m in metrics:
            row = {
                "candidate_id": m.candidate_id,
                "expression": m.expression,
                "normalized_expression": m.normalized_expression,
                "hypothesis_id": m.hypothesis_id,
                "source": m.source,
                "n_days": m.n_days,
                "n_rebalances": m.n_rebalances,
                "n_trades": m.n_trades,
                "first_signal_date": m.first_signal_date,
                "first_activation_date": m.first_activation_date,
                "strategy_total_return": m.strategy_total_return,
                "strategy_cagr": m.strategy_cagr,
                "strategy_sharpe": m.strategy_sharpe,
                "strategy_max_drawdown": m.strategy_max_drawdown,
                "strategy_annual_turnover": m.strategy_annual_turnover,
                "strategy_total_costs": m.strategy_total_costs,
                "avg_excess_cagr": m.avg_excess_cagr,
                "avg_information_ratio": m.avg_information_ratio,
                "quality_score": m.quality_score,
            }
            for bc in m.benchmark_comparisons:
                key = bc.benchmark.lower()
                row[f"{key}_excess_cagr"] = bc.excess_cagr
                row[f"{key}_information_ratio"] = bc.information_ratio
                row[f"{key}_alpha"] = bc.alpha
                row[f"{key}_beta"] = bc.beta
            rows.append(row)
        return pd.DataFrame(rows)

    def _evaluate_single(
        self,
        candidate: CandidateFactor,
        trading_dates: List[str],
        signal_dates: List[str],
        signal_to_activation: Dict[str, str],
        signal_slices: Dict[str, Dict[str, pd.DataFrame]],
        price_cache: Dict[str, Dict[str, float]],
        benchmark_cache: Dict[str, List[Tuple[str, float]]],
    ) -> CandidateSlowMetrics:
        nav_series, n_rebalances, n_trades, total_costs, annual_turnover, first_signal, first_activation = (
            self._simulate_strategy(
                expression=candidate.normalized_expression,
                trading_dates=trading_dates,
                signal_dates=signal_dates,
                signal_to_activation=signal_to_activation,
                signal_slices=signal_slices,
                price_cache=price_cache,
            )
        )

        strat_metrics = compute_metrics(
            nav_series=nav_series,
            total_costs=total_costs,
            n_trades=n_trades,
            annual_turnover=annual_turnover,
            days_per_year=TRADING_DAYS_PER_YEAR,
        )

        comparisons = self._evaluate_benchmarks(nav_series, benchmark_cache)
        avg_excess_cagr = float(np.mean([x.excess_cagr for x in comparisons])) if comparisons else 0.0
        avg_ir = float(np.mean([x.information_ratio for x in comparisons])) if comparisons else 0.0

        return CandidateSlowMetrics(
            candidate_id=candidate.candidate_id,
            expression=candidate.expression,
            normalized_expression=candidate.normalized_expression,
            hypothesis_id=candidate.hypothesis_id,
            source=candidate.source,
            n_days=len(nav_series),
            n_rebalances=n_rebalances,
            n_trades=n_trades,
            first_signal_date=first_signal,
            first_activation_date=first_activation,
            strategy_total_return=float(strat_metrics.total_return),
            strategy_cagr=float(strat_metrics.cagr),
            strategy_sharpe=float(strat_metrics.sharpe_ratio),
            strategy_max_drawdown=float(strat_metrics.max_drawdown),
            strategy_annual_turnover=float(strat_metrics.annual_turnover),
            strategy_total_costs=float(strat_metrics.total_costs),
            benchmark_comparisons=comparisons,
            avg_excess_cagr=avg_excess_cagr,
            avg_information_ratio=avg_ir,
        )

    def _simulate_strategy(
        self,
        expression: str,
        trading_dates: List[str],
        signal_dates: List[str],
        signal_to_activation: Dict[str, str],
        signal_slices: Dict[str, Dict[str, pd.DataFrame]],
        price_cache: Dict[str, Dict[str, float]],
    ) -> Tuple[List[Tuple[str, float]], int, int, float, float, str, str]:
        # Signal date -> activated weights (at t+1)
        activated_weights: Dict[str, Dict[str, float]] = {}
        for s in signal_dates:
            activation_date = signal_to_activation.get(s)
            if not activation_date:
                continue

            sliced = signal_slices.get(s, {})
            if not sliced:
                continue
            result = self.engine.evaluate(expression, sliced)
            if not result.is_valid or not result.scores:
                continue
            weights = self._build_target_weights(result.scores)
            if weights:
                activated_weights[activation_date] = weights

        first_signal = signal_dates[0] if signal_dates else ""
        first_activation = min(activated_weights.keys()) if activated_weights else ""

        nav_series: List[Tuple[str, float]] = [(trading_dates[0], self.config.initial_capital)]
        current_weights: Dict[str, float] = {}
        total_costs = 0.0
        turnover_notional = 0.0
        n_rebalances = 0
        n_trades = 0

        for i in range(1, len(trading_dates)):
            date = trading_dates[i]
            prev_date = trading_dates[i - 1]
            prev_nav = nav_series[-1][1]

            # Rebalance at activation day, based on previous signal.
            if date in activated_weights:
                target_weights = activated_weights[date]
                trade_fraction, trade_count = self._compute_trade_fraction(current_weights, target_weights)
                if trade_fraction > 0:
                    n_rebalances += 1
                    n_trades += trade_count
                    traded_notional = prev_nav * trade_fraction
                    cost = traded_notional * self.config.cost_rate
                    total_costs += cost
                    turnover_notional += traded_notional
                    prev_nav = max(0.0, prev_nav - cost)
                current_weights = target_weights

            daily_ret = self._daily_portfolio_return(
                current_weights=current_weights,
                prev_prices=price_cache.get(prev_date, {}),
                curr_prices=price_cache.get(date, {}),
            )
            nav = prev_nav * (1.0 + daily_ret)
            nav_series.append((date, nav))

        n_days = len(nav_series)
        years = n_days / TRADING_DAYS_PER_YEAR if n_days > 0 else 0.0
        avg_nav = float(np.mean([x[1] for x in nav_series])) if nav_series else 0.0
        annual_turnover = (
            turnover_notional / avg_nav / years
            if avg_nav > 0 and years > 0
            else 0.0
        )
        return (
            nav_series,
            n_rebalances,
            n_trades,
            total_costs,
            annual_turnover,
            first_signal,
            first_activation,
        )

    def _build_target_weights(self, scores: Dict[str, float]) -> Dict[str, float]:
        clean = [(s, float(v)) for s, v in scores.items() if np.isfinite(v)]
        if not clean:
            return {}

        clean.sort(key=lambda x: x[1], reverse=True)
        top = clean[: self.config.top_n]
        if not top:
            return {}

        if self.config.weighting == "score_weighted":
            vals = np.array([x[1] for x in top], dtype=float)
            # Shift scores to non-negative to avoid negative long-only weights.
            vals = vals - np.min(vals)
            if float(np.sum(vals)) <= 1e-12:
                w = np.ones(len(top), dtype=float) / len(top)
            else:
                w = vals / float(np.sum(vals))
        else:
            w = np.ones(len(top), dtype=float) / len(top)

        return {sym: float(weight) for (sym, _), weight in zip(top, w)}

    @staticmethod
    def _compute_trade_fraction(
        old_w: Dict[str, float],
        new_w: Dict[str, float],
    ) -> Tuple[float, int]:
        symbols = set(old_w) | set(new_w)
        total_abs = 0.0
        trade_count = 0
        for sym in symbols:
            prev = old_w.get(sym, 0.0)
            new = new_w.get(sym, 0.0)
            diff = abs(new - prev)
            total_abs += diff
            if diff > 1e-10:
                trade_count += 1
        return total_abs, trade_count

    @staticmethod
    def _daily_portfolio_return(
        current_weights: Dict[str, float],
        prev_prices: Dict[str, float],
        curr_prices: Dict[str, float],
    ) -> float:
        if not current_weights:
            return 0.0

        valid_weights: List[Tuple[float, float]] = []
        for sym, weight in current_weights.items():
            p0 = prev_prices.get(sym)
            p1 = curr_prices.get(sym)
            if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
                continue
            r = (p1 / p0) - 1.0
            valid_weights.append((weight, r))

        if not valid_weights:
            return 0.0

        total_w = float(sum(w for w, _ in valid_weights))
        if total_w <= 1e-12:
            return 0.0

        return float(sum((w / total_w) * r for w, r in valid_weights))

    def _evaluate_benchmarks(
        self,
        strategy_nav: List[Tuple[str, float]],
        benchmark_cache: Dict[str, List[Tuple[str, float]]],
    ) -> List[BenchmarkComparison]:
        out: List[BenchmarkComparison] = []
        for benchmark, bm_nav in benchmark_cache.items():
            if not bm_nav or len(bm_nav) < 2:
                continue

            strat_aligned, bm_aligned = self._align_nav_series(strategy_nav, bm_nav)
            if len(strat_aligned) < 2 or len(bm_aligned) < 2:
                continue

            rel = compute_metrics(
                nav_series=strat_aligned,
                benchmark_nav=bm_aligned,
                days_per_year=TRADING_DAYS_PER_YEAR,
            )
            bm = compute_metrics(
                nav_series=bm_aligned,
                days_per_year=TRADING_DAYS_PER_YEAR,
            )

            out.append(
                BenchmarkComparison(
                    benchmark=benchmark,
                    benchmark_total_return=float(bm.total_return),
                    benchmark_cagr=float(bm.cagr),
                    excess_total_return=float(rel.total_return - bm.total_return),
                    excess_cagr=float(rel.cagr - bm.cagr),
                    alpha=float(rel.alpha),
                    beta=float(rel.beta),
                    information_ratio=float(rel.information_ratio),
                    tracking_error=float(rel.tracking_error),
                )
            )
        return out

    def _prefetch_benchmarks(self) -> Dict[str, List[Tuple[str, float]]]:
        """Load benchmark NAV series once per evaluation batch."""
        out: Dict[str, List[Tuple[str, float]]] = {}
        for benchmark in self.config.benchmarks:
            nav = self.adapter.get_benchmark_nav(benchmark)
            if nav and len(nav) >= 2:
                out[benchmark] = nav
        return out

    @staticmethod
    def _align_nav_series(
        strategy_nav: List[Tuple[str, float]],
        benchmark_nav: List[Tuple[str, float]],
    ) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
        s_map = {d: v for d, v in strategy_nav}
        b_map = {d: v for d, v in benchmark_nav}
        common = sorted(set(s_map.keys()) & set(b_map.keys()))
        s = [(d, float(s_map[d])) for d in common]
        b = [(d, float(b_map[d])) for d in common]
        return s, b

    @staticmethod
    def _zscore(values: List[float]) -> List[float]:
        arr = np.array(values, dtype=float)
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=0))
        if std <= 1e-12:
            return [0.0 for _ in values]
        return [float((x - mean) / std) for x in arr]

    def _attach_quality_scores(self, metrics: List[CandidateSlowMetrics]) -> None:
        if not metrics:
            return

        z_sharpe = self._zscore([m.strategy_sharpe for m in metrics])
        z_cagr = self._zscore([m.strategy_cagr for m in metrics])
        z_excess = self._zscore([m.avg_excess_cagr for m in metrics])
        z_ir = self._zscore([m.avg_information_ratio for m in metrics])
        z_mdd = self._zscore([-abs(m.strategy_max_drawdown) for m in metrics])

        for i, m in enumerate(metrics):
            m.quality_score = (
                0.35 * z_sharpe[i]
                + 0.25 * z_cagr[i]
                + 0.20 * z_excess[i]
                + 0.15 * z_ir[i]
                + 0.05 * z_mdd[i]
            )
