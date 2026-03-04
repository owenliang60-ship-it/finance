"""
DSL parser and validator for autonomous factor candidates.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from factor_research_codex1.miner.feature_grammar import (
    BINARY_FUNCS,
    UNARY_FUNCS,
    WINDOW_FUNCS,
    is_allowed_function,
    is_allowed_identifier,
)


@dataclass(frozen=True)
class ValidationIssue:
    """Single validation issue."""

    code: str
    message: str


@dataclass
class FactorValidationResult:
    """Validation output."""

    expression: str
    is_valid: bool
    normalized_expression: str = ""
    depth: int = 0
    node_count: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)


class FactorDSLValidator:
    """
    Validate DSL expressions under a strict whitelist.

    The validator is intentionally restrictive:
    - function-call only
    - no attributes/subscripts/lambdas/comprehensions
    - known identifiers and known function signatures only
    """

    def __init__(
        self,
        max_depth: int = 4,
        max_window: int = 252,
        min_window: int = 1,
    ):
        self.max_depth = max_depth
        self.max_window = min_window if max_window < min_window else max_window
        self.min_window = min_window

    def validate(self, expression: str) -> FactorValidationResult:
        expression = expression.strip()
        issues: List[ValidationIssue] = []

        if not expression:
            issues.append(ValidationIssue("empty_expression", "Expression is empty."))
            return FactorValidationResult(
                expression=expression,
                is_valid=False,
                issues=issues,
            )

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            issues.append(
                ValidationIssue(
                    "syntax_error",
                    f"Invalid syntax: {exc.msg}",
                )
            )
            return FactorValidationResult(
                expression=expression,
                is_valid=False,
                issues=issues,
            )

        depth, node_count = self._validate_node(tree.body, issues)

        if depth > self.max_depth:
            issues.append(
                ValidationIssue(
                    "depth_exceeded",
                    f"Expression depth {depth} exceeds max_depth={self.max_depth}.",
                )
            )

        normalized = ""
        if not issues:
            # Safe after parse + whitelist checks.
            normalized = ast.unparse(tree.body)

        return FactorValidationResult(
            expression=expression,
            is_valid=not issues,
            normalized_expression=normalized,
            depth=depth,
            node_count=node_count,
            issues=issues,
        )

    def _validate_node(self, node: ast.AST, issues: List[ValidationIssue]) -> Tuple[int, int]:
        if isinstance(node, ast.Name):
            if not is_allowed_identifier(node.id):
                issues.append(
                    ValidationIssue(
                        "unknown_identifier",
                        f"Identifier not allowed: {node.id!r}.",
                    )
                )
            return 0, 1

        if self._is_number_node(node):
            return 0, 1

        if isinstance(node, ast.Call):
            depth, count = self._validate_call(node, issues)
            return depth, count

        # Reject all other AST node kinds.
        issues.append(
            ValidationIssue(
                "unsupported_node",
                f"Unsupported syntax node: {type(node).__name__}.",
            )
        )
        return 0, 1

    def _validate_call(self, node: ast.Call, issues: List[ValidationIssue]) -> Tuple[int, int]:
        if not isinstance(node.func, ast.Name):
            issues.append(
                ValidationIssue(
                    "invalid_function_ref",
                    "Function reference must be a plain function name.",
                )
            )
            return 1, 1

        fn = node.func.id
        if not is_allowed_function(fn):
            issues.append(
                ValidationIssue(
                    "unknown_function",
                    f"Function not allowed: {fn!r}.",
                )
            )

        if node.keywords:
            issues.append(
                ValidationIssue(
                    "keyword_not_allowed",
                    "Keyword arguments are not allowed in DSL.",
                )
            )

        # Validate function-specific signature.
        self._validate_signature(fn, node.args, issues)

        child_depths = []
        node_count = 1
        for arg in node.args:
            d, c = self._validate_node(arg, issues)
            child_depths.append(d)
            node_count += c

        return 1 + (max(child_depths) if child_depths else 0), node_count

    def _validate_signature(self, fn: str, args: List[ast.AST], issues: List[ValidationIssue]) -> None:
        if fn in UNARY_FUNCS:
            self._check_arity(fn, args, expected=1, issues=issues)
            return

        if fn in BINARY_FUNCS:
            self._check_arity(fn, args, expected=2, issues=issues)
            return

        if fn in WINDOW_FUNCS:
            self._check_arity(fn, args, expected=2, issues=issues)
            if len(args) == 2:
                self._check_window_arg(fn, args[1], issues)
            return

        if fn == "clip":
            self._check_arity(fn, args, expected=3, issues=issues)
            if len(args) == 3:
                lo = self._read_number(args[1])
                hi = self._read_number(args[2])
                if lo is None or hi is None:
                    issues.append(
                        ValidationIssue(
                            "clip_bounds_not_numeric",
                            "clip bounds must be numeric constants.",
                        )
                    )
                elif lo >= hi:
                    issues.append(
                        ValidationIssue(
                            "clip_bounds_invalid",
                            "clip lower bound must be strictly smaller than upper bound.",
                        )
                    )
            return

        if fn == "macd":
            self._check_arity(fn, args, expected=4, issues=issues)
            if len(args) == 4:
                fast = self._read_integer(args[1])
                slow = self._read_integer(args[2])
                signal = self._read_integer(args[3])
                if fast is None or slow is None or signal is None:
                    issues.append(
                        ValidationIssue(
                            "macd_params_invalid",
                            "macd parameters must be integer constants.",
                        )
                    )
                else:
                    for name, val in (("fast", fast), ("slow", slow), ("signal", signal)):
                        self._check_window_value(f"macd.{name}", val, issues)
                    if fast is not None and slow is not None and fast >= slow:
                        issues.append(
                            ValidationIssue(
                                "macd_order_invalid",
                                "macd requires fast < slow.",
                            )
                        )
            return

        if fn == "atr":
            self._check_arity(fn, args, expected=4, issues=issues)
            if len(args) == 4:
                expected_names = ("high", "low", "close")
                for i, expected in enumerate(expected_names):
                    name = args[i]
                    if not isinstance(name, ast.Name) or name.id != expected:
                        issues.append(
                            ValidationIssue(
                                "atr_input_invalid",
                                "atr signature must be atr(high, low, close, window).",
                            )
                        )
                        break
                self._check_window_arg("atr", args[3], issues)
            return

    def _check_arity(self, fn: str, args: List[ast.AST], expected: int, issues: List[ValidationIssue]) -> None:
        if len(args) != expected:
            issues.append(
                ValidationIssue(
                    "arity_mismatch",
                    f"{fn} expects {expected} args, got {len(args)}.",
                )
            )

    def _check_window_arg(self, fn: str, node: ast.AST, issues: List[ValidationIssue]) -> None:
        window = self._read_integer(node)
        if window is None:
            issues.append(
                ValidationIssue(
                    "window_not_integer",
                    f"{fn} window must be an integer constant.",
                )
            )
            return
        self._check_window_value(fn, window, issues)

    def _check_window_value(self, fn: str, window: int, issues: List[ValidationIssue]) -> None:
        if window < self.min_window or window > self.max_window:
            issues.append(
                ValidationIssue(
                    "window_out_of_range",
                    (
                        f"{fn} window={window} out of range "
                        f"[{self.min_window}, {self.max_window}]."
                    ),
                )
            )

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
    def _read_number(node: ast.AST) -> Optional[float]:
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
        return None

    @staticmethod
    def _read_integer(node: ast.AST) -> Optional[int]:
        value = FactorDSLValidator._read_number(node)
        if value is None:
            return None
        if abs(value - int(value)) > 1e-12:
            return None
        return int(value)

