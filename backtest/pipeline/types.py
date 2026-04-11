from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class UniverseBuildResult:
    universe_df: pd.DataFrame
    effective_start: str
    rebalance_dates: List[str]
    warnings: List[str] = field(default_factory=list)


@dataclass
class SignalComputationResult:
    factor_frames: Dict[str, pd.DataFrame]
    combo_frame: pd.DataFrame


@dataclass
class BacktestRunResult:
    nav: pd.DataFrame
    trades: pd.DataFrame
    positions_daily: pd.DataFrame
    benchmark_nav: pd.DataFrame
    total_costs: float
    annual_turnover: float = 0.0
    n_trades: int = 0


@dataclass
class EvaluationOutput:
    metrics: Dict[str, Any]
    report_markdown: str
    report_html: str


@dataclass
class PipelineResult:
    spec_hash: str
    artifact_dir: Path
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    output_paths: Dict[str, Path] = field(default_factory=dict)
    universe_df: Optional[pd.DataFrame] = None
    signals_is: Optional[pd.DataFrame] = None
    signals_oos: Optional[pd.DataFrame] = None
    nav_is: Optional[pd.DataFrame] = None
    nav_oos: Optional[pd.DataFrame] = None
