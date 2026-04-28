from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def build_t1open_excess_return_matrices(
    price_dict: Dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame,
    computation_dates: List[str],
    horizons: List[int],
) -> Dict[int, pd.DataFrame]:
    """
    Build excess-return matrices with event-day semantics:

    signal on T
    entry at T+1 open
    exit at T+H close

    The matrix index remains the event date T.
    """
    stock_raw = _build_t1open_return_matrices(price_dict, computation_dates, horizons)
    bench_raw = _build_t1open_return_matrices({"__BENCH__": benchmark_df}, computation_dates, horizons)

    result: Dict[int, pd.DataFrame] = {}
    for horizon in horizons:
        ret_df = stock_raw[horizon].copy()
        bench_series = bench_raw[horizon]["__BENCH__"]
        for date_str in ret_df.index:
            bench_ret = bench_series.get(date_str, np.nan)
            if pd.isna(bench_ret):
                ret_df.loc[date_str] = np.nan
            else:
                ret_df.loc[date_str] = ret_df.loc[date_str] - bench_ret
        result[horizon] = ret_df
    return result


def build_close_forward_return_matrices(
    price_dict: Dict[str, pd.DataFrame],
    computation_dates: List[str],
    horizons: List[int],
) -> Dict[int, pd.DataFrame]:
    """
    Build raw close-to-close forward return matrices on the event date T.

    ret(T, H) = close[T+H] / close[T] - 1
    """
    date_index_maps: Dict[str, Dict[str, int]] = {}
    sorted_dates_map: Dict[str, List[str]] = {}
    close_maps: Dict[str, Dict[str, float]] = {}

    for symbol, df in price_dict.items():
        ordered = df.sort_values("date").reset_index(drop=True)
        dates = ordered["date"].astype(str).str[:10].tolist()
        closes = ordered["close"].astype(float).tolist()
        date_index_maps[symbol] = {d: i for i, d in enumerate(dates)}
        sorted_dates_map[symbol] = dates
        close_maps[symbol] = dict(zip(dates, closes))

    symbols = sorted(price_dict.keys())
    result: Dict[int, pd.DataFrame] = {}
    for horizon in horizons:
        matrix_data: Dict[str, List[float]] = {sym: [] for sym in symbols}
        for comp_date in computation_dates:
            for sym in symbols:
                ret = _close_forward_return(
                    comp_date=comp_date,
                    horizon=horizon,
                    date_index_map=date_index_maps[sym],
                    sorted_dates=sorted_dates_map[sym],
                    close_map=close_maps[sym],
                )
                matrix_data[sym].append(ret)
        df = pd.DataFrame(matrix_data, index=computation_dates)
        df.index.name = "date"
        result[horizon] = df
    return result


def build_prior_excess_return_matrix(
    price_dict: Dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame,
    computation_dates: List[str],
    lookback_days: int,
) -> pd.DataFrame:
    """
    Build close-to-close prior excess return matrix on the event date T.

    prior_excess(T, L) = stock_close[T] / stock_close[T-L] - 1
                       - benchmark_close[T] / benchmark_close[T-L] - 1
    """
    close_maps: Dict[str, Dict[str, float]] = {}
    date_index_maps: Dict[str, Dict[str, int]] = {}
    sorted_dates_map: Dict[str, List[str]] = {}

    for symbol, df in price_dict.items():
        ordered = df.sort_values("date").reset_index(drop=True)
        dates = ordered["date"].astype(str).str[:10].tolist()
        closes = ordered["close"].astype(float).tolist()
        close_maps[symbol] = dict(zip(dates, closes))
        date_index_maps[symbol] = {d: i for i, d in enumerate(dates)}
        sorted_dates_map[symbol] = dates

    bench_ordered = benchmark_df.sort_values("date").reset_index(drop=True)
    bench_dates = bench_ordered["date"].astype(str).str[:10].tolist()
    bench_closes = bench_ordered["close"].astype(float).tolist()
    bench_date_index = {d: i for i, d in enumerate(bench_dates)}

    symbols = sorted(price_dict.keys())
    matrix_data: Dict[str, List[float]] = {sym: [] for sym in symbols}

    for comp_date in computation_dates:
        bench_ret = _close_to_close_return(
            comp_date=comp_date,
            lookback_days=lookback_days,
            date_index_map=bench_date_index,
            sorted_dates=bench_dates,
            close_map=dict(zip(bench_dates, bench_closes)),
        )
        for sym in symbols:
            stock_ret = _close_to_close_return(
                comp_date=comp_date,
                lookback_days=lookback_days,
                date_index_map=date_index_maps[sym],
                sorted_dates=sorted_dates_map[sym],
                close_map=close_maps[sym],
            )
            if np.isnan(stock_ret) or np.isnan(bench_ret):
                matrix_data[sym].append(np.nan)
            else:
                matrix_data[sym].append(stock_ret - bench_ret)

    matrix = pd.DataFrame(matrix_data, index=computation_dates)
    matrix.index.name = "date"
    return matrix


