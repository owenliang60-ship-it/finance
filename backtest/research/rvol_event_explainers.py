from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import pandas as pd

EventMap = Dict[str, List[str]]
SymbolDates = Dict[str, List[str]]
SocialScores = Dict[Tuple[str, str], float]


@dataclass(frozen=True)
class EventExplainerSummary:
    cohort: str
    events_total: int
    events_with_earnings_info: int
    events_near_earnings: int
    events_with_social_info: int
    events_social_spike: int
    events_after_social_start: int
    events_with_social_info_after_social_start: int
    earnings_coverage: float
    social_full_history_coverage: float
    social_post_start_coverage: float
    earnings_status: str
    social_status: str


def summarize_event_explainers(
    cohort_events: Dict[str, EventMap],
    earnings_dates: SymbolDates | None = None,
    social_scores: SocialScores | None = None,
    social_start_date: str = "2025-12-13",
    earnings_window_days: int = 3,
    social_spike_threshold: float = 2.0,
) -> List[EventExplainerSummary]:
    earnings_dates = earnings_dates or {}
    social_scores = social_scores or {}

    summaries: List[EventExplainerSummary] = []
    for cohort, events in sorted(cohort_events.items()):
        summaries.append(
            _summarize_one_cohort(
                cohort=cohort,
                events=events,
                earnings_dates=earnings_dates,
                social_scores=social_scores,
                social_start_date=social_start_date,
                earnings_window_days=earnings_window_days,
                social_spike_threshold=social_spike_threshold,
            )
        )
    return summaries


def load_earnings_dates_from_market_db(db_path: Path) -> SymbolDates:
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT symbol, COALESCE(filing_date, date) AS event_date
            FROM income_quarterly
            WHERE event_date IS NOT NULL AND event_date != ''
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    out: SymbolDates = {}
    for symbol, event_date in rows:
        out.setdefault(str(symbol), []).append(str(event_date)[:10])
    return out


def load_social_attention_zscores_from_market_db(db_path: Path) -> SocialScores:
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(str(db_path))
    try:
        frame = pd.read_sql_query(
            """
            SELECT symbol, date, buzz_score
            FROM social_sentiment
            WHERE buzz_score IS NOT NULL
            """,
            conn,
        )
    except Exception:
        return {}
    finally:
        conn.close()

    if frame.empty:
        return {}

    frame["date"] = frame["date"].astype(str).str[:10]
    frame["buzz_score"] = frame["buzz_score"].astype(float)
    grouped = frame.groupby(["symbol", "date"], as_index=False)["buzz_score"].max()
    mean = grouped.groupby("symbol")["buzz_score"].transform("mean")
    std = grouped.groupby("symbol")["buzz_score"].transform("std").replace(0, pd.NA)
    grouped["attention_zscore"] = ((grouped["buzz_score"] - mean) / std).fillna(0.0)

    return {
        (str(row.symbol), str(row.date)): float(row.attention_zscore)
        for row in grouped.itertuples(index=False)
    }


def _summarize_one_cohort(
    cohort: str,
    events: EventMap,
    earnings_dates: SymbolDates,
    social_scores: SocialScores,
    social_start_date: str,
    earnings_window_days: int,
    social_spike_threshold: float,
) -> EventExplainerSummary:
    total = 0
    earnings_info = 0
    near_earnings = 0
    social_info = 0
    social_spike = 0
    after_social_start = 0
    social_info_after_start = 0

    for symbol, dates in events.items():
        symbol_earnings = earnings_dates.get(symbol, [])
        for event_date in dates:
            total += 1
            if symbol_earnings:
                earnings_info += 1
                if _near_any_date(event_date, symbol_earnings, earnings_window_days):
                    near_earnings += 1

            score = social_scores.get((symbol, event_date))
            if score is not None:
                social_info += 1
                if score >= social_spike_threshold:
                    social_spike += 1

            if event_date >= social_start_date:
                after_social_start += 1
                if score is not None:
                    social_info_after_start += 1

    earnings_coverage = _ratio(earnings_info, total)
    social_full_coverage = _ratio(social_info, total)
    social_post_coverage = _ratio(social_info_after_start, after_social_start)

    return EventExplainerSummary(
        cohort=cohort,
        events_total=total,
        events_with_earnings_info=earnings_info,
        events_near_earnings=near_earnings,
        events_with_social_info=social_info,
        events_social_spike=social_spike,
        events_after_social_start=after_social_start,
        events_with_social_info_after_social_start=social_info_after_start,
        earnings_coverage=earnings_coverage,
        social_full_history_coverage=social_full_coverage,
        social_post_start_coverage=social_post_coverage,
        earnings_status="usable" if earnings_coverage >= 0.60 else "exploratory",
        social_status="usable_post_start" if social_post_coverage >= 0.60 else "exploratory",
    )


def _near_any_date(event_date: str, candidate_dates: Iterable[str], window_days: int) -> bool:
    event = _parse_date(event_date)
    if event is None:
        return False
    for candidate in candidate_dates:
        parsed = _parse_date(candidate)
        if parsed is not None and abs((event - parsed).days) <= window_days:
            return True
    return False


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator / denominator)
