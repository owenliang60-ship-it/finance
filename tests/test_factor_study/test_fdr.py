"""
FDR 校正测试 — R2: Benjamini-Hochberg 多重检验校正
"""

import pytest

from backtest.factor_study.report import _apply_bh_fdr


class TestApplyBhFdr:
    def test_single_p_value_unchanged(self):
        """单个 p-value 不变."""
        result = _apply_bh_fdr([0.03])
        assert result == [0.03]

    def test_empty_list(self):
        """空列表返回空."""
        assert _apply_bh_fdr([]) == []

    def test_all_significant_stay_significant(self):
        """所有 p-value 都很小时，调整后仍显著."""
        p_values = [0.001, 0.002, 0.003]
        adjusted = _apply_bh_fdr(p_values)
        for p in adjusted:
            assert p < 0.01

    def test_borderline_becomes_nonsignificant(self):
        """75 个假设中 p=0.04 经 FDR 调整后不再显著.

        BH 公式: p_adj = p * N / rank
        排在第 1 位 (最小) 的 p=0.04 调整后 = 0.04 * 75 / 1 = 3.0 >> 0.05
        """
        # 75 个假设: 1 个 borderline (0.04), 74 个不显著 (0.5~0.99)
        p_values = [0.04] + [0.5 + i * 0.005 for i in range(74)]
        adjusted = _apply_bh_fdr(p_values)

        # p=0.04 调整后应远大于 0.05
        assert adjusted[0] > 0.05

    def test_preserves_order(self):
        """调整后的排序与原始一致."""
        p_values = [0.01, 0.05, 0.10, 0.50]
        adjusted = _apply_bh_fdr(p_values)

        for i in range(len(adjusted) - 1):
            assert adjusted[i] <= adjusted[i + 1]

    def test_never_exceeds_one(self):
        """调整后的 p-value 不超过 1."""
        p_values = [0.8, 0.9, 0.95, 0.99]
        adjusted = _apply_bh_fdr(p_values)
        for p in adjusted:
            assert p <= 1.0

    def test_monotonicity(self):
        """BH 校正满足单调性: 原始 p_i < p_j → p_adj_i <= p_adj_j."""
        p_values = [0.001, 0.01, 0.02, 0.05, 0.10, 0.50]
        adjusted = _apply_bh_fdr(p_values)
        for i in range(len(adjusted) - 1):
            assert adjusted[i] <= adjusted[i + 1] + 1e-10

    def test_known_example(self):
        """验证已知的 BH 计算结果.

        3 个 p-values: [0.01, 0.04, 0.05]
        rank 1: 0.01 * 3/1 = 0.03
        rank 2: 0.04 * 3/2 = 0.06
        rank 3: 0.05 * 3/3 = 0.05

        从大到小: adj[3]=0.05, adj[2]=min(0.05, 0.06)=0.05, adj[1]=min(0.05, 0.03)=0.03
        """
        adjusted = _apply_bh_fdr([0.01, 0.04, 0.05])
        assert abs(adjusted[0] - 0.03) < 1e-10
        assert abs(adjusted[1] - 0.05) < 1e-10  # min(0.06, 0.05)
        assert abs(adjusted[2] - 0.05) < 1e-10
