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


def new_high_signals(
    price_df: pd.DataFrame,
    entry_days: int = 50,
    exit_days: int = 20,
) -> List[Tuple[str, str]]:
    """
    N 日新高突破信号 (Donchian Channel / Turtle Trading)

    收盘价创 entry_days 日新高 -> BUY
    收盘价创 exit_days 日新低 -> SELL
    """
    close = price_df["close"].astype(float)
    dates = price_df["date"].astype(str)

    warmup = max(entry_days, exit_days)
    signals = []
    in_market = False

    for i in range(warmup, len(close)):
        high_n = close.iloc[i - entry_days:i].max()
        low_n = close.iloc[i - exit_days:i].min()

        if not in_market and close.iloc[i] >= high_n:
            signals.append((dates.iloc[i], "BUY"))
            in_market = True
        elif in_market and close.iloc[i] <= low_n:
            signals.append((dates.iloc[i], "SELL"))
            in_market = False

    return signals


# ── VIX 跨资产择时信号 ──────────────────────────────────


def vix_ma_signals(
    price_df: pd.DataFrame,
    vix_ma_period: int = 20,
    aux_data: pd.DataFrame = None,
) -> List[Tuple[str, str]]:
    """
    VIX 均线择时信号

    VIX 下穿 SMA(vix_ma_period) → BUY（恐慌消退）
    VIX 上穿 SMA(vix_ma_period) → SELL（恐慌升温）
    """
    if aux_data is None or len(aux_data) == 0:
        return []

    target_dates = set(price_df["date"].astype(str))

    # 在完整 VIX 历史上计算 MA，保留 lookback
    vix = aux_data.copy()
    vix["date"] = vix["date"].astype(str)

    if len(vix) < vix_ma_period + 1:
        return []

    close = vix["close"].astype(float)
    dates = vix["date"]
    ma = close.rolling(window=vix_ma_period, min_periods=vix_ma_period).mean()

    warmup = vix_ma_period
    signals = []

    for i in range(warmup, len(close)):
        if dates.iloc[i] not in target_dates:
            continue

        prev_diff = close.iloc[i - 1] - ma.iloc[i - 1]
        curr_diff = close.iloc[i] - ma.iloc[i]

        if np.isnan(prev_diff) or np.isnan(curr_diff):
            continue

        if prev_diff >= 0 and curr_diff < 0:
            signals.append((dates.iloc[i], "BUY"))
        elif prev_diff <= 0 and curr_diff > 0:
            signals.append((dates.iloc[i], "SELL"))

    return signals


def vix_spike_signals(
    price_df: pd.DataFrame,
    buy_threshold: float = 30,
    sell_threshold: float = 20,
    aux_data: pd.DataFrame = None,
) -> List[Tuple[str, str]]:
    """
    VIX 恐慌反转信号

    VIX > buy_threshold → BUY（买恐慌）
    VIX < sell_threshold → SELL（恐慌消退 = 贪婪，减仓）
    """
    if aux_data is None or len(aux_data) == 0:
        return []

    target_dates = set(price_df["date"].astype(str))

    # 在完整 VIX 上跟踪状态，只在 target_dates 发信号
    vix = aux_data.copy()
    vix["date"] = vix["date"].astype(str)

    if len(vix) < 1:
        return []

    close = vix["close"].astype(float)
    dates = vix["date"]

    signals = []
    in_market = False

    for i in range(len(close)):
        if not in_market and close.iloc[i] > buy_threshold:
            in_market = True
            if dates.iloc[i] in target_dates:
                signals.append((dates.iloc[i], "BUY"))
        elif in_market and close.iloc[i] < sell_threshold:
            in_market = False
            if dates.iloc[i] in target_dates:
                signals.append((dates.iloc[i], "SELL"))

    return signals


def vix_percentile_signals(
    price_df: pd.DataFrame,
    lookback: int = 252,
    buy_pctile: float = 90,
    sell_pctile: float = 20,
    aux_data: pd.DataFrame = None,
) -> List[Tuple[str, str]]:
    """
    VIX 百分位择时信号

    VIX 252日百分位 > 90% → BUY（极端恐慌 = 买入机会）
    VIX 252日百分位 < 20% → SELL（极端自满 = 减仓）
    """
    if aux_data is None or len(aux_data) == 0:
        return []

    target_dates = set(price_df["date"].astype(str))

    # 在完整 VIX 上计算百分位 + 跟踪状态
    vix = aux_data.copy()
    vix["date"] = vix["date"].astype(str)

    if len(vix) < lookback + 1:
        return []

    close = vix["close"].astype(float)
    dates = vix["date"]

    signals = []
    in_market = False

    for i in range(lookback, len(close)):
        window = close.iloc[i - lookback:i]
        pctile = (window < close.iloc[i]).sum() / len(window) * 100

        if not in_market and pctile > buy_pctile:
            in_market = True
            if dates.iloc[i] in target_dates:
                signals.append((dates.iloc[i], "BUY"))
        elif in_market and pctile < sell_pctile:
            in_market = False
            if dates.iloc[i] in target_dates:
                signals.append((dates.iloc[i], "SELL"))

    return signals


def vix_rsi_signals(
    price_df: pd.DataFrame,
    period: int = 14,
    overbought: float = 70,
    oversold: float = 30,
    aux_data: pd.DataFrame = None,
) -> List[Tuple[str, str]]:
    """
    VIX RSI 择时信号

    VIX RSI > overbought（VIX 过热 = 市场超卖）→ BUY
    VIX RSI < oversold（VIX 冷却 = 市场过热）→ SELL
    """
    if aux_data is None or len(aux_data) == 0:
        return []

    target_dates = set(price_df["date"].astype(str))

    # 在完整 VIX 上计算 RSI
    vix = aux_data.copy()
    vix["date"] = vix["date"].astype(str)

    if len(vix) < period + 2:
        return []

    close = vix["close"].astype(float)
    dates = vix["date"]

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # avg_loss == 0 时: 纯涨 → RSI=100, 无波动 → RSI=50
    fill_values = pd.Series(
        np.where(avg_gain > 0, 100.0, 50.0), index=close.index
    )
    rsi = rsi.fillna(fill_values)

    warmup = period + 1
    signals = []

    for i in range(warmup, len(close)):
        if dates.iloc[i] not in target_dates:
            continue

        prev_rsi = rsi.iloc[i - 1]
        curr_rsi = rsi.iloc[i]

        if prev_rsi <= overbought and curr_rsi > overbought:
            signals.append((dates.iloc[i], "BUY"))
        elif prev_rsi >= oversold and curr_rsi < oversold:
            signals.append((dates.iloc[i], "SELL"))

    return signals


# 信号注册表: 名称 -> (函数, 默认参数)
SIGNAL_REGISTRY: Dict[str, Tuple[Callable, dict]] = {
    "MACD": (macd_signals, {"fast": 12, "slow": 26, "signal": 9}),
    "RSI": (rsi_signals, {"period": 14, "oversold": 30, "overbought": 70}),
    "MA_Cross": (ma_cross_signals, {"short_window": 20, "long_window": 60}),
    "New_High": (new_high_signals, {"entry_days": 50, "exit_days": 20}),
    "VIX_MA": (vix_ma_signals, {"vix_ma_period": 20}),
    "VIX_Spike": (vix_spike_signals, {"buy_threshold": 30, "sell_threshold": 20}),
    "VIX_Percentile": (vix_percentile_signals, {"lookback": 252, "buy_pctile": 90, "sell_pctile": 20}),
    "VIX_RSI": (vix_rsi_signals, {"period": 14, "overbought": 70, "oversold": 30}),
}