def _build_t1open_return_matrices(
    price_dict: Dict[str, pd.DataFrame],
    computation_dates: List[str],
    horizons: List[int],
) -> Dict[int, pd.DataFrame]:
    date_index_maps: Dict[str, Dict[str, int]] = {}
    sorted_dates_map: Dict[str, List[str]] = {}
    open_maps: Dict[str, Dict[str, float]] = {}
    close_maps: Dict[str, Dict[str, float]] = {}

    for symbol, df in price_dict.items():
        ordered = df.sort_values("date").reset_index(drop=True)
        dates = ordered["date"].astype(str).str[:10].tolist()
        opens = ordered["open"].astype(float).tolist()
        closes = ordered["close"].astype(float).tolist()
        date_index_maps[symbol] = {d: i for i, d in enumerate(dates)}
        sorted_dates_map[symbol] = dates
        open_maps[symbol] = dict(zip(dates, opens))
        close_maps[symbol] = dict(zip(dates, closes))

    symbols = sorted(price_dict.keys())
    result: Dict[int, pd.DataFrame] = {}
    for horizon in horizons:
        matrix_data: Dict[str, List[float]] = {sym: [] for sym in symbols}
        for comp_date in computation_dates:
            for sym in symbols:
                ret = _t1open_close_forward_return(
                    comp_date=comp_date,
                    horizon=horizon,
                    date_index_map=date_index_maps[sym],
                    sorted_dates=sorted_dates_map[sym],
                    open_map=open_maps[sym],
                    close_map=close_maps[sym],
                )
                matrix_data[sym].append(ret)
        df = pd.DataFrame(matrix_data, index=computation_dates)
        df.index.name = "date"
        result[horizon] = df
    return result


def _t1open_close_forward_return(
    comp_date: str,
    horizon: int,
    date_index_map: Dict[str, int],
    sorted_dates: List[str],
    open_map: Dict[str, float],
    close_map: Dict[str, float],
) -> float:
    start_idx = date_index_map.get(comp_date)
    if start_idx is None:
        return np.nan

    entry_idx = start_idx + 1
    exit_idx = start_idx + horizon
    if entry_idx >= len(sorted_dates) or exit_idx >= len(sorted_dates):
        return np.nan

    entry_date = sorted_dates[entry_idx]
    exit_date = sorted_dates[exit_idx]
    entry_price = open_map.get(entry_date)
    exit_price = close_map.get(exit_date)

    if entry_price is None or exit_price is None or entry_price <= 0:
        return np.nan

    return exit_price / entry_price - 1.0


def _close_to_close_return(
    comp_date: str,
    lookback_days: int,
    date_index_map: Dict[str, int],
    sorted_dates: List[str],
    close_map: Dict[str, float],
) -> float:
    end_idx = date_index_map.get(comp_date)
    if end_idx is None or end_idx < lookback_days:
        return np.nan

    start_date = sorted_dates[end_idx - lookback_days]
    end_date = sorted_dates[end_idx]
    start_price = close_map.get(start_date)
    end_price = close_map.get(end_date)
    if start_price is None or end_price is None or start_price <= 0:
        return np.nan

    return end_price / start_price - 1.0


def _close_forward_return(
    comp_date: str,
    horizon: int,
    date_index_map: Dict[str, int],
    sorted_dates: List[str],
    close_map: Dict[str, float],
) -> float:
    start_idx = date_index_map.get(comp_date)
    if start_idx is None:
        return np.nan

    end_idx = start_idx + horizon
    if end_idx >= len(sorted_dates):
        return np.nan

    start_date = sorted_dates[start_idx]
    end_date = sorted_dates[end_idx]
    start_price = close_map.get(start_date)
    end_price = close_map.get(end_date)
    if start_price is None or end_price is None or start_price <= 0:
        return np.nan

    return end_price / start_price - 1.0
