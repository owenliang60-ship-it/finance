"""
Tests for DSL expression execution engine.
"""

import numpy as np
import pandas as pd

from factor_research_codex1.evaluation.expression_engine import DSLExpressionEngine


def _make_price_dict():
    dates = pd.bdate_range("2024-01-01", periods=80)
    x = np.arange(len(dates), dtype=float)

    a_close = 100 + 0.8 * x + 2 * np.sin(x / 5)
    b_close = 120 + 0.1 * x + 1 * np.sin(x / 4)
    c_close = 90 - 0.2 * x + 3 * np.sin(x / 6)

    out = {}
    for sym, close in [("A", a_close), ("B", b_close), ("C", c_close)]:
        close = pd.Series(close, index=range(len(dates)))
        high = close + 1.5
        low = close - 1.5
        volume = 1_000_000 + 10_000 * np.arange(len(dates))
        out[sym] = pd.DataFrame(
            {
                "date": [d.strftime("%Y-%m-%d") for d in dates],
                "open": close.values,
                "high": high.values,
                "low": low.values,
                "close": close.values,
                "volume": volume,
            }
        )
    return out


class TestDSLExpressionEngine:
    def test_basic_expression(self):
        engine = DSLExpressionEngine()
        result = engine.evaluate("ret_n(close, 20)", _make_price_dict())
        assert result.is_valid
        assert len(result.scores) == 3

    def test_rank_cs_output_range(self):
        engine = DSLExpressionEngine()
        result = engine.evaluate("rank_cs(ret_n(close, 20))", _make_price_dict())
        assert result.is_valid
        for v in result.scores.values():
            assert 0.0 <= v <= 1.0

    def test_macd_clip_composite(self):
        engine = DSLExpressionEngine()
        expr = "clip(zscore(macd(close, 12, 26, 9), 20), -3, 3)"
        result = engine.evaluate(expr, _make_price_dict())
        assert result.is_valid
        assert len(result.scores) >= 1

    def test_atr_supported(self):
        engine = DSLExpressionEngine()
        result = engine.evaluate("atr(high, low, close, 14)", _make_price_dict())
        assert result.is_valid
        assert len(result.scores) == 3

    def test_invalid_expression_is_reported(self):
        engine = DSLExpressionEngine()
        result = engine.evaluate("future_magic(close, 20)", _make_price_dict())
        assert not result.is_valid
        assert result.scores == {}
        assert len(result.issues) > 0

