#!/usr/bin/env python3
"""BTC 奶牛（BitCow）日线信号监控：慢层 50W SMA + 中层 EMA84±ATR14 + 偏离 D + DVOL。

核心计算只用标准库；网络用 requests；投递用 src.telegram_bot（延迟导入）。
"""
from __future__ import annotations

import math
import re
from typing import NamedTuple, Sequence

DAY_MS = 86_400_000
MIN_DAILY_BARS = 900
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
DERIBIT_DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
LABEL_RE = re.compile(r"^[A-Za-z0-9-]{1,20}$")


class DataError(RuntimeError):
    pass


class Bar(NamedTuple):
    open_ms: int
    high: float
    low: float
    close: float


def ema_atr(bars: Sequence[Bar], ema_len: int = 84, atr_len: int = 14) -> tuple[list[float], list[float]]:
    if len(bars) < max(ema_len, atr_len):
        raise DataError("K线数量不足以计算 EMA/ATR")
    k = 2.0 / (ema_len + 1)
    ema = [bars[0].close]
    atr = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        h, lo, c, pc = bars[i].high, bars[i].low, bars[i].close, bars[i - 1].close
        ema.append(c * k + ema[-1] * (1.0 - k))
        tr = max(h - lo, abs(h - pc), abs(lo - pc))
        atr.append((atr[-1] * (atr_len - 1) + tr) / atr_len)
    if not (math.isfinite(atr[-1]) and atr[-1] > 0):
        raise DataError("ATR 非正或非有限")
    return ema, atr
