from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from backtest.adapters.us_stocks import USStocksAdapter
from backtest.event_study.protocol import (
    EventStudyConfig,
    StudyOutcome,
    SymbolDateStudyAdapter,
)
from backtest.event_study.report import (
    build_markdown_report_from_summary,
    build_summary_frame,
    merge_summary_frames,
    write_report_artifacts,
)
from backtest.event_study.returns import (
    build_t1open_excess_return_matrices,
    build_t1open_return_matrices,
)
from backtest.event_study.stats import (
    apply_bh_fdr,
    build_symbol_date_index,
    summarize_bucket_stats,
)
from backtest.event_study.universe import EventUniverseGate
from backtest.pipeline.paths import resolve_shared_data_root


class EventStudyRunner:
    """Minimal runner shell for the standardized event-study flow."""

    def __init__(self, config: EventStudyConfig, study: SymbolDateStudyAdapter):
        self._config = config
        self._study = study

    @property
    def config(self) -> EventStudyConfig:
        return self._config

    def with_study_window(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> "EventStudyRunner":
        return EventStudyRunner(
            replace(
                self._config,
                study_start_date=start_date,
                study_end_date=end_date,
            ),
            study=self._study,
        )

    def run(self, output_dir: Optional[str | Path] = None) -> StudyOutcome:
        study_symbols = self._resolve_study_symbols()
        price_adapter = USStocksAdapter(symbols=study_symbols)
        price_dict = price_adapter.load_all()
        computation_dates = self._filter_dates(price_adapter.get_trading_dates())

        gate = EventUniverseGate(
            config=self._config.universe,
            candidate_symbols=study_symbols,
        )
        eligibility = gate.build_eligibility_matrix(computation_dates)
        universe_audit = gate.build_universe_audit(
            eligibility,
            loaded_symbol_count=len(price_dict),
            json_universe_count=len(study_symbols),
        )

        feature_frames = self._study.build_feature_frames(price_dict)
        symbol_date_index = build_symbol_date_index(feature_frames)
        bucket_events = self._study.detect_events(feature_frames)

        raw_matrices = build_t1open_return_matrices(
            price_dict=price_dict,
            computation_dates=computation_dates,
            horizons=list(self._config.returns.horizons),
        )

        benchmark_adapter = USStocksAdapter(symbols=[self._config.returns.benchmark_symbol])
        benchmark_price_dict = benchmark_adapter.load_all()
        benchmark_df = benchmark_price_dict[self._config.returns.benchmark_symbol]
        excess_matrices = build_t1open_excess_return_matrices(
            price_dict=price_dict,
            benchmark_df=benchmark_df,
            computation_dates=computation_dates,
            horizons=list(self._config.returns.horizons),
        )

        filtered_full_events = {
            bucket_label: self._filter_events(
                events=events,
                eligibility=eligibility,
                start_date=self._config.study_start_date,
                end_date=self._config.study_end_date,
            )
            for bucket_label, events in bucket_events.items()
        }

        raw_results_by_window: Dict[str, list] = {}
        excess_results_by_window: Dict[str, list] = {}

        full_raw, full_excess = self._summarize_windows(
            filtered_full_events,
            raw_matrices,
            excess_matrices,
            symbol_date_index,
        )
        raw_results_by_window["Full"] = full_raw
        excess_results_by_window["Full"] = full_excess

        is_events, oos_events = self._split_events(filtered_full_events)
        if is_events:
            is_raw, is_excess = self._summarize_windows(
                is_events,
                raw_matrices,
                excess_matrices,
                symbol_date_index,
            )
            raw_results_by_window["IS"] = is_raw
            excess_results_by_window["IS"] = is_excess
        else:
            raw_results_by_window["IS"] = []
            excess_results_by_window["IS"] = []

        if oos_events:
            oos_raw, oos_excess = self._summarize_windows(
                oos_events,
                raw_matrices,
                excess_matrices,
                symbol_date_index,
            )
            raw_results_by_window["OOS"] = oos_raw
            excess_results_by_window["OOS"] = oos_excess
        else:
            raw_results_by_window["OOS"] = []
            excess_results_by_window["OOS"] = []

        raw_summary_df = build_summary_frame(raw_results_by_window)
        excess_summary_df = build_summary_frame(excess_results_by_window)
        summary_df = merge_summary_frames(raw_summary_df, excess_summary_df)

        event_level_df = self._build_event_level_frame(
            feature_frames=feature_frames,
            bucket_events=filtered_full_events,
            raw_matrices=raw_matrices,
            excess_matrices=excess_matrices,
        )

        notes = self._build_notes(universe_audit)
        report_markdown = build_markdown_report_from_summary(
            config=self._config,
            research_question=self._study.research_question(),
            universe_audit=universe_audit,
            summary_df=summary_df,
            notes=notes,
            failure_modes=[
                "若 OOS 样本过少，则不把显著性当成有效性证据。",
                "若 eligible count 在早年出现明显断崖，需优先排查 historical_market_cap 覆盖，而不是直接解读策略失效。",
            ],
            next_steps=[
                "若 RVOL 的 Full 与 OOS 同方向，再把同一协议迁到 PMARP/BBWP。",
                "若要引入 breadth，第二阶段先作为市场过滤器进入同一协议。",
            ],
        )

        output_root = self._resolve_output_dir(output_dir)
        artifact_paths = write_report_artifacts(
            output_dir=output_root,
            summary_df=summary_df,
            event_level_df=event_level_df,
            universe_audit=universe_audit,
            report_markdown=report_markdown,
        )

        return StudyOutcome(
            study_name=self._config.study_name,
            status="completed",
            summary_rows=tuple(summary_df.to_dict(orient="records")),
            artifact_paths=artifact_paths,
            notes=tuple(notes),
        )

    def _resolve_study_symbols(self) -> list[str]:
        data_root = resolve_shared_data_root()
        pool_root = data_root / "data" / "pool"
        universe_name = self._config.universe.universe_name
        if universe_name == "extended_true":
            active = _read_symbols(pool_root / "extended_universe.json")
            overlay = _read_symbols(pool_root / "delisted_large_caps.json")
            return sorted(set(active) | set(overlay))
        if universe_name == "extended":
            return _read_symbols(pool_root / "extended_universe.json")
        if universe_name == "pool":
            return _read_symbols(pool_root / "universe.json")
        raise ValueError(f"Unsupported universe_name for event study: {universe_name}")

    def _filter_dates(self, trading_dates: list[str]) -> list[str]:
        filtered = [str(value)[:10] for value in trading_dates]
        if self._config.study_start_date:
            filtered = [date for date in filtered if date >= self._config.study_start_date]
        if self._config.study_end_date:
            filtered = [date for date in filtered if date <= self._config.study_end_date]
        return filtered

    def _filter_events(
        self,
        events: Dict[str, list[str]],
        eligibility: pd.DataFrame,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Dict[str, list[str]]:
        filtered: Dict[str, list[str]] = {}
        eligible_dates = set(eligibility.index.astype(str))
        for symbol, dates in events.items():
            if symbol not in eligibility.columns:
                continue
            kept = []
            for date_str in dates:
                normalized = str(date_str)[:10]
                if start_date and normalized < start_date:
                    continue
                if end_date and normalized > end_date:
                    continue
                if normalized not in eligible_dates:
                    continue
                if bool(eligibility.loc[normalized, symbol]):
                    kept.append(normalized)
            if kept:
                filtered[symbol] = kept
        return filtered

    def _split_events(
        self,
        bucket_events: Dict[str, Dict[str, list[str]]],
    ) -> tuple[Dict[str, Dict[str, list[str]]], Dict[str, Dict[str, list[str]]]]:
        oos_start = self._config.report_split.oos_start_date
        if not oos_start:
            return {}, {}

        is_events: Dict[str, Dict[str, list[str]]] = {}
        oos_events: Dict[str, Dict[str, list[str]]] = {}
        for bucket_label, events in bucket_events.items():
            is_bucket: Dict[str, list[str]] = {}
            oos_bucket: Dict[str, list[str]] = {}
            for symbol, dates in events.items():
                is_dates = [date for date in dates if date < oos_start]
                oos_dates = [date for date in dates if date >= oos_start]
                if is_dates:
                    is_bucket[symbol] = is_dates
                if oos_dates:
                    oos_bucket[symbol] = oos_dates
            if is_bucket:
                is_events[bucket_label] = is_bucket
            if oos_bucket:
                oos_events[bucket_label] = oos_bucket
        return is_events, oos_events

    def _summarize_windows(
        self,
        bucket_events: Dict[str, Dict[str, list[str]]],
        raw_matrices: Dict[int, pd.DataFrame],
        excess_matrices: Dict[int, pd.DataFrame],
        symbol_date_index: Dict[str, Dict[str, int]],
    ) -> tuple[list, list]:
        raw_results = []
        excess_results = []
        for bucket_label, events in bucket_events.items():
            raw_results.extend(
                summarize_bucket_stats(
                    bucket_label=bucket_label,
                    events=events,
                    return_matrices=raw_matrices,
                    symbol_date_index=symbol_date_index,
                )
            )
            excess_results.extend(
                summarize_bucket_stats(
                    bucket_label=bucket_label,
                    events=events,
                    return_matrices=excess_matrices,
                    symbol_date_index=symbol_date_index,
                )
            )
        return apply_bh_fdr(raw_results), apply_bh_fdr(excess_results)

    def _build_event_level_frame(
        self,
        feature_frames: Dict[str, pd.DataFrame],
        bucket_events: Dict[str, Dict[str, list[str]]],
        raw_matrices: Dict[int, pd.DataFrame],
        excess_matrices: Dict[int, pd.DataFrame],
    ) -> pd.DataFrame:
        frame = self._study.build_event_level_frame(feature_frames, bucket_events)
        if frame.empty:
            return frame

        enriched = frame.copy()
        for horizon, ret_df in raw_matrices.items():
            enriched[f"raw_{horizon}d"] = enriched.apply(
                lambda row: _lookup_matrix_value(ret_df, row["date"], row["symbol"]),
                axis=1,
            )
        for horizon, ret_df in excess_matrices.items():
            enriched[f"excess_{horizon}d"] = enriched.apply(
                lambda row: _lookup_matrix_value(ret_df, row["date"], row["symbol"]),
                axis=1,
            )
        enriched["window"] = enriched["date"].map(self._window_label_for_date)
        return enriched.sort_values(["date", "symbol"]).reset_index(drop=True)

    def _build_notes(self, universe_audit) -> list[str]:
        notes = [
            "IS/OOS 在事件研究里只表示报告层时间切片，不表示训练模型。",
            "same-symbol de-overlap 使用硬窗口排斥；同日多股票事件按日期聚类。",
        ]
        hmc_min = universe_audit.summary.get("historical_market_cap_min_date")
        if self._config.study_start_date and hmc_min and hmc_min > self._config.study_start_date:
            notes.append(
                "historical_market_cap 的最早覆盖日期晚于研究起点，早期样本可能会被 universe gate 过滤。"
            )
        return notes

    def _resolve_output_dir(self, output_dir: Optional[str | Path]) -> Path:
        if output_dir is not None:
            return Path(output_dir)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Path("backtest/new") / f"{self._config.study_name}_{stamp}"

    def _window_label_for_date(self, date_str: str) -> str:
        oos_start = self._config.report_split.oos_start_date
        if not oos_start:
            return "Full"
        return "IS" if date_str < oos_start else "OOS"


def _lookup_matrix_value(ret_df: pd.DataFrame, date_str: str, symbol: str) -> float | None:
    if symbol not in ret_df.columns or date_str not in ret_df.index:
        return None
    value = ret_df.loc[date_str, symbol]
    if pd.isna(value):
        return None
    return float(value)


def _read_symbols(path: Path) -> list[str]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        symbols = payload
    elif isinstance(payload, dict):
        symbols = payload.get("symbols", [])
    else:
        raise ValueError(
            "universe JSON must be a list or an object with a symbols field: %s"
            % path
        )
    return sorted({str(symbol).upper() for symbol in symbols if str(symbol).strip()})
