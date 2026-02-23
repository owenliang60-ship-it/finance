"""
FactorStudyRunner — 编排器

核心循环:
1. adapter.load_all()
2. computation_dates = all_dates[::freq]
3. FOR each comp_date:
     sliced = adapter.slice_to_date(comp_date)
     FOR each factor:
       scores[factor][symbol].append((date, score))
4. return_matrices = build_return_matrix(full_data, dates, horizons)
5. Track 1: analyze_ic(scores, return_matrices)
6. Track 2: FOR each sweep_entry:
     events = detect_signals(scores, signal_def)
     event_study(events, return_matrices)
7. → FactorStudyResults
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backtest.config import FREQ_DAYS, FactorStudyConfig
from backtest.factor_study.event_study import EventStudyResult, run_event_study
from backtest.factor_study.forward_returns import build_return_matrix
from backtest.factor_study.ic_analysis import ICDecayCurve, ICResult, analyze_ic
from backtest.factor_study.protocol import Factor
from backtest.factor_study.signals import SignalDefinition, detect_signals
from backtest.factor_study.sweep import get_default_sweep

logger = logging.getLogger(__name__)


@dataclass
class FactorStudyResults:
    """单个因子的完整研究结果"""
    factor_name: str
    config: FactorStudyConfig
    ic_results: List[ICResult] = field(default_factory=list)
    ic_decay: Optional[ICDecayCurve] = None
    event_results: List[EventStudyResult] = field(default_factory=list)
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
            每个因子一个 FactorStudyResults
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

        # Step 4: 前向收益矩阵 (一次，跨因子共享)
        logger.info("构建前向收益矩阵...")
        return_matrices = build_return_matrix(
            full_data, computation_dates, self._config.forward_horizons,
        )

        # Step 3+5+6: 逐因子计算
        all_results: List[FactorStudyResults] = []

        for factor in self._factors:
            t0 = time.time()
            name = factor.meta.name
            logger.info(f"开始因子研究: {name}")

            result = self._run_single_factor(
                factor, full_data, computation_dates, return_matrices,
            )
            result.elapsed_seconds = time.time() - t0
            all_results.append(result)

            logger.info(
                f"完成 {name}: "
                f"IC results={len(result.ic_results)}, "
                f"Event results={len(result.event_results)}, "
                f"耗时={result.elapsed_seconds:.1f}s"
            )

        return all_results

    def _run_single_factor(
        self,
        factor: Factor,
        full_data: Dict,
        computation_dates: List[str],
        return_matrices: Dict,
    ) -> FactorStudyResults:
        """运行单个因子的完整研究"""
        name = factor.meta.name
        result = FactorStudyResults(
            factor_name=name,
            config=self._config,
            n_computation_dates=len(computation_dates),
        )

        # Step 3: 逐日计算因子分数
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
                logger.debug(f"  {name}: {i+1}/{len(computation_dates)} 日, {len(scores)} symbols")

        result.n_symbols = len(symbols_seen)
        logger.info(f"  因子分数计算完成: {len(score_history)} symbols × {len(computation_dates)} 日")

        if not score_history:
            return result

        # Step 5: Track 1 — IC 分析
        ic_results, ic_decay = analyze_ic(
            factor.meta, dict(score_history), return_matrices,
            computation_dates, self._config.n_quantiles,
        )
        result.ic_results = ic_results
        result.ic_decay = ic_decay

        # Step 6: Track 2 — 事件研究
        sweep = self._sweep_overrides.get(name) or get_default_sweep(name)

        for signal_def in sweep:
            events = detect_signals(dict(score_history), signal_def)
            if not events:
                continue

            event_results = run_event_study(
                name, signal_def, events, return_matrices,
            )
            result.event_results.extend(event_results)

        return result
