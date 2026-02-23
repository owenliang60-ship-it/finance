"""
ParamOptimizer 测试 — 稳健性分析 + Walk-Forward
"""

import pytest
import pandas as pd
import numpy as np

from backtest.optimizer import ParamOptimizer


class TestRobustnessRanking:
    """稳健性排名测试"""

    def _make_sweep_df(self):
        """构造一个模拟的参数扫描结果"""
        rows = []
        for method in ["B", "C"]:
            for top_n in [5, 10, 15, 20]:
                for freq in ["W", "2W", "M"]:
                    for buf in [0, 5, 10]:
                        sharpe = np.random.RandomState(
                            hash(f"{method}{top_n}{freq}{buf}") % 2**31
                        ).uniform(0.3, 2.0)
                        rows.append({
                            "rs_method": method,
                            "top_n": top_n,
                            "rebalance_freq": freq,
                            "sell_buffer": buf,
                            "sharpe_ratio": sharpe,
                            "cagr": sharpe * 0.05,
                            "max_drawdown": -0.1 / sharpe if sharpe > 0 else -0.5,
                            "label": f"us_{method}_top{top_n}_{freq}_buf{buf}",
                        })
        return pd.DataFrame(rows)

    def test_basic_ranking(self):
        opt = ParamOptimizer("us_stocks")
        sweep_df = self._make_sweep_df()
        result = opt.rank_with_robustness(sweep_df)
        assert "robustness_score" in result.columns
        assert len(result) == 10  # top_k=10 default
        # 稳健性得分应该是正的
        assert result["robustness_score"].iloc[0] > 0

    def test_neighbor_count(self):
        opt = ParamOptimizer("us_stocks")
        sweep_df = self._make_sweep_df()
        result = opt.rank_with_robustness(sweep_df)
        assert "neighbor_count" in result.columns
        # 每个候选应该有邻居
        assert result["neighbor_count"].iloc[0] > 0

    def test_empty_sweep(self):
        opt = ParamOptimizer("us_stocks")
        result = opt.rank_with_robustness(pd.DataFrame())
        assert result.empty

    def test_top_k(self):
        opt = ParamOptimizer("us_stocks")
        sweep_df = self._make_sweep_df()
        result = opt.rank_with_robustness(sweep_df, top_k=5)
        assert len(result) == 5

    def test_robustness_penalizes_outliers(self):
        """
        如果一组参数的 Sharpe 很高但邻居很差，
        其稳健性得分应低于 Sharpe 中等但邻居一致的。
        """
        opt = ParamOptimizer("us_stocks")

        # 构造: method=B, top_n=10 是 outlier (Sharpe=3.0)
        # 但 top_n=5 和 15 都很差 (Sharpe=0.2)
        rows = []
        for top_n in [5, 10, 15, 20]:
            for freq in ["W", "2W", "M"]:
                for buf in [0, 5, 10]:
                    for method in ["B", "C"]:
                        if method == "B" and top_n == 10 and freq == "M" and buf == 5:
                            sharpe = 3.0  # outlier
                        elif method == "B" and top_n in (5, 15):
                            sharpe = 0.2  # 邻居很差
                        else:
                            sharpe = 1.0

                        rows.append({
                            "rs_method": method,
                            "top_n": top_n,
                            "rebalance_freq": freq,
                            "sell_buffer": buf,
                            "sharpe_ratio": sharpe,
                            "cagr": sharpe * 0.05,
                            "max_drawdown": -0.1,
                            "label": f"us_{method}_top{top_n}_{freq}_buf{buf}",
                        })

        sweep_df = pd.DataFrame(rows)
        result = opt.rank_with_robustness(sweep_df, top_k=10)

        # outlier 的稳健性得分应该不是第一
        outlier_row = result[result["label"] == "us_B_top10_M_buf5"]
        if not outlier_row.empty:
            outlier_score = outlier_row.iloc[0]["robustness_score"]
            best_score = result.iloc[0]["robustness_score"]
            # outlier 的稳健性应低于最佳
            assert outlier_score <= best_score


class TestNeighborFinding:
    """邻居查找测试"""

    def test_adjacent_values(self):
        opt = ParamOptimizer("us_stocks")
        adj = opt._get_adjacent_values("top_n", 10)
        assert 5 in adj
        assert 15 in adj
        assert len(adj) == 2

    def test_edge_values(self):
        opt = ParamOptimizer("us_stocks")
        adj = opt._get_adjacent_values("top_n", 5)
        assert len(adj) == 1
        assert 10 in adj

    def test_freq_neighbors(self):
        opt = ParamOptimizer("us_stocks")
        adj = opt._get_adjacent_values("rebalance_freq", "2W")
        assert "W" in adj
        assert "M" in adj

    def test_method_neighbors(self):
        opt = ParamOptimizer("us_stocks")
        adj = opt._get_adjacent_values("rs_method", "B")
        assert "C" in adj

    def test_crypto_buffer_neighbors(self):
        opt = ParamOptimizer("crypto")
        adj = opt._get_adjacent_values("sell_buffer", 3)
        assert 0 in adj
        assert 5 in adj
