from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from backtest.pipeline.paths import resolve_repo_root, resolve_shared_data_root
from backtest.pipeline.primitives.evaluation import EvaluationEngine
from backtest.pipeline.primitives.execution import ExecutionEngine
from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.primitives.portfolio_builder import PortfolioBuilder
from backtest.pipeline.primitives.signal_engine import SignalEngine
from backtest.pipeline.primitives.universe_builder import UniverseBuilder
from backtest.pipeline.spec import StrategySpec
from backtest.pipeline.types import PipelineResult


class PipelineRunner:
    def __init__(
        self,
        spec_path: str | Path,
        artifact_root: Optional[str | Path] = None,
        market_db_path: Optional[str | Path] = None,
        company_db_path: Optional[str | Path] = None,
        initial_capital: float = 100_000.0,
    ):
        self.spec_path = Path(spec_path)
        self.spec = StrategySpec.from_yaml(self.spec_path)
        self.repo_root = resolve_repo_root()
        self.shared_root = resolve_shared_data_root()
        self.artifact_root = Path(artifact_root) if artifact_root is not None else self.repo_root / "reports" / "backtest"
        self.market_db_path = Path(market_db_path) if market_db_path is not None else None
        self.company_db_path = Path(company_db_path) if company_db_path is not None else None
        self.initial_capital = initial_capital

    def compute_spec_hash(self) -> str:
        return hashlib.sha256(self.spec.to_json().encode("utf-8")).hexdigest()[:16]

    def build_period_split(self) -> dict[str, str]:
        return {
            "start": self.spec.period.start.isoformat(),
            "train_end": self.spec.period.train_end.isoformat(),
            "test_end": self.spec.period.test_end.isoformat(),
        }

    def _slice_frame(
        self,
        frame: pd.DataFrame,
        after_date: Optional[str],
        end_date: Optional[str],
    ) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        sliced = frame.copy()
        date_index = pd.Series(sliced.index.astype(str), index=sliced.index)
        if after_date is not None:
            sliced = sliced.loc[date_index > after_date]
            date_index = pd.Series(sliced.index.astype(str), index=sliced.index)
        if end_date is not None:
            sliced = sliced.loc[date_index <= end_date]
        return sliced

    def _write_frame_artifact(
        self,
        path: Path,
        frame: pd.DataFrame,
        warnings: list[str],
    ) -> None:
        try:
            frame.to_parquet(path)
        except Exception:
            fallback_path = path.with_suffix(".json")
            fallback_path.write_text(frame.to_json(orient="table"), encoding="utf-8")
            path.write_text(
                f"PARQUET_UNAVAILABLE\nSee {fallback_path.name} for JSON fallback.\n",
                encoding="utf-8",
            )
            warnings.append(
                f"{path.name}: parquet engine unavailable, wrote JSON fallback {fallback_path.name}"
            )

    def run(self) -> PipelineResult:
        """Execute the V3 research pipeline.

        IS and OOS are intentionally run as two independent capital paths:
        OOS starts from fresh capital and does not inherit IS positions.
        """
        spec_hash = self.compute_spec_hash()
        artifact_dir = self.artifact_root / f"{self.spec.spec_id}_{spec_hash}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []

        spec_snapshot = artifact_dir / "spec.yaml"
        spec_snapshot.write_text(self.spec_path.read_text(encoding="utf-8"), encoding="utf-8")

        split_manifest = artifact_dir / "split.json"
        split_manifest.write_text(
            json.dumps(self.build_period_split(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        pit_data = PitData(
            market_db_path=self.market_db_path,
            company_db_path=self.company_db_path,
        )
        universe_builder = UniverseBuilder(
            market_db_path=pit_data.market_db_path,
            company_db_path=pit_data.company_db_path,
        )
        universe_result = universe_builder.build(
            start_date=self.spec.period.start.isoformat(),
            end_date=self.spec.period.test_end.isoformat(),
            rebalance=self.spec.portfolio.rebalance,
            market_cap_min_usd=self.spec.universe.market_cap_min_usd,
            exclude_sectors=self.spec.universe.exclude_sectors,
            min_names=self.spec.universe.min_names,
        )
        warnings.extend(universe_result.warnings)

        signal_engine = SignalEngine(pit_data)
        signal_result = signal_engine.compute(
            factors=self.spec.factors,
            combo=self.spec.combo,
            universe_df=universe_result.universe_df,
        )

        signals_is = self._slice_frame(
            signal_result.combo_frame,
            after_date=None,
            end_date=self.spec.period.train_end.isoformat(),
        )
        signals_oos = self._slice_frame(
            signal_result.combo_frame,
            after_date=self.spec.period.train_end.isoformat(),
            end_date=self.spec.period.test_end.isoformat(),
        )

        portfolio_builder = PortfolioBuilder(pit_data)
        target_is = portfolio_builder.build_target_weights(signals_is, self.spec.portfolio)
        target_oos = portfolio_builder.build_target_weights(signals_oos, self.spec.portfolio)

        execution_engine = ExecutionEngine(pit_data, initial_capital=self.initial_capital)
        run_is = execution_engine.run(
            target_weights=target_is,
            benchmark_symbol=self.spec.benchmark,
            execution=self.spec.execution,
            start_date=universe_result.effective_start,
            end_date=self.spec.period.train_end.isoformat(),
        )
        oos_start = min(signals_oos.index.astype(str)) if not signals_oos.empty else self.spec.period.train_end.isoformat()
        run_oos = execution_engine.run(
            target_weights=target_oos,
            benchmark_symbol=self.spec.benchmark,
            execution=self.spec.execution,
            start_date=str(oos_start),
            end_date=self.spec.period.test_end.isoformat(),
        )

        signals_is_path = artifact_dir / "signals_is.parquet"
        signals_oos_path = artifact_dir / "signals_oos.parquet"
        nav_is_path = artifact_dir / "nav_is.parquet"
        nav_oos_path = artifact_dir / "nav_oos.parquet"
        metrics_path = artifact_dir / "metrics.json"
        report_md_path = artifact_dir / "report.md"
        report_html_path = artifact_dir / "report.html"

        self._write_frame_artifact(signals_is_path, signals_is, warnings)
        self._write_frame_artifact(signals_oos_path, signals_oos, warnings)
        self._write_frame_artifact(nav_is_path, run_is.nav, warnings)
        self._write_frame_artifact(nav_oos_path, run_oos.nav, warnings)

        evaluation = EvaluationEngine(pit_data)
        evaluation_output = evaluation.evaluate(
            spec=self.spec,
            factor_frames=signal_result.factor_frames,
            combo_frame=signal_result.combo_frame,
            run_is=run_is,
            run_oos=run_oos,
            warnings=warnings,
        )
        metrics = evaluation_output.metrics
        metrics["shared_root"] = str(self.shared_root)

        metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report_md_path.write_text(evaluation_output.report_markdown, encoding="utf-8")
        report_html_path.write_text(evaluation_output.report_html, encoding="utf-8")

        result = PipelineResult(
            spec_hash=spec_hash,
            artifact_dir=artifact_dir,
            warnings=warnings,
            metrics=metrics,
            output_paths={
                "spec_snapshot": spec_snapshot,
                "split_manifest": split_manifest,
                "signals_is": signals_is_path,
                "signals_oos": signals_oos_path,
                "nav_is": nav_is_path,
                "nav_oos": nav_oos_path,
                "metrics_json": metrics_path,
                "report_md": report_md_path,
                "report_html": report_html_path,
            },
            universe_df=universe_result.universe_df,
            signals_is=signals_is,
            signals_oos=signals_oos,
            nav_is=run_is.nav,
            nav_oos=run_oos.nav,
        )
        return result
