"""
Factor ABC + FactorMeta 测试
"""

import pytest
from typing import Dict

import pandas as pd

from backtest.factor_study.protocol import Factor, FactorMeta


# ── 测试 FactorMeta ──────────────────────────────────────

class TestFactorMeta:
    def test_str_higher_is_stronger(self):
        meta = FactorMeta(
            name="RS_Rating_B",
            score_name="rs_rank",
            score_range=(0, 99),
            higher_is_stronger=True,
        )
        s = str(meta)
        assert "RS_Rating_B" in s
        assert "↑" in s

    def test_str_lower_is_stronger(self):
        meta = FactorMeta(
            name="Inverse",
            score_name="inv",
            score_range=(0, 1),
            higher_is_stronger=False,
        )
        s = str(meta)
        assert "↓" in s

    def test_default_min_data_days(self):
        meta = FactorMeta("X", "x", (0, 1), True)
        assert meta.min_data_days == 70

    def test_custom_min_data_days(self):
        meta = FactorMeta("X", "x", (0, 1), True, min_data_days=200)
        assert meta.min_data_days == 200


# ── 测试 Factor ABC ──────────────────────────────────────

class DummyFactor(Factor):
    """测试用的具体 Factor 实现"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="Dummy",
            score_name="dummy_score",
            score_range=(0, 100),
            higher_is_stronger=True,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        return {sym: 50.0 for sym in price_dict}


class TestFactorABC:
    def test_instantiation(self):
        f = DummyFactor()
        assert f.meta.name == "Dummy"

    def test_compute_returns_dict(self):
        f = DummyFactor()
        prices = {"AAPL": pd.DataFrame({"close": [100]})}
        result = f.compute(prices, "2024-01-01")
        assert isinstance(result, dict)
        assert result["AAPL"] == 50.0

    def test_repr(self):
        f = DummyFactor()
        assert "Dummy" in repr(f)

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Factor()
