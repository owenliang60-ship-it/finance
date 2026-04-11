from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


class SpecValidationError(ValueError):
    """Raised when a pipeline spec is invalid."""


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise SpecValidationError(f"Expected date string, got {type(value).__name__}")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SpecValidationError(f"Invalid date format: {value!r}") from exc


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    if text in {"null", "Null", "NULL", "~"}:
        return None
    if text in {"true", "True"}:
        return True
    if text in {"false", "False"}:
        return False
    if text.startswith(('"', "'", "[", "{")):
        try:
            return ast.literal_eval(text)
        except Exception:
            return text.strip("'\"")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _load_yaml_subset(text: str) -> Any:
    """
    Tiny YAML subset loader.

    Supports the subset used by pipeline specs:
    - nested mappings by indentation
    - lists introduced with "-"
    - inline scalar values / JSON-like [] and {}
    """

    cleaned: List[tuple[int, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        cleaned.append((indent, line.lstrip(" ")))

    if not cleaned:
        return {}

    def parse_map(start: int, indent: int) -> tuple[Dict[str, Any], int]:
        result: Dict[str, Any] = {}
        idx = start
        while idx < len(cleaned):
            current_indent, stripped = cleaned[idx]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise SpecValidationError(f"Unexpected indentation near: {stripped!r}")
            if stripped.startswith("- "):
                raise SpecValidationError(f"Unexpected list item in mapping: {stripped!r}")
            if ":" not in stripped:
                raise SpecValidationError(f"Expected key/value pair, got: {stripped!r}")

            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()
            idx += 1

            if raw_value == "":
                if idx < len(cleaned) and cleaned[idx][0] > indent:
                    next_indent = cleaned[idx][0]
                    if cleaned[idx][1].startswith("- "):
                        value, idx = parse_list(idx, next_indent)
                    else:
                        value, idx = parse_map(idx, next_indent)
                else:
                    value = None
            else:
                value = _parse_scalar(raw_value)
            result[key] = value
        return result, idx

    def parse_list(start: int, indent: int) -> tuple[List[Any], int]:
        items: List[Any] = []
        idx = start
        while idx < len(cleaned):
            current_indent, stripped = cleaned[idx]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise SpecValidationError(f"Unexpected indentation near list item: {stripped!r}")
            if not stripped.startswith("- "):
                break

            item_text = stripped[2:].strip()
            idx += 1
            if item_text == "":
                if idx >= len(cleaned) or cleaned[idx][0] <= indent:
                    items.append(None)
                    continue
                next_indent = cleaned[idx][0]
                if cleaned[idx][1].startswith("- "):
                    item, idx = parse_list(idx, next_indent)
                else:
                    item, idx = parse_map(idx, next_indent)
                items.append(item)
                continue

            if ":" in item_text and not item_text.startswith(('"', "'", "[", "{")):
                key, raw_value = item_text.split(":", 1)
                item: Dict[str, Any] = {key.strip(): _parse_scalar(raw_value.strip()) if raw_value.strip() else None}
                if idx < len(cleaned) and cleaned[idx][0] > indent:
                    extra, idx = parse_map(idx, cleaned[idx][0])
                    item.update(extra)
                items.append(item)
            else:
                items.append(_parse_scalar(item_text))
        return items, idx

    first_indent, first_text = cleaned[0]
    if first_text.startswith("- "):
        value, end = parse_list(0, first_indent)
    else:
        value, end = parse_map(0, first_indent)
    if end != len(cleaned):
        raise SpecValidationError("Failed to parse entire spec file")
    return value


def _load_spec_mapping(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        raise SpecValidationError("Spec file is empty")

    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SpecValidationError(f"Invalid JSON spec: {exc}") from exc
        if not isinstance(payload, dict):
            raise SpecValidationError("Spec root must be a mapping")
        return payload

    payload = _load_yaml_subset(text)
    if not isinstance(payload, dict):
        raise SpecValidationError("Spec root must be a mapping")
    return payload


@dataclass
class FactorInput:
    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    transform: Literal["raw", "rank_pct", "zscore"] = "raw"
    weight: float = 1.0
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FactorInput":
        return cls(
            name=str(payload["name"]),
            params=dict(payload.get("params", {})),
            transform=payload.get("transform", "raw"),
            weight=float(payload.get("weight", 1.0)),
            direction=payload.get("direction", "higher_is_better"),
        )


@dataclass
class ComboSpec:
    method: Literal["single", "weighted_sum", "rank_average"]

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ComboSpec":
        return cls(method=payload["method"])


@dataclass
class UniverseSpec:
    market_cap_min_usd: float
    exclude_sectors: List[str] = field(default_factory=list)
    min_names: int = 20

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "UniverseSpec":
        return cls(
            market_cap_min_usd=float(payload["market_cap_min_usd"]),
            exclude_sectors=list(payload.get("exclude_sectors", [])),
            min_names=int(payload.get("min_names", 20)),
        )


@dataclass
class PortfolioSpec:
    selection: Literal["top_n", "threshold"]
    top_n: Optional[int] = None
    threshold: Optional[float] = None
    rebalance: Literal["weekly", "monthly_first_trading_day"] = "monthly_first_trading_day"
    weighting: Literal["equal", "inv_vol"] = "equal"
    vol_lookback_days: int = 60
    max_position_weight: float = 1.0
    max_annual_turnover: Optional[float] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PortfolioSpec":
        return cls(
            selection=payload["selection"],
            top_n=int(payload["top_n"]) if payload.get("top_n") is not None else None,
            threshold=float(payload["threshold"]) if payload.get("threshold") is not None else None,
            rebalance=payload.get("rebalance", "monthly_first_trading_day"),
            weighting=payload.get("weighting", "equal"),
            vol_lookback_days=int(payload.get("vol_lookback_days", 60)),
            max_position_weight=float(payload.get("max_position_weight", 1.0)),
            max_annual_turnover=float(payload["max_annual_turnover"]) if payload.get("max_annual_turnover") is not None else None,
        )


@dataclass
class ExecutionSpec:
    timing: Literal["next_open"] = "next_open"
    transaction_cost_bps: float = 0.0
    spread_bps: float = 0.0

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExecutionSpec":
        return cls(
            timing=payload.get("timing", "next_open"),
            transaction_cost_bps=float(payload.get("transaction_cost_bps", 0.0)),
            spread_bps=float(payload.get("spread_bps", 0.0)),
        )


@dataclass
class EvaluationSpec:
    newey_west_lag_days: Optional[int] = None

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "EvaluationSpec":
        payload = payload or {}
        lag = payload.get("newey_west_lag_days")
        return cls(newey_west_lag_days=int(lag) if lag is not None else None)


@dataclass
class PeriodSpec:
    start: date
    train_end: date
    test_end: date

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PeriodSpec":
        return cls(
            start=_parse_date(payload["start"]),
            train_end=_parse_date(payload["train_end"]),
            test_end=_parse_date(payload["test_end"]),
        )


@dataclass
class StrategySpec:
    spec_id: str
    benchmark: str
    universe: UniverseSpec
    factors: List[FactorInput]
    combo: ComboSpec
    portfolio: PortfolioSpec
    execution: ExecutionSpec
    evaluation: EvaluationSpec
    period: PeriodSpec
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategySpec":
        required = ["spec_id", "benchmark", "universe", "factors", "combo", "portfolio", "execution", "period"]
        missing = [key for key in required if key not in payload]
        if missing:
            raise SpecValidationError(f"Missing required spec fields: {', '.join(missing)}")

        spec = cls(
            spec_id=str(payload["spec_id"]),
            benchmark=str(payload["benchmark"]),
            universe=UniverseSpec.from_dict(payload["universe"]),
            factors=[FactorInput.from_dict(item) for item in payload["factors"]],
            combo=ComboSpec.from_dict(payload["combo"]),
            portfolio=PortfolioSpec.from_dict(payload["portfolio"]),
            execution=ExecutionSpec.from_dict(payload["execution"]),
            evaluation=EvaluationSpec.from_dict(payload.get("evaluation")),
            period=PeriodSpec.from_dict(payload["period"]),
            notes=payload.get("notes"),
        )
        spec.validate()
        return spec

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StrategySpec":
        path = Path(path)
        return cls.from_dict(_load_spec_mapping(path))

    def validate(self) -> None:
        if not (1 <= len(self.factors) <= 3):
            raise SpecValidationError("StrategySpec.factors must contain between 1 and 3 factors")
        if self.combo.method == "single" and len(self.factors) != 1:
            raise SpecValidationError("combo.method='single' requires exactly 1 factor")
        if self.execution.timing != "next_open":
            raise SpecValidationError("Only execution.timing='next_open' is supported in V3")
        if self.portfolio.selection == "top_n" and self.portfolio.top_n is None:
            raise SpecValidationError("portfolio.top_n is required when selection='top_n'")
        if self.portfolio.selection == "threshold" and self.portfolio.threshold is None:
            raise SpecValidationError("portfolio.threshold is required when selection='threshold'")
        if self.portfolio.selection == "top_n" and self.portfolio.threshold is not None:
            raise SpecValidationError("portfolio.threshold must be omitted when selection='top_n'")
        if self.portfolio.selection == "threshold" and self.portfolio.top_n is not None:
            raise SpecValidationError("portfolio.top_n must be omitted when selection='threshold'")
        if self.portfolio.weighting == "inv_vol" and self.portfolio.vol_lookback_days <= 0:
            raise SpecValidationError("portfolio.vol_lookback_days must be > 0 for inv_vol weighting")
        if self.period.train_end >= self.period.test_end:
            raise SpecValidationError("period.train_end must be before period.test_end")
        if self.period.start >= self.period.train_end:
            raise SpecValidationError("period.start must be before period.train_end")

    def resolved_newey_west_lag_days(self) -> int:
        if self.evaluation.newey_west_lag_days is not None:
            return self.evaluation.newey_west_lag_days
        if self.portfolio.rebalance == "weekly":
            return 5
        return 21

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["period"]["start"] = self.period.start.isoformat()
        payload["period"]["train_end"] = self.period.train_end.isoformat()
        payload["period"]["test_end"] = self.period.test_end.isoformat()
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
