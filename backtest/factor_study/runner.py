"""
FactorStudyRunner — 编排器

核心循环:
1. adapter.load_all()
2. computation_dates = all_dates[::freq]
3. FOR each comp_date:
     sliced = adapter.slice_to_date(comp_date)
     FOR each factor:
       scores[factor][symbol].append((date, score))
4. FOR each benchmark:
     return_matrices = build_return_matrix(...)
     Track 1: analyze_ic(scores, return_matrices)
     Track 2: event_study(events, return_matrices)
5. → FactorStudyResults (N factors × M benchmarks)
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from backtest.config import FREQ_DAYS, FactorStudyConfig
from backtest.factor_study.event_study import EventStudyResult, run_event_study
from backtest.factor_study.forward_returns import (
    build_excess_return_matrix,
    build_return_matrix,
)
from backtest.factor_study.ic_analysis import ICDecayCurve, ICResult, analyze_ic
from backtest.factor_study.protocol import Factor
from backtest.factor_study.signals import SignalDefinition, detect_signals
from backtest.factor_study.sweep import get_default_sweep

logger = logging.getLogger(__name__)


@dataclass
class FactorStudyResults:
    """单个因子 × 单个基准的完整研究结果"""
    factor_name: str
    config: FactorStudyConfig
    benchmark_label: str = ""
    # In-Sample 结果 (始终有)
    ic_results: List[ICResult] = field(default_factory=list)
    ic_decay: Optional[ICDecayCurve] = None
    event_results: List[EventStudyResult] = field(default_factory=list)
    # Out-of-Sample 结果 (数据不足时为 None)
    oos_ic_results: Optional[List[ICResult]] = None
    oos_ic_decay: Optional[ICDecayCurve] = None
    oos_event_results: Optional[List[EventStudyResult]] = None
    # 元信息
    is_dates: List[str] = field(default_factory=list)
    oos_dates: List[str] = field(default_factory=list)
    oos_skipped: bool = False
    n_computation_dates: int = 0
    n_symbols: int = 0
    elapsed_seconds: float = 0.0


class FactorStudyRunner:
    """
    因子研究编排器

    用法:
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RSRatingBFactor())
        results = runner.run()
    """

    def __init__(self, config: FactorStudyConfig, adapter):
        """
        Args:
            config: 因子研究配置
            adapter: 数据适配器 (USStocksAdapter / CryptoAdapter)
                须实现 load_all(), get_trading_dates(), slice_to_date()
        """
        self._config = config
        self._adapter = adapter
        self._factors: List[Factor] = []
        self._sweep_overrides: Dict[str, List[SignalDefinition]] = {}

    def add_factor(self, factor: Factor) -> None:
        """注册因子"""
        self._factors.append(factor)

    def set_sweep(self, factor_name: str, signals: List[SignalDefinition]) -> None:
        """覆盖某因子的参数扫描"""
        self._sweep_overrides[factor_name] = signals

    def run(self) -> List[FactorStudyResults]:
        """
        运行因子研究

        Returns:
            List[FactorStudyResults] — 长度 = 因子数 × 基准数
        """
        if not self._factors:
            logger.warning("没有注册任何因子")
            return []

        # Step 1: 加载数据 (一次)
        full_data = self._adapter.load_all()
        all_dates = self._adapter.get_trading_dates()
        logger.info(f"数据加载完成: {len(full_data)} symbols, {len(all_dates)} 交易日")

        # 日期过滤
        if self._config.start_date:
            all_dates = [d for d in all_dates if d >= self._config.start_date]
        if self._config.end_date:
            all_dates = [d for d in all_dates if d <= self._config.end_date]

        # Step 2: 计算日期采样
        freq_days = FREQ_DAYS.get(self._config.computation_freq, 5)
        computation_dates = all_dates[::freq_days]
        logger.info(f"计算频率={self._config.computation_freq}, 计算日数={len(computation_dates)}")

        # Step 3: 构建多组 return_matrices
        benchmarks = self._config.benchmark_symbols
        bench_return_matrices: Dict[str, Dict[int, pd.DataFrame]] = {}

        if benchmarks:
            for bench_label in benchmarks:
                benchmark_nav = self._adapter.get_benchmark_nav(bench_label)
                if benchmark_nav:
                    benchmark_df = pd.DataFrame(
                        benchmark_nav, columns=["date", "close"],
                    )
                    logger.info(
                        f"基准已加载: {bench_label}, {len(benchmark_df)} 日"
                    )
                    logger.info("构建超额前向收益矩阵 (vs %s)...", bench_label)
                    bench_return_matrices[bench_label] = build_excess_return_matrix(
                        full_data, benchmark_df, computation_dates,
                        self._config.forward_horizons,
                    )
                else:
                    logger.warning(
                        f"基准 {bench_label} 数据不可用，跳过"
                    )
        else:
            # 无基准: 使用原始收益
            logger.info("构建前向收益矩阵 (无基准)...")
            bench_return_matrices[""] = build_return_matrix(
                full_data, computation_dates, self._config.forward_horizons,
            )

        if not bench_return_matrices:
            logger.warning("所有基准数据均不可用，回退到原始收益")
            bench_return_matrices[""] = build_return_matrix(
                full_data, computation_dates, self._config.forward_horizons,
            )

        # Step 4: 逐因子计算分数 (一次)，然后对每个基准分析
        all_results: List[FactorStudyResults] = []

        for factor in self._factors:
            t0 = time.time()
            name = factor.meta.name
            logger.info(f"开始因子研究: {name}")

            # 因子分数只算一次
            score_dict, symbols_seen = self._compute_scores(
                factor, full_data, computation_dates,
            )

            # 对每个基准的 return_matrices 做分析
            for bench_label, return_matrices in bench_return_matrices.items():
                result = self._analyze_factor(
                    factor, score_dict, symbols_seen,
                    computation_dates, return_matrices,
                    bench_label,
                )
                result.elapsed_seconds = time.time() - t0
                all_results.append(result)

                bench_display = bench_label or "raw"
                logger.info(
                    f"完成 {name} (vs {bench_display}): "
                    f"IC results={len(result.ic_results)}, "
                    f"Event results={len(result.event_results)}, "
                    f"耗时={result.elapsed_seconds:.1f}s"
                )

        return all_results

    def _compute_scores(
        self,
        factor: Factor,
        full_data: Dict,
        computation_dates: List[str],
    ) -> Tuple[Dict[str, List[Tuple[str, float]]], set]:
        """计算因子分数 (只算一次，跨基准共享)

        Returns:
            (score_dict, symbols_seen)
        """
        name = factor.meta.name
        score_history: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        symbols_seen = set()

        for i, comp_date in enumerate(computation_dates):
            sliced = self._adapter.slice_to_date(comp_date)
            if not sliced:
                continue

            scores = factor.compute(sliced, comp_date)

            for sym, score in scores.items():
                score_history[sym].append((comp_date, score))
                symbols_seen.add(sym)

            if (i + 1) % 50 == 0:
                logger.debug(
                    f"  {name}: {i+1}/{len(computation_dates)} 日, "
                    f"{len(scores)} symbols"
                )

        logger.info(
            f"  因子分数计算完成: {len(score_history)} symbols × "
            f"{len(computation_dates)} 日"
        )

        return dict(score_history), symbols_seen

    def _analyze_factor(
        self,
        factor: Factor,
        score_dict: Dict[str, List[Tuple[str, float]]],
        symbols_seen: set,
        computation_dates: List[str],
        return_matrices: Dict[int, pd.DataFrame],
        benchmark_label: str,
    ) -> FactorStudyResults:
        """对单个因子 × 单个基准做 IC + 事件研究 (含 IS/OOS 分割)"""
        name = factor.meta.name

        # IS/OOS 日期分割
        if self._config.oos_start_date:
            is_dates = [
                d for d in computation_dates
                if d < self._config.oos_start_date
            ]
            oos_dates = [
                d for d in computation_dates
                if d >= self._config.oos_start_date
            ]
        else:
            split_idx = int(
                len(computation_dates) * (1 - self._config.oos_fraction)
            )
            is_dates = computation_dates[:split_idx]
            oos_dates = computation_dates[split_idx:]
        has_oos = len(oos_dates) >= self._config.min_oos_dates

        if has_oos:
            split_mode = (
                f"显式起点 {self._config.oos_start_date}"
                if self._config.oos_start_date
                else f"最后 {self._config.oos_fraction:.0%}"
            )
            logger.info(
                f"  IS/OOS 分割 ({split_mode}): IS={len(is_dates)} 日 "
                f"({is_dates[0]}~{is_dates[-1]}), "
                f"OOS={len(oos_dates)} 日 "
                f"({oos_dates[0]}~{oos_dates[-1]})"
            )
        else:
            split_mode = (
                f"显式起点 {self._config.oos_start_date}"
                if self._config.oos_start_date
                else f"最后 {self._config.oos_fraction:.0%}"
            )
            logger.info(
                f"  OOS 跳过 ({split_mode}): OOS 日期数 {len(oos_dates)} < "
                f"最小门槛 {self._config.min_oos_dates}"
            )

        result = FactorStudyResults(
            factor_name=name,
            config=self._config,
            benchmark_label=benchmark_label,
            n_computation_dates=len(computation_dates),
            n_symbols=len(symbols_seen),
            is_dates=list(is_dates),
            oos_dates=list(oos_dates),
            oos_skipped=not has_oos,
        )

        if not score_dict:
            return result

        sweep = self._sweep_overrides.get(name) or get_default_sweep(name)

        # ── In-Sample ─────────────────────────────────────
        result.ic_results, result.ic_decay = analyze_ic(
            factor.meta, score_dict, return_matrices,
            is_dates, self._config.n_quantiles,
        )

        is_date_set = set(is_dates)
        for signal_def in sweep:
            events = detect_signals(score_dict, signal_def)
            events = _filter_events(events, is_date_set)
            if not events:
                continue
            evts = run_event_study(name, signal_def, events, return_matrices)
            result.event_results.extend(evts)

        # ── Out-of-Sample ─────────────────────────────────
        if has_oos:
            result.oos_ic_results, result.oos_ic_decay = analyze_ic(
                factor.meta, score_dict, return_matrices,
                oos_dates, self._config.n_quantiles,
            )

            oos_date_set = set(oos_dates)
            result.oos_event_results = []
            for signal_def in sweep:
                events = detect_signals(score_dict, signal_def)
                events = _filter_events(events, oos_date_set)
                if not events:
                    continue
                evts = run_event_study(
                    name, signal_def, events, return_matrices,
                )
                result.oos_event_results.extend(evts)

        return result


def _filter_score_history(
    score_history: Dict[str, List[Tuple[str, float]]],
    dates_set: set,
) -> Dict[str, List[Tuple[str, float]]]:
    """过滤 score_history，只保留指定日期集合内的记录."""
    filtered: Dict[str, List[Tuple[str, float]]] = {}
    for sym, history in score_history.items():
        f = [(d, s) for d, s in history if d in dates_set]
        if f:
            filtered[sym] = f
    return filtered


def _filter_events(
    events: Dict[str, List[str]],
    dates_set: set,
) -> Dict[str, List[str]]:
    """过滤事件，只保留落在指定日期集合内的事件日期."""
    filtered: Dict[str, List[str]] = {}
    for sym, event_dates in events.items():
        f = [d for d in event_dates if d in dates_set]
        if f:
            filtered[sym] = f
    return filtered
