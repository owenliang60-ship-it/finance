"""
ParameterSweep — 参数扫描编排

数据只加载一次，所有参数组合共享。
"""

import logging
from dataclasses import asdict
from itertools import product
from typing import Any, Dict, List, Optional

import pandas as pd

from backtest.config import (
    BacktestConfig,
    US_SWEEP_GRID,
    CRYPTO_SWEEP_GRID,
    us_preset,
    crypto_preset,
)
from backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)


class ParameterSweep:
    """
    参数扫描器

    用法:
        sweep = ParameterSweep("us_stocks")
        results_df = sweep.run()
    """

    def __init__(self, market: str, grid: Optional[Dict[str, list]] = None):
        """
        Args:
            market: "us_stocks" 或 "crypto"
            grid: 自定义参数网格。None = 使用默认网格
        """
        self.market = market

        if grid is not None:
            self._grid = grid
        elif market == "crypto":
            self._grid = dict(CRYPTO_SWEEP_GRID)
        else:
            self._grid = dict(US_SWEEP_GRID)

        # 可选覆盖
        self._overrides: Dict[str, Any] = {}

    def add(self, param: str, values: list):
        """添加或覆盖扫描维度"""
        self._grid[param] = values

    def set_override(self, **kwargs):
        """设置固定参数 (不参与扫描)"""
        self._overrides.update(kwargs)

    def total_combinations(self) -> int:
        """总参数组合数"""
        n = 1
        for vals in self._grid.values():
            n *= len(vals)
        return n

    def run(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adapter=None,
        progress_callback=None,
    ) -> pd.DataFrame:
        """
        执行参数扫描

        Args:
            start_date: 回测起始日期
            end_date: 回测结束日期
            adapter: 预加载的数据适配器 (避免重复加载)
            progress_callback: 进度回调 fn(current, total, config)

        Returns:
            DataFrame — 每行一组参数 + 完整绩效指标
        """
        # 预加载数据
        if adapter is None:
            adapter = self._create_adapter()
        adapter.load_all()

        # 生成参数组合
        param_names = sorted(self._grid.keys())
        param_values = [self._grid[k] for k in param_names]
        combos = list(product(*param_values))
        total = len(combos)

        logger.info(f"参数扫描: {total} 组合, market={self.market}")

        results = []
        for i, combo in enumerate(combos):
            params = dict(zip(param_names, combo))
            params.update(self._overrides)

            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date

            config = self._make_config(params)

            if progress_callback:
                progress_callback(i + 1, total, config)

            # 执行回测 (共享 adapter)
            engine = BacktestEngine(config, adapter=adapter)
            metrics = engine.run()

            # 合并参数 + 指标
            row = {**params, **asdict(metrics), "label": config.label()}
            results.append(row)

            if (i + 1) % 10 == 0:
                logger.info(f"  进度: {i+1}/{total}")

        df = pd.DataFrame(results)

        # 排序: Sharpe 降序
        if "sharpe_ratio" in df.columns:
            df = df.sort_values("sharpe_ratio", ascending=False).reset_index(drop=True)

        return df

    # ── 内部方法 ──────────────────────────────────────

    def _make_config(self, params: dict) -> BacktestConfig:
        """从参数字典创建 BacktestConfig"""
        factory = crypto_preset if self.market == "crypto" else us_preset
        return factory(**params)

    def _create_adapter(self):
        """根据 market 创建适配器"""
        if self.market == "crypto":
            from backtest.adapters.crypto import CryptoAdapter
            return CryptoAdapter()
        else:
            from backtest.adapters.us_stocks import USStocksAdapter
            return USStocksAdapter()
