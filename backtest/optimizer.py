"""
ParamOptimizer — 最优参数选择 + 稳健性分析 + Walk-Forward 验证

两层防过拟合:
1. 稳健性分析: 候选参数的邻域表现一致性
2. Walk-Forward: 滚动窗口样本外验证
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd
from dateutil.relativedelta import relativedelta
from datetime import datetime

from backtest.config import BacktestConfig, us_preset, crypto_preset
from backtest.sweep import ParameterSweep

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardRound:
    """单轮 Walk-Forward 结果"""
    round_num: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_config_label: str
    best_params: dict
    in_sample_sharpe: float
    in_sample_cagr: float
    out_sample_sharpe: float
    out_sample_cagr: float
    out_sample_max_dd: float


@dataclass
class WalkForwardResult:
    """Walk-Forward 验证汇总"""
    rounds: List[WalkForwardRound]
    avg_in_sample_sharpe: float
    avg_out_sample_sharpe: float
    avg_out_sample_cagr: float
    overfit_ratio: float             # 1 - (out/in), 越接近 0 越好
    recommended_config: Optional[BacktestConfig] = None
    param_consistency: float = 0.0   # 各轮参数一致性 (0-1)


# ── 邻居参数定义 ────────────────────────────────────────

# 有序参数值列表 (用于查找相邻值)
_NEIGHBOR_MAP = {
    "top_n": [5, 10, 15, 20],
    "rebalance_freq": {
        "us_stocks": ["W", "2W", "M"],
        "crypto": ["D", "3D", "W"],
    },
    "sell_buffer": {
        "us_stocks": [0, 5, 10],
        "crypto": [0, 3, 5],
    },
    "rs_method": ["B", "C"],
}


class ParamOptimizer:
    """两层优化: 稳健性选择 + Walk-Forward 验证"""

    def __init__(self, market: str, adapter=None):
        self.market = market
        self.adapter = adapter

    # ═══ 第一层: 稳健性分析 ═══════════════════════════

    def rank_with_robustness(
        self,
        sweep_df: pd.DataFrame,
        metric: str = "sharpe_ratio",
        top_k: int = 10,
    ) -> pd.DataFrame:
        """
        稳健性排名

        1. 取 metric 排名 Top K
        2. 对每个候选找参数邻居
        3. 计算稳健性得分 = 候选 metric × 邻域平均 metric 的调和平均
        """
        if sweep_df.empty or metric not in sweep_df.columns:
            return sweep_df

        sorted_df = sweep_df.sort_values(metric, ascending=False).reset_index(drop=True)
        top_candidates = sorted_df.head(top_k).copy()

        robustness_scores = []
        for idx, row in top_candidates.iterrows():
            candidate_val = row[metric]
            neighbor_vals = self._find_neighbor_values(row, sorted_df, metric)

            if neighbor_vals:
                neighbor_avg = sum(neighbor_vals) / len(neighbor_vals)
                # 调和平均
                if candidate_val > 0 and neighbor_avg > 0:
                    score = 2 * candidate_val * neighbor_avg / (candidate_val + neighbor_avg)
                else:
                    score = 0.0
            else:
                score = candidate_val

            robustness_scores.append(score)

        top_candidates["robustness_score"] = robustness_scores
        top_candidates["neighbor_count"] = [
            len(self._find_neighbor_values(row, sorted_df, metric))
            for _, row in top_candidates.iterrows()
        ]

        return top_candidates.sort_values(
            "robustness_score", ascending=False
        ).reset_index(drop=True)

    def _find_neighbor_values(
        self, row: pd.Series, full_df: pd.DataFrame, metric: str
    ) -> List[float]:
        """
        查找参数空间中的邻居

        邻居定义: 只有一个维度变化的组合
        """
        param_cols = ["rs_method", "top_n", "rebalance_freq", "sell_buffer"]
        available_cols = [c for c in param_cols if c in row.index and c in full_df.columns]

        neighbor_vals = []

        for col in available_cols:
            current_val = row[col]
            adjacent_vals = self._get_adjacent_values(col, current_val)

            for adj_val in adjacent_vals:
                # 构建邻居的筛选条件: 只有 col 不同
                mask = pd.Series(True, index=full_df.index)
                for c in available_cols:
                    if c == col:
                        mask &= full_df[c] == adj_val
                    else:
                        mask &= full_df[c] == row[c]

                matches = full_df[mask]
                if not matches.empty:
                    neighbor_vals.append(float(matches.iloc[0][metric]))

        return neighbor_vals

    def _get_adjacent_values(self, param: str, current_val) -> list:
        """获取参数的相邻值"""
        if param == "rebalance_freq":
            ordered = _NEIGHBOR_MAP["rebalance_freq"].get(self.market, [])
        elif param == "sell_buffer":
            ordered = _NEIGHBOR_MAP["sell_buffer"].get(self.market, [])
        elif param in _NEIGHBOR_MAP:
            ordered = _NEIGHBOR_MAP[param]
        else:
            return []

        try:
            idx = ordered.index(current_val)
        except ValueError:
            return []

        adjacent = []
        if idx > 0:
            adjacent.append(ordered[idx - 1])
        if idx < len(ordered) - 1:
            adjacent.append(ordered[idx + 1])

        return adjacent

    # ═══ 第二层: Walk-Forward ═══════════════════════════

    def walk_forward(
        self,
        train_months: int = 36,
        test_months: int = 12,
        step_months: int = 12,
        metric: str = "sharpe_ratio",
        grid: Optional[dict] = None,
    ) -> WalkForwardResult:
        """
        滚动窗口 Walk-Forward 验证

        Args:
            train_months: 训练窗口月数
            test_months: 验证窗口月数
            step_months: 步进月数
            metric: 优化目标指标
            grid: 自定义参数网格

        Returns:
            WalkForwardResult
        """
        # 加载数据获取日期范围
        if self.adapter is None:
            self.adapter = self._create_adapter()
        self.adapter.load_all()

        date_range = self.adapter.get_date_range()
        if not date_range[0]:
            logger.error("无数据可用于 Walk-Forward")
            return WalkForwardResult(
                rounds=[], avg_in_sample_sharpe=0, avg_out_sample_sharpe=0,
                avg_out_sample_cagr=0, overfit_ratio=1.0,
            )

        data_start = datetime.strptime(date_range[0][:10], "%Y-%m-%d")
        data_end = datetime.strptime(date_range[1][:10], "%Y-%m-%d")

        # 生成窗口
        rounds = []
        round_num = 0
        window_start = data_start

        while True:
            train_end = window_start + relativedelta(months=train_months) - relativedelta(days=1)
            test_start = train_end + relativedelta(days=1)
            test_end = test_start + relativedelta(months=test_months) - relativedelta(days=1)

            if test_end > data_end:
                break

            round_num += 1
            logger.info(
                f"Walk-Forward 第 {round_num} 轮: "
                f"训练 {window_start.date()} → {train_end.date()} | "
                f"测试 {test_start.date()} → {test_end.date()}"
            )

            # 训练: 在 train 窗口上扫描
            sweep = ParameterSweep(self.market, grid=grid)
            train_df = sweep.run(
                start_date=str(window_start.date()),
                end_date=str(train_end.date()),
                adapter=self.adapter,
            )

            if train_df.empty:
                logger.warning(f"第 {round_num} 轮训练无结果, 跳过")
                window_start += relativedelta(months=step_months)
                continue

            # 稳健性排名
            robust_df = self.rank_with_robustness(train_df, metric)
            best_row = robust_df.iloc[0]

            # 提取最优参数
            param_cols = ["rs_method", "top_n", "rebalance_freq", "sell_buffer"]
            best_params = {c: best_row[c] for c in param_cols if c in best_row.index}
            in_sharpe = float(best_row.get("sharpe_ratio", 0))
            in_cagr = float(best_row.get("cagr", 0))

            # 测试: 用最优参数在 test 窗口跑
            factory = crypto_preset if self.market == "crypto" else us_preset
            test_config = factory(
                start_date=str(test_start.date()),
                end_date=str(test_end.date()),
                **best_params,
            )

            from backtest.engine import BacktestEngine
            test_engine = BacktestEngine(test_config, adapter=self.adapter)
            test_metrics = test_engine.run()

            wf_round = WalkForwardRound(
                round_num=round_num,
                train_start=str(window_start.date()),
                train_end=str(train_end.date()),
                test_start=str(test_start.date()),
                test_end=str(test_end.date()),
                best_config_label=test_config.label(),
                best_params=best_params,
                in_sample_sharpe=in_sharpe,
                in_sample_cagr=in_cagr,
                out_sample_sharpe=test_metrics.sharpe_ratio,
                out_sample_cagr=test_metrics.cagr,
                out_sample_max_dd=test_metrics.max_drawdown,
            )
            rounds.append(wf_round)

            window_start += relativedelta(months=step_months)

        # 汇总
        return self._summarize_wf(rounds)

    def _summarize_wf(self, rounds: List[WalkForwardRound]) -> WalkForwardResult:
        """汇总 Walk-Forward 结果"""
        if not rounds:
            return WalkForwardResult(
                rounds=[], avg_in_sample_sharpe=0, avg_out_sample_sharpe=0,
                avg_out_sample_cagr=0, overfit_ratio=1.0,
            )

        avg_in = sum(r.in_sample_sharpe for r in rounds) / len(rounds)
        avg_out = sum(r.out_sample_sharpe for r in rounds) / len(rounds)
        avg_out_cagr = sum(r.out_sample_cagr for r in rounds) / len(rounds)

        overfit = 1 - (avg_out / avg_in) if avg_in > 0 else 1.0

        # 参数一致性: 各轮选出的参数有多一致
        consistency = self._param_consistency(rounds)

        # 推荐: 用出现频率最高的参数组合
        recommended = self._most_common_params(rounds)

        return WalkForwardResult(
            rounds=rounds,
            avg_in_sample_sharpe=round(avg_in, 4),
            avg_out_sample_sharpe=round(avg_out, 4),
            avg_out_sample_cagr=round(avg_out_cagr, 6),
            overfit_ratio=round(overfit, 4),
            recommended_config=recommended,
            param_consistency=round(consistency, 4),
        )

    def _param_consistency(self, rounds: List[WalkForwardRound]) -> float:
        """计算各轮参数的一致性 (0-1)"""
        if len(rounds) <= 1:
            return 1.0

        params_list = [r.best_params for r in rounds]
        all_keys = set()
        for p in params_list:
            all_keys.update(p.keys())

        if not all_keys:
            return 1.0

        match_count = 0
        total_count = 0

        for key in all_keys:
            values = [p.get(key) for p in params_list]
            # 统计最常见值的出现比例
            from collections import Counter
            counts = Counter(values)
            most_common_count = counts.most_common(1)[0][1]
            match_count += most_common_count
            total_count += len(values)

        return match_count / total_count if total_count > 0 else 0.0

    def _most_common_params(self, rounds: List[WalkForwardRound]) -> Optional[BacktestConfig]:
        """从多轮中找出最常见的参数组合"""
        if not rounds:
            return None

        from collections import Counter

        # 对每个参数维度取最常见值
        param_keys = set()
        for r in rounds:
            param_keys.update(r.best_params.keys())

        best_params = {}
        for key in param_keys:
            values = [r.best_params.get(key) for r in rounds if key in r.best_params]
            if values:
                best_params[key] = Counter(values).most_common(1)[0][0]

        factory = crypto_preset if self.market == "crypto" else us_preset
        return factory(**best_params)

    def _create_adapter(self):
        """根据 market 创建适配器"""
        if self.market == "crypto":
            from backtest.adapters.crypto import CryptoAdapter
            return CryptoAdapter()
        else:
            from backtest.adapters.us_stocks import USStocksAdapter
            return USStocksAdapter()
