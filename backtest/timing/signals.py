"""
择时信号生成器

每个信号函数签名统一：(price_df: DataFrame) -> List[Tuple[str, str]]
- price_df 含 date, close 列（升序）
- 返回 [(date_str, "BUY"/"SELL"), ...]
"""

from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd


def macd_signals(
    price_df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> List[Tuple[str, str]]:
    """
    MACD 择时信号

    DIF = EMA(fast) - EMA(slow)
    DEA = EMA(DIF, signal)
    DIF 上穿 DEA -> BUY, DIF 下穿 DEA -> SELL
    """
    close = price_df["close"].astype(float)
    dates = price_df["date"].astype(str)

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()

    # 跳过预热期：slow + signal 天
    warmup = slow + signal
    signals = []

    for i in range(warmup, len(close)):
        prev_diff = dif.iloc[i - 1] - dea.iloc[i - 1]
        curr_diff = dif.iloc[i] - dea.iloc[i]

        if prev_diff <= 0 and curr_diff > 0:
            signals.append((dates.iloc[i], "BUY"))
        elif prev_diff >= 0 and curr_diff < 0:
            signals.append((dates.iloc[i], "SELL"))

    return signals


def rsi_signals(
    price_df: pd.DataFrame,
    period: int = 14,
    oversold: float = 30,
    overbought: float = 70,
) -> List[Tuple[str, str]]:
    """
    RSI 择时信号

    RSI 上穿 oversold -> BUY, RSI 下穿 overbought -> SELL
    """
    close = price_df["close"].astype(float)
    dates = price_df["date"].astype(str)

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)  # 无数据时中性

    # 跳过预热期
    warmup = period + 1
    signals = []

    for i in range(warmup, len(close)):
        prev_rsi = rsi.iloc[i - 1]
        curr_rsi = rsi.iloc[i]

        if prev_rsi <= oversold and curr_rsi > oversold:
            signals.append((dates.iloc[i], "BUY"))
        elif prev_rsi >= overbought and curr_rsi < overbought:
            signals.append((dates.iloc[i], "SELL"))

    return signals


def ma_cross_signals(
    price_df: pd.DataFrame,
    short_window: int = 20,
    long_window: int = 60,
) -> List[Tuple[str, str]]:
    """
    均线交叉择时信号

    MA_short 上穿 MA_long -> BUY, MA_short 下穿 MA_long -> SELL
    """
    close = price_df["close"].astype(float)
    dates = price_df["date"].astype(str)

    ma_short = close.rolling(window=short_window, min_periods=short_window).mean()
    ma_long = close.rolling(window=long_window, min_periods=long_window).mean()

    # 跳过预热期
    warmup = long_window
    signals = []

    for i in range(warmup, len(close)):
        prev_diff = ma_short.iloc[i - 1] - ma_long.iloc[i - 1]
        curr_diff = ma_short.iloc[i] - ma_long.iloc[i]

        # 需要两个值都非 NaN
        if np.isnan(prev_diff) or np.isnan(curr_diff):
            continue

        if prev_diff <= 0 and curr_diff > 0:
            signals.append((dates.iloc[i], "BUY"))
        elif prev_diff >= 0 and curr_diff < 0:
            signals.append((dates.iloc[i], "SELL"))

    return signals


# 信号注册表: 名称 -> (函数, 默认参数)
SIGNAL_REGISTRY: Dict[str, Tuple[Callable, dict]] = {
    "MACD": (macd_signals, {"fast": 12, "slow": 26, "signal": 9}),
    "RSI": (rsi_signals, {"period": 14, "oversold": 30, "overbought": 70}),
    "MA_Cross": (ma_cross_signals, {"short_window": 20, "long_window": 60}),
}
