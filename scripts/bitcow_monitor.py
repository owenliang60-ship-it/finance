#!/usr/bin/env python3
"""BTC 奶牛（BitCow）日线信号监控：慢层 50W SMA + 中层 EMA84±ATR14 + 偏离 D + DVOL。

核心计算只用标准库；网络用 requests；投递用 src.telegram_bot（延迟导入）。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, NamedTuple, Sequence

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


def mid_step(prev_state: int, close: float, ema: float, atr: float) -> tuple[int, str | None]:
    upper, lower = ema + atr, ema - atr
    if close > upper:
        new = 1
    elif close < lower:
        new = -1
    else:
        new = prev_state
    if prev_state == 0:
        return new, None
    if prev_state == 1:
        if new == -1:
            return -1, "flip_short"
        if close - lower < 0.5 * atr:
            return 1, "warn_long_near"
        if close < ema:
            return 1, "warn_long_mid"
        return 1, None
    if new == 1:
        return 1, "flip_long"
    if upper - close < 0.5 * atr:
        return -1, "warn_short_near"
    if close > ema:
        return -1, "warn_short_mid"
    return -1, None


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _day(ms: int) -> str:
    return _utc(ms).date().isoformat()


def slow_layer(bars: Sequence[Bar], sma_len: int = 50) -> dict[str, Any]:
    weeks = [b for b in bars if _utc(b.open_ms).weekday() == 6]
    if len(weeks) < sma_len + 1:
        raise DataError("周线数量不足以计算 50W SMA")
    since = _day(weeks[sma_len - 1].open_ms)
    bulls: list[bool] = []
    sma = 0.0
    for i in range(sma_len - 1, len(weeks)):
        sma = sum(w.close for w in weeks[i - sma_len + 1 : i + 1]) / sma_len
        bull = weeks[i].close > sma
        if bulls and bull != bulls[-1]:
            since = _day(weeks[i].open_ms)
        bulls.append(bull)
    flipped = None
    if _utc(bars[-1].open_ms).weekday() == 6 and bulls[-1] != bulls[-2]:
        flipped = "bull" if bulls[-1] else "bear"
    return {
        "bull": bulls[-1],
        "wclose": weeks[-1].close,
        "sma50": sma,
        "weekly_date": _day(weeks[-1].open_ms),
        "since": since,
        "flipped": flipped,
        "daily_vs_sma50_pct": (bars[-1].close / sma - 1.0) * 100.0,
    }
