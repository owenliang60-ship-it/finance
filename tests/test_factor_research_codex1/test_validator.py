"""
Unit tests for Week 1 DSL validator.
"""

from factor_research_codex1.miner.validator import FactorDSLValidator


class TestFactorDSLValidator:
    def test_valid_simple_expression(self):
        validator = FactorDSLValidator(max_depth=4)
        result = validator.validate("rsi(close, 14)")
        assert result.is_valid
        assert result.depth >= 1
        assert result.normalized_expression != ""

    def test_valid_nested_expression(self):
        validator = FactorDSLValidator(max_depth=4)
        result = validator.validate("rank_cs(zscore(ret_n(close, 20), 60))")
        assert result.is_valid
        assert result.depth <= 4

    def test_valid_macd_and_clip(self):
        validator = FactorDSLValidator(max_depth=4)
        expr = "clip(zscore(macd(close, 12, 26, 9), 20), -3, 3)"
        result = validator.validate(expr)
        assert result.is_valid

    def test_invalid_unknown_function(self):
        validator = FactorDSLValidator()
        result = validator.validate("foo(close, 14)")
        assert not result.is_valid
        codes = {issue.code for issue in result.issues}
        assert "unknown_function" in codes

    def test_invalid_unknown_identifier(self):
        validator = FactorDSLValidator()
        result = validator.validate("rsi(future_close, 14)")
        assert not result.is_valid
        codes = {issue.code for issue in result.issues}
        assert "unknown_identifier" in codes

    def test_invalid_keyword_args(self):
        validator = FactorDSLValidator()
        result = validator.validate("rsi(close, n=14)")
        assert not result.is_valid
        codes = {issue.code for issue in result.issues}
        assert "keyword_not_allowed" in codes

    def test_invalid_window_out_of_range(self):
        validator = FactorDSLValidator(max_window=252)
        result = validator.validate("ret_n(close, 400)")
        assert not result.is_valid
        codes = {issue.code for issue in result.issues}
        assert "window_out_of_range" in codes

    def test_invalid_depth_exceeded(self):
        validator = FactorDSLValidator(max_depth=3)
        expr = "rank_cs(zscore(ema(ret_n(close, 20), 10), 60))"
        result = validator.validate(expr)
        assert not result.is_valid
        codes = {issue.code for issue in result.issues}
        assert "depth_exceeded" in codes

    def test_invalid_macd_order(self):
        validator = FactorDSLValidator()
        result = validator.validate("macd(close, 26, 12, 9)")
        assert not result.is_valid
        codes = {issue.code for issue in result.issues}
        assert "macd_order_invalid" in codes

    def test_invalid_atr_signature(self):
        validator = FactorDSLValidator()
        result = validator.validate("atr(close, high, low, 14)")
        assert not result.is_valid
        codes = {issue.code for issue in result.issues}
        assert "atr_input_invalid" in codes

