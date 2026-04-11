from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

import pandas as pd

from backtest.pipeline.paths import resolve_shared_data_root
from backtest.pipeline.types import UniverseBuildResult


class UniverseBuildError(ValueError):
    """Raised when the PIT universe cannot be built safely."""


class UniverseBuilder:
    def __init__(
        self,
        market_db_path: Optional[str | Path] = None,
        company_db_path: Optional[str | Path] = None,
    ):
        data_root = resolve_shared_data_root()
        self.market_db_path = Path(market_db_path) if market_db_path is not None else data_root / "data" / "market.db"
        self.company_db_path = Path(company_db_path) if company_db_path is not None else data_root / "data" / "company.db"

    def _market_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.market_db_path)

    def _company_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.company_db_path)

    def rebalance_dates(self, start_date: str, end_date: str, rebalance: str) -> List[str]:
        with self._market_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT date
                FROM daily_price
                WHERE date >= ? AND date <= ?
                ORDER BY date
                """,
                (start_date, end_date),
            ).fetchall()
        dates = [row[0] for row in rows]
        if rebalance == "weekly":
            return dates[::5]
        if rebalance == "monthly_first_trading_day":
            seen = set()
            picked: List[str] = []
            for value in dates:
                month = value[:7]
                if month not in seen:
                    picked.append(value)
                    seen.add(month)
            return picked
        raise UniverseBuildError(f"Unsupported rebalance cadence: {rebalance}")

    def build(
        self,
        start_date: str,
        end_date: str,
        rebalance: str,
        market_cap_min_usd: float,
        exclude_sectors: List[str],
        min_names: int,
    ) -> UniverseBuildResult:
        rebalance_dates = self.rebalance_dates(start_date, end_date, rebalance)
        warnings: List[str] = []
        kept_frames: List[pd.DataFrame] = []
        effective_start: Optional[str] = None
        skipped_dates: List[str] = []

        for rebalance_date in rebalance_dates:
            frame = self._universe_at(
                rebalance_date=rebalance_date,
                market_cap_min_usd=market_cap_min_usd,
                exclude_sectors=exclude_sectors,
            )

            if frame.empty:
                if effective_start is None:
                    warnings.append(
                        f"{rebalance_date}: no historical market cap coverage yet, moving effective_start forward"
                    )
                    continue
                raise UniverseBuildError(
                    f"{rebalance_date}: historical_market_cap coverage broken after effective_start"
                )

            if len(frame) < min_names:
                if effective_start is None:
                    warnings.append(
                        f"{rebalance_date}: insufficient names ({len(frame)} < {min_names}), moving effective_start forward"
                    )
                    continue
                skipped_dates.append(rebalance_date)
                warnings.append(
                    f"{rebalance_date}: skipped rebalance due to min_names ({len(frame)} < {min_names})"
                )
                continue

            if effective_start is None:
                effective_start = rebalance_date

            frame = frame.copy()
            frame["date"] = rebalance_date
            kept_frames.append(frame)

        if effective_start is None:
            raise UniverseBuildError("No rebalance date has enough historical market cap coverage")

        if skipped_dates and (len(skipped_dates) / max(len(rebalance_dates), 1)) > 0.1:
            raise UniverseBuildError(
                f"Skipped {len(skipped_dates)} of {len(rebalance_dates)} rebalance dates (>10%)"
            )

        universe_df = (
            pd.concat(kept_frames, ignore_index=True)
            if kept_frames
            else pd.DataFrame(columns=["date", "symbol", "market_cap", "sector"])
        )
        return UniverseBuildResult(
            universe_df=universe_df[["date", "symbol", "market_cap", "sector"]],
            effective_start=effective_start,
            rebalance_dates=[d for d in rebalance_dates if d >= effective_start and d not in skipped_dates],
            warnings=warnings,
        )

    def _universe_at(
        self,
        rebalance_date: str,
        market_cap_min_usd: float,
        exclude_sectors: List[str],
    ) -> pd.DataFrame:
        with self._market_conn() as market_conn, self._company_conn() as company_conn:
            query = """
                SELECT h.symbol, h.market_cap
                FROM historical_market_cap h
                JOIN (
                    SELECT symbol, MAX(date) AS max_date
                    FROM historical_market_cap
                    WHERE date <= ?
                    GROUP BY symbol
                ) latest
                  ON latest.symbol = h.symbol
                 AND latest.max_date = h.date
                WHERE h.market_cap >= ?
                ORDER BY h.symbol
            """
            market_caps = pd.read_sql_query(
                query,
                market_conn,
                params=(rebalance_date, market_cap_min_usd),
            )
            if market_caps.empty:
                return market_caps

            sectors = pd.read_sql_query(
                "SELECT symbol, sector FROM companies",
                company_conn,
            )

        merged = market_caps.merge(sectors, on="symbol", how="left")
        if exclude_sectors:
            merged = merged[~merged["sector"].isin(exclude_sectors)]
        return merged.reset_index(drop=True)
