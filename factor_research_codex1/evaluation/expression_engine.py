"""
Expression execution engine for the controlled factor DSL.

Week 2 scope:
- Evaluate DSL expression on sliced price data (per date, no look-ahead)
- Produce cross-sectional scores for factor evaluation
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Literal, Optional, Tuple, Union

import numpy as np
import pandas as pd

from factor_research_codex1.miner.validator import FactorDSLValidator, FactorValidationResult

ValueKind = Literal["series", "scalar"]
SeriesMap = Dict[str, pd.Series]
ScalarMap = Dict[str, float]
EvalValue = Union[SeriesMap, ScalarMap]


@dataclass
class ExpressionEvalResult:
    """Output of expression execution."""

    expression: str
    normalized_expression: str
    scores: ScalarMap
    is_valid: bool
    issues: List[str] = field(default_factory=list)


class DSLExpressionEngine:
    """Execute validated DSL expressions on price dictionaries."""

    def __init__(self, validator: Optional[FactorDSLValidator] = None):
        self.validator = validator or FactorDSLValidator()
        self._ast_cache: Dict[str, ast.AST] = {}

    def evaluate(
        self,
        expression: str,
        price_dict: Dict[str, pd.DataFrame],
    ) -> ExpressionEvalResult:
        """
        Evaluate expression on price_dict and return cross-sectional scores.

        Args:
            expression: DSL expression
            price_dict: {symbol: sliced price df}, each df is <= current date
        """
        validation = self.validator.validate(expression)
        if not validation.is_valid:
            return ExpressionEvalResult(
                expression=expression,
                normalized_expression="",
                scores={},
                is_valid=False,
                issues=[f"{i.code}: {i.message}" for i in validation.issues],
            )

        normalized = validation.normalized_expression
        tree = self._get_ast(normalized)
        symbols = sorted(price_dict.keys())
        if not symbols:
            return ExpressionEvalResult(
                expression=expression,
                normalized_expression=normalized,
                scores={},
                is_valid=True,
            )

        kind, value = self._eval_node(tree.body, price_dict, symbols)
        scores = self._to_scalar_map(kind, value, symbols)
        clean = {s: float(v) for s, v in scores.items() if np.isfinite(v)}

        return ExpressionEvalResult(
            expression=expression,
            normalized_expression=normalized,
            scores=clean,
            is_valid=True,
        )

    def _get_ast(self, normalized_expression: str) -> ast.AST:
        tree = self._ast_cache.get(normalized_expression)
        if tree is None:
            tree = ast.parse(normalized_expression, mode="eval")
            self._ast_cache[normalized_expression] = tree
        return tree

    def _eval_node(
        self,
        node: ast.AST,
        price_dict: Dict[str, pd.DataFrame],
        symbols: List[str],
    ) -> Tuple[ValueKind, EvalValue]:
        if isinstance(node, ast.Name):
            return "series", self._name_to_series(node.id, price_dict, symbols)

        if self._is_number_node(node):
            value = float(self._read_number(node))
            return "scalar", {s: value for s in symbols}

        if isinstance(node, ast.Call):
            return self._eval_call(node, price_dict, symbols)

        raise ValueError(f"Unsupported AST node during evaluation: {type(node).__name__}")

    def _eval_call(
        self,
        node: ast.Call,
        price_dict: Dict[str, pd.DataFrame],
        symbols: List[str],
    ) -> Tuple[ValueKind, EvalValue]:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Function reference must be a plain name.")

        fn = node.func.id
        args = node.args

        # unary
        if fn in {"abs", "normalize", "rank_cs"}:
            if len(args) != 1:
                raise ValueError(f"{fn} expects 1 arg.")
            kind, value = self._eval_node(args[0], price_dict, symbols)
            if fn == "abs":
                return self._apply_abs(kind, value, symbols)
            scalar = self._to_scalar_map(kind, value, symbols)
            if fn == "rank_cs":
                return "scalar", self._rank_cs(scalar)
            return "scalar", self._normalize_cs(scalar)

        # binary
        if fn in {"add", "sub", "mul", "div"}:
            if len(args) != 2:
                raise ValueError(f"{fn} expects 2 args.")
            lk, lv = self._eval_node(args[0], price_dict, symbols)
            rk, rv = self._eval_node(args[1], price_dict, symbols)
            return self._apply_binary(fn, lk, lv, rk, rv, symbols)

        # windowed funcs
        if fn in {"ret_n", "vol_n", "rsi", "sma", "ema", "zscore", "ts_rank", "lag", "delta"}:
            if len(args) != 2:
                raise ValueError(f"{fn} expects 2 args.")
            kind, value = self._eval_node(args[0], price_dict, symbols)
            series = self._ensure_series_map(kind, value, price_dict, symbols)
            window = int(self._read_number(args[1]))
            return "series", self._apply_windowed(fn, series, window)

        # clip(expr, lo, hi)
        if fn == "clip":
            if len(args) != 3:
                raise ValueError("clip expects 3 args.")
            kind, value = self._eval_node(args[0], price_dict, symbols)
            lo = float(self._read_number(args[1]))
            hi = float(self._read_number(args[2]))
            if kind == "series":
                return "series", {s: v.clip(lower=lo, upper=hi) for s, v in value.items()}
            return "scalar", {s: float(np.clip(v, lo, hi)) for s, v in value.items()}

        # macd(expr, fast, slow, signal)
        if fn == "macd":
            if len(args) != 4:
                raise ValueError("macd expects 4 args.")
            kind, value = self._eval_node(args[0], price_dict, symbols)
            series = self._ensure_series_map(kind, value, price_dict, symbols)
            fast = int(self._read_number(args[1]))
            slow = int(self._read_number(args[2]))
            signal = int(self._read_number(args[3]))
            out: SeriesMap = {}
            for sym, s in series.items():
                ema_fast = s.ewm(span=fast, adjust=False, min_periods=fast).mean()
                ema_slow = s.ewm(span=slow, adjust=False, min_periods=slow).mean()
                macd_line = ema_fast - ema_slow
                signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
                out[sym] = macd_line - signal_line
            return "series", out

        # atr(high, low, close, n)
        if fn == "atr":
            if len(args) != 4:
                raise ValueError("atr expects 4 args.")
            h_kind, h = self._eval_node(args[0], price_dict, symbols)
            l_kind, l = self._eval_node(args[1], price_dict, symbols)
            c_kind, c = self._eval_node(args[2], price_dict, symbols)
            high = self._ensure_series_map(h_kind, h, price_dict, symbols)
            low = self._ensure_series_map(l_kind, l, price_dict, symbols)
            close = self._ensure_series_map(c_kind, c, price_dict, symbols)
            n = int(self._read_number(args[3]))

            out: SeriesMap = {}
            for sym in symbols:
                hi = high[sym]
                lo = low[sym]
                cl = close[sym]
                prev_close = cl.shift(1)
                tr = pd.concat(
                    [
                        (hi - lo).abs(),
                        (hi - prev_close).abs(),
                        (lo - prev_close).abs(),
                    ],
                    axis=1,
                ).max(axis=1)
                out[sym] = tr.rolling(n, min_periods=n).mean()
            return "series", out

        raise ValueError(f"Unsupported function: {fn}")

    def _apply_windowed(self, fn: str, series_map: SeriesMap, window: int) -> SeriesMap:
        out: SeriesMap = {}
        for sym, s in series_map.items():
            if fn == "ret_n":
                out[sym] = s.pct_change(window)
            elif fn == "vol_n":
                out[sym] = s.pct_change().rolling(window, min_periods=window).std()
            elif fn == "rsi":
                out[sym] = self._rsi(s, window)
            elif fn == "sma":
                out[sym] = s.rolling(window, min_periods=window).mean()
            elif fn == "ema":
                out[sym] = s.ewm(span=window, adjust=False, min_periods=window).mean()
            elif fn == "zscore":
                mean = s.rolling(window, min_periods=window).mean()
                std = s.rolling(window, min_periods=window).std(ddof=0)
                out[sym] = (s - mean) / std.replace(0.0, np.nan)
            elif fn == "ts_rank":
                out[sym] = s.rolling(window, min_periods=window).apply(
                    lambda x: float(pd.Series(x).rank(pct=True).iloc[-1]),
                    raw=False,
                )
            elif fn == "lag":
                out[sym] = s.shift(window)
            elif fn == "delta":
                out[sym] = s - s.shift(window)
            else:
                raise ValueError(f"Unexpected windowed func: {fn}")
        return out

    def _apply_abs(self, kind: ValueKind, value: EvalValue, symbols: List[str]) -> Tuple[ValueKind, EvalValue]:
        if kind == "series":
            return "series", {s: v.abs() for s, v in value.items()}
        return "scalar", {s: abs(float(value[s])) for s in symbols}

    def _apply_binary(
        self,
        fn: str,
        left_kind: ValueKind,
        left: EvalValue,
        right_kind: ValueKind,
        right: EvalValue,
        symbols: List[str],
    ) -> Tuple[ValueKind, EvalValue]:
        if left_kind == "series" or right_kind == "series":
            lmap = self._ensure_series_map(left_kind, left, {}, symbols)
            rmap = self._ensure_series_map(right_kind, right, {}, symbols, template=lmap)
            out: SeriesMap = {}
            for sym in symbols:
                ls = lmap[sym]
                rs = rmap[sym]
                if fn == "add":
                    out[sym] = ls + rs
                elif fn == "sub":
                    out[sym] = ls - rs
                elif fn == "mul":
                    out[sym] = ls * rs
                elif fn == "div":
                    out[sym] = ls / rs.replace(0.0, np.nan)
            return "series", out

        # scalar x scalar
        out_scalar: ScalarMap = {}
        for sym in symbols:
            lv = float(left[sym])
            rv = float(right[sym])
            if fn == "add":
                out_scalar[sym] = lv + rv
            elif fn == "sub":
                out_scalar[sym] = lv - rv
            elif fn == "mul":
                out_scalar[sym] = lv * rv
            elif fn == "div":
                out_scalar[sym] = lv / rv if rv != 0 else np.nan
        return "scalar", out_scalar

    def _to_scalar_map(
        self,
        kind: ValueKind,
        value: EvalValue,
        symbols: List[str],
    ) -> ScalarMap:
        if kind == "scalar":
            return {s: float(value[s]) for s in symbols}
        return self._series_to_scalar_map(value, symbols)

    def _series_to_scalar_map(self, series_map: SeriesMap, symbols: Iterable[str]) -> ScalarMap:
        out: ScalarMap = {}
        for sym in symbols:
            s = series_map.get(sym)
            if s is None:
                out[sym] = np.nan
                continue
            valid = s.dropna()
            out[sym] = float(valid.iloc[-1]) if not valid.empty else np.nan
        return out

    def _ensure_series_map(
        self,
        kind: ValueKind,
        value: EvalValue,
        price_dict: Dict[str, pd.DataFrame],
        symbols: List[str],
        template: Optional[SeriesMap] = None,
    ) -> SeriesMap:
        if kind == "series":
            return value  # type: ignore[return-value]

        # scalar -> series broadcast
        out: SeriesMap = {}
        for sym in symbols:
            if template is not None and sym in template:
                idx = template[sym].index
            elif sym in price_dict and not price_dict[sym].empty:
                idx = price_dict[sym].index
            else:
                idx = pd.RangeIndex(0)
            out[sym] = pd.Series(float(value[sym]), index=idx, dtype=float)
        return out

    def _name_to_series(
        self,
        name: str,
        price_dict: Dict[str, pd.DataFrame],
        symbols: List[str],
    ) -> SeriesMap:
        out: SeriesMap = {}
        for sym in symbols:
            df = price_dict.get(sym)
            if df is None or df.empty:
                out[sym] = pd.Series(dtype=float)
                continue
            if name not in df.columns:
                out[sym] = pd.Series(np.nan, index=df.index, dtype=float)
                continue
            out[sym] = pd.to_numeric(df[name], errors="coerce").astype(float)
        return out

    def _rank_cs(self, values: ScalarMap) -> ScalarMap:
        s = pd.Series(values, dtype=float)
        ranked = s.rank(method="average", pct=True)
        return {k: float(v) for k, v in ranked.items()}

    def _normalize_cs(self, values: ScalarMap) -> ScalarMap:
        s = pd.Series(values, dtype=float)
        mean = float(s.mean())
        std = float(s.std(ddof=0))
        if std <= 1e-12:
            return {k: 0.0 for k in s.index}
        z = (s - mean) / std
        return {k: float(v) for k, v in z.items()}

    @staticmethod
    def _rsi(series: pd.Series, window: int) -> pd.Series:
        delta = series.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        roll_up = up.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
        roll_down = down.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
        rs = roll_up / roll_down.replace(0.0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _is_number_node(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return True
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.UAdd, ast.USub))
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
        ):
            return True
        return False

    @staticmethod
    def _read_number(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.UAdd, ast.USub))
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
        ):
            value = float(node.operand.value)
            return value if isinstance(node.op, ast.UAdd) else -value
        raise ValueError("Expected numeric constant.")

