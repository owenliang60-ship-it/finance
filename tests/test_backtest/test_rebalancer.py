"""
Rebalancer 换仓逻辑测试
"""

import pytest
import pandas as pd
from backtest.rebalancer import Rebalancer, RebalanceAction


def _make_rs_df(symbols_ranks):
    """辅助: 从 [(symbol, rank), ...] 创建 RS DataFrame"""
    return pd.DataFrame(symbols_ranks, columns=["symbol", "rs_rank"])


class TestRebalancer:
    """换仓逻辑"""

    def test_empty_rs(self):
        r = Rebalancer(top_n=5, sell_buffer=0)
        action = r.compute(pd.DataFrame(columns=["symbol", "rs_rank"]), {"A", "B"})
        assert set(action.to_sell) == {"A", "B"}
        assert action.to_buy == []

    def test_fresh_start(self):
        """空仓开始 → 买入 Top N"""
        r = Rebalancer(top_n=3, sell_buffer=0)
        rs = _make_rs_df([("A", 99), ("B", 90), ("C", 80), ("D", 70), ("E", 60)])
        action = r.compute(rs, set())
        assert action.to_buy == ["A", "B", "C"]
        assert action.to_sell == []
        assert action.to_hold == []

    def test_no_change_needed(self):
        """持仓全在 Top N → 不换"""
        r = Rebalancer(top_n=3, sell_buffer=0)
        rs = _make_rs_df([("A", 99), ("B", 90), ("C", 80), ("D", 70)])
        action = r.compute(rs, {"A", "B", "C"})
        assert action.to_sell == []
        assert action.to_buy == []
        assert sorted(action.to_hold) == ["A", "B", "C"]

    def test_sell_below_top_n_no_buffer(self):
        """buffer=0: 跌出 Top N 立即卖"""
        r = Rebalancer(top_n=2, sell_buffer=0)
        rs = _make_rs_df([("D", 99), ("E", 90), ("A", 80), ("B", 70)])
        action = r.compute(rs, {"A", "B"})
        assert sorted(action.to_sell) == ["A", "B"]
        assert action.to_buy == ["D", "E"]

    def test_hysteresis_buffer(self):
        """buffer=2: 只要还在 Top(N+buffer) 就不卖"""
        r = Rebalancer(top_n=3, sell_buffer=2)
        rs = _make_rs_df([
            ("D", 99), ("E", 90), ("F", 80),  # Top 3 新
            ("A", 70), ("B", 60),               # 第 4, 5 名 (仍在安全区)
            ("C", 50),                           # 第 6 名 (出安全区)
        ])
        action = r.compute(rs, {"A", "B", "C"})
        assert "C" in action.to_sell  # 出局
        assert "A" in action.to_hold  # 保留
        assert "B" in action.to_hold  # 保留

    def test_missing_from_rs(self):
        """不在 RS 结果中 → 强制卖出"""
        r = Rebalancer(top_n=3, sell_buffer=5)
        rs = _make_rs_df([("A", 99), ("B", 90), ("C", 80)])
        action = r.compute(rs, {"A", "DELISTED"})
        assert "DELISTED" in action.to_sell

    def test_fill_slots(self):
        """卖出后空出 slots, 从 Top N 中补入"""
        r = Rebalancer(top_n=3, sell_buffer=0)
        rs = _make_rs_df([("D", 99), ("A", 90), ("E", 80), ("B", 70)])
        # 持有 A, B, C (C 不在 rs), B 跌出 top3
        action = r.compute(rs, {"A", "B", "C"})
        assert "C" in action.to_sell  # 不在 RS 中
        assert "B" in action.to_sell  # 跌出 top3
        assert "A" in action.to_hold
        # 空出 2 slots, 从 Top 3 (D, A, E) 中填 D 和 E
        assert "D" in action.to_buy
        assert "E" in action.to_buy


class TestComputeWeights:
    """目标权重计算"""

    def test_equal_weight(self):
        r = Rebalancer(top_n=3)
        rs = _make_rs_df([("A", 99), ("B", 90), ("C", 80)])
        action = RebalanceAction(to_sell=[], to_buy=["A", "B", "C"], to_hold=[], target_count=3)
        weights = r.compute_weights(action, rs, "equal")
        assert len(weights) == 3
        for w in weights.values():
            assert w == pytest.approx(1 / 3, rel=1e-6)

    def test_rs_weighted(self):
        r = Rebalancer(top_n=2)
        rs = _make_rs_df([("A", 80), ("B", 20)])
        action = RebalanceAction(to_sell=[], to_buy=["A", "B"], to_hold=[], target_count=2)
        weights = r.compute_weights(action, rs, "rs_weighted")
        assert weights["A"] > weights["B"]
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_empty_action(self):
        r = Rebalancer(top_n=3)
        rs = _make_rs_df([])
        action = RebalanceAction(to_sell=[], to_buy=[], to_hold=[], target_count=0)
        weights = r.compute_weights(action, rs, "equal")
        assert weights == {}
