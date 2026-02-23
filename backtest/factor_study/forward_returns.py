"""
前向收益矩阵构建

给定完整价格数据和计算日期列表，构建 {horizon: DataFrame} 矩阵。
矩阵的每一行是计算日期，每一列是股票，值是该日期起的 N 天前向收益。

注意: 用完整数据计算前向收益是合法的 — 这是评估指标，不是决策输入。
"""

import logging
from typing import Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_return_matrix(
    price_dict: Dict[str, pd.DataFrame],
    computation_dates: List[str],
    horizons: List[int],
) -> Dict[int, pd.DataFrame]:
    """
    构建前向收益矩阵

    Args:
        price_dict: {symbol: price_df}，完整数据（含未来数据）
        computation_dates: 计算日期列表 (sorted)
        horizons: 前向窗口列表，如 [5, 10, 20, 40, 60]

    Returns:
        {horizon: DataFrame[index=date, columns=symbol, values=forward_return]}
        forward_return = price[t+horizon] / price[t] - 1
    """
    # 预处理: 为每只股票建立 date → close 的快速索引
    close_maps: Dict[str, Dict[str, float]] = {}
    date_index_maps: Dict[str, Dict[str, int]] = {}
    sorted_dates_map: Dict[str, List[str]] = {}

    for symbol, df in price_dict.items():
        dates = df["date"].astype(str).tolist()
        closes = df["close"].astype(float).tolist()
        close_maps[symbol] = dict(zip(dates, closes))
        date_index_maps[symbol] = {d: i for i, d in enumerate(dates)}
        sorted_dates_map[symbol] = dates

    symbols = sorted(price_dict.keys())
    result: Dict[int, pd.DataFrame] = {}

    for horizon in horizons:
        matrix_data: Dict[str, List[float]] = {sym: [] for sym in symbols}
        valid_dates: List[str] = []

        for comp_date in computation_dates:
            valid_dates.append(comp_date)
            for sym in symbols:
                fwd_ret = _forward_return(
                    sym, comp_date, horizon,
                    date_index_maps, sorted_dates_map, close_maps,
                )
                matrix_data[sym].append(fwd_ret)

        df = pd.DataFrame(matrix_data, index=valid_dates)
        df.index.name = "date"
        result[horizon] = df

    return result


def _forward_return(
    symbol: str,
    comp_date: str,
    horizon: int,
    date_index_maps: Dict[str, Dict[str, int]],
    sorted_dates_map: Dict[str, List[str]],
    close_maps: Dict[str, Dict[str, float]],
) -> float:
    """计算单只股票在指定日期的前向收益"""
    idx_map = date_index_maps.get(symbol, {})
    dates = sorted_dates_map.get(symbol, [])

    if comp_date not in idx_map:
        return np.nan

    start_idx = idx_map[comp_date]
    end_idx = start_idx + horizon

    if end_idx >= len(dates):
        return np.nan

    end_date = dates[end_idx]
    p0 = close_maps[symbol].get(comp_date)
    p1 = close_maps[symbol].get(end_date)

    if p0 is None or p1 is None or p0 == 0:
        return np.nan

    return p1 / p0 - 1
