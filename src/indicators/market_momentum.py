"""
Market Momentum 指标 — 物理学第一性原理市场动量

公式:
    日动量:  m(t) = Close(t) × Volume(t) × ln(Close(t) / Close(t-1))
    21日冲量: M(t) = Σ m(t-20..t)
    Z-score: z(t) = (M(t) - μ_150) / σ_150

信号:
    z > 0 → 正向资金动量（可配阈值）

用途:
    捕捉"价量齐升"的资金势能，高 z-score 表示异常强劲的资金流入。
"""

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 参数常量
ROLLING_SUM_WINDOW = 21
ZSCORE_WINDOW = 150
MIN_DATA_DAYS = 1 + ROLLING_SUM_WINDOW + ZSCORE_WINDOW  # 172


def _compute_momentum_series(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    计算完整的 market momentum 时间序列

    Args:
        df: 单只股票的量价 DataFrame，必须包含 [date, close, volume]，按日期正序

    Returns:
        带有 [date, close, volume, log_return, daily_momentum, momentum_21d, zscore] 的 DataFrame
        数据不足返回 None
    """
    if df is None or df.empty:
        return None

    required_cols = {"date", "close", "volume"}
    if not required_cols.issubset(df.columns):
        logger.warning(f"缺少必要列，需要 {required_cols}，实际 {set(df.columns)}")
        return None

    if len(df) < MIN_DATA_DAYS:
        return None

    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"].values.astype(np.float64)
    volume = df["volume"].values.astype(np.float64)

    # log return: ln(close[t] / close[t-1])
    log_return = np.full(len(close), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = close[1:] / close[:-1]
        log_return[1:] = np.where(ratio > 0, np.log(ratio), np.nan)

    # 日动量: m(t) = close(t) * volume(t) * log_return(t)
    daily_momentum = close * volume * log_return

    # 21日滚动求和（冲量）
    s = pd.Series(daily_momentum)
    momentum_21d = s.rolling(window=ROLLING_SUM_WINDOW, min_periods=ROLLING_SUM_WINDOW).sum()

    # 150日滚动 z-score
    rolling_mean = momentum_21d.rolling(window=ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    rolling_std = momentum_21d.rolling(window=ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()

    with np.errstate(divide="ignore", invalid="ignore"):
        zscore = (momentum_21d - rolling_mean) / rolling_std

    result = df[["date", "close", "volume"]].copy()
    result["log_return"] = log_return
    result["daily_momentum"] = daily_momentum
    result["momentum_21d"] = momentum_21d.values
    result["zscore"] = zscore.values

    return result


def compute_market_momentum(df: pd.DataFrame) -> Optional[dict]:
    """
    计算单只股票的最新 market momentum

    Args:
        df: 单只股票的量价 DataFrame，必须包含 [date, close, volume]

    Returns:
        {"zscore": float, "raw_momentum_21d": float, "mean_150d": float, "std_150d": float}
        数据不足返回 None
    """
    series = _compute_momentum_series(df)
    if series is None:
        return None

    # 取最后一行有效 zscore
    valid = series.dropna(subset=["zscore"])
    if valid.empty:
        return None

    last = valid.iloc[-1]

    # 重新计算 mean/std 用于输出
    momentum_21d_series = pd.Series(series["momentum_21d"].values)
    last_idx = valid.index[-1]
    start_idx = max(0, last_idx - ZSCORE_WINDOW + 1)
    window_data = series["momentum_21d"].iloc[start_idx : last_idx + 1].dropna()

    mean_150d = float(window_data.mean()) if len(window_data) >= ZSCORE_WINDOW else np.nan
    std_150d = float(window_data.std()) if len(window_data) >= ZSCORE_WINDOW else np.nan

    return {
        "zscore": float(round(last["zscore"], 4)),
        "raw_momentum_21d": float(last["momentum_21d"]),
        "mean_150d": mean_150d,
        "std_150d": std_150d,
    }


def scan_market_momentum(
    price_dict: Dict[str, pd.DataFrame],
    threshold: float = 0.0,
) -> pd.DataFrame:
    """
    批量扫描多只股票的 market momentum

    Args:
        price_dict: {symbol: price_df} 字典
        threshold: z-score 信号阈值（默认 0.0）

    Returns:
        DataFrame [symbol, zscore, raw_momentum_21d, signal]，按 zscore 降序
    """
    columns = ["symbol", "zscore", "raw_momentum_21d", "signal"]

    if not price_dict:
        return pd.DataFrame(columns=columns)

    results = []
    for symbol, df in price_dict.items():
        result = compute_market_momentum(df)
        if result is not None:
            results.append({
                "symbol": symbol,
                "zscore": result["zscore"],
                "raw_momentum_21d": result["raw_momentum_21d"],
                "signal": bool(result["zscore"] > threshold),
            })
        else:
            logger.debug(f"{symbol}: 数据不足，跳过")

    if not results:
        return pd.DataFrame(columns=columns)

    out = pd.DataFrame(results)[columns]
    out = out.sort_values("zscore", ascending=False).reset_index(drop=True)

    fired = out[out["signal"]].shape[0]
    logger.info(
        f"Market Momentum 扫描完成: {len(out)} 只, {fired} 只触发信号 (threshold={threshold})"
    )

    return out
