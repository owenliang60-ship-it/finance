from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from backtest.pipeline.paths import resolve_shared_data_root


class PitData:
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

    def window(self, symbol: str, end_date: str, lookback_days: int) -> pd.DataFrame:
        query = """
            SELECT date, open, high, low, close, volume
            FROM daily_price
            WHERE symbol = ? AND date <= ?
            ORDER BY date DESC
            LIMIT ?
        """
        with self._market_conn() as conn:
            df = pd.read_sql_query(query, conn, params=(symbol, end_date, lookback_days))
        if df.empty:
            return df
        return df.sort_values("date").reset_index(drop=True)

    def as_of(self, symbol: str, end_date: str) -> pd.DataFrame:
        query = """
            SELECT date, open, high, low, close, volume
            FROM daily_price
            WHERE symbol = ? AND date <= ?
            ORDER BY date
        """
        with self._market_conn() as conn:
            return pd.read_sql_query(query, conn, params=(symbol, end_date))

    def benchmark_prices(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        query = """
            SELECT date, open, high, low, close, volume
            FROM daily_price
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        with self._market_conn() as conn:
            return pd.read_sql_query(query, conn, params=(symbol, start_date, end_date))

    def history(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses = ["symbol = ?"]
        params: List[object] = [symbol]
        if start_date is not None:
            clauses.append("date >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("date <= ?")
            params.append(end_date)

        query = f"""
            SELECT date, open, high, low, close, volume
            FROM daily_price
            WHERE {' AND '.join(clauses)}
            ORDER BY date
        """
        with self._market_conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def trading_calendar(self, start_date: str, end_date: str) -> List[str]:
        query = """
            SELECT DISTINCT date
            FROM daily_price
            WHERE date >= ? AND date <= ?
            ORDER BY date
        """
        with self._market_conn() as conn:
            rows = conn.execute(query, (start_date, end_date)).fetchall()
        return [row[0] for row in rows]

    def price_panel(
        self,
        symbols: Iterable[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        symbol_list = [str(symbol) for symbol in symbols]
        if not symbol_list:
            return pd.DataFrame(columns=["date", "symbol", "open", "close"])

        placeholders = ",".join("?" for _ in symbol_list)
        query = f"""
            SELECT date, symbol, open, close
            FROM daily_price
            WHERE symbol IN ({placeholders})
              AND date >= ?
              AND date <= ?
            ORDER BY date, symbol
        """
        params: List[object] = [*symbol_list, start_date, end_date]
        with self._market_conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def bulk_history(
        self,
        symbols: Iterable[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, pd.DataFrame]:
        result: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            frame = self.history(str(symbol), start_date=start_date, end_date=end_date)
            if not frame.empty:
                result[str(symbol)] = frame
        return result

    def social_mentions_history_as_of(
        self,
        symbol: str,
        end_date: str,
        lookback_days: int,
    ) -> List[int]:
        query = """
            SELECT date, COALESCE(SUM(total_mentions), 0) AS combined_mentions
            FROM social_sentiment
            WHERE symbol = ? AND date <= ?
            GROUP BY date
            ORDER BY date DESC
            LIMIT ?
        """
        with self._market_conn() as conn:
            rows = conn.execute(query, (symbol, end_date, lookback_days)).fetchall()
        return [int(row[1] or 0) for row in rows]

    def bulk_price_windows(
        self,
        symbols: Iterable[str],
        end_date: str,
        lookback_days: int,
        min_rows: int = 1,
    ) -> Dict[str, pd.DataFrame]:
        result: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self.window(symbol, end_date=end_date, lookback_days=lookback_days)
            if len(df) >= min_rows:
                result[symbol] = df
        return result
