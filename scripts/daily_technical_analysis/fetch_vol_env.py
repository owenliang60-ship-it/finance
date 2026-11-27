#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch cross-asset volatility & sentiment environment (with 2Y percentiles)
for the Daily Technical Analysis Agent (BTC / QQQ / SOXX).

Outputs strict JSON with per-block fail-closed status (`available: bool`, `reason: str`):
1. btc_vol: Deribit BTC DVOL (closed UTC 1D + 2Y 730d percentile + live ref) & Binance BTC HV20 (2Y percentile + VRP)
2. us_fear_greed: CNN US Fear & Greed Index (0-100 score + rating + 2Y percentile)
3. us_equity_vol: CBOE ^VIX & ^VXN (2Y 504-trading-day percentile) + QQQ HV20 & VRP
4. semi_vol: SOXX real-time 30D constant-maturity ATM IV (option chain variance interpolation) + SOXX HV20 (2Y 504-trading-day percentile) & VRP
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

SG_TZ = ZoneInfo("Asia/Singapore")
ET_TZ = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
DAY_MS = 86_400_000
EPOCH_SENTINEL_MS = 946_684_800_000  # 2000-01-01 UTC guard against 1970 sentinel

DERIBIT_DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
BINANCE_KLINES_URLS = (
    "https://api.binance.com/api/v3/klines",
    "https://data-api.binance.vision/api/v3/klines",
)
CNN_FNG_BASE_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://edition.cnn.com/",
}

FNG_RATING_CN = {
    "extreme fear": "极度恐慌",
    "fear": "恐慌",
    "neutral": "中性",
    "greed": "贪婪",
    "extreme greed": "极度贪婪",
}


def calc_percentile(values: list[float], current: float) -> float | None:
    """Exact empirical CDF percentile: count(v <= current) / total * 100."""
    clean = [v for v in values if math.isfinite(v)]
    if not clean or not math.isfinite(current):
        return None
    return round(100.0 * sum(1 for v in clean if v <= current) / len(clean), 1)


def calc_rolling_hv(closes: list[float], window: int = 20, ann_days: float = 252.0) -> list[float]:
    """Compute rolling annualized sample log-return volatility (%) series."""
    if len(closes) < window + 1:
        return []
    log_rets: list[float] = []
    for i in range(1, len(closes)):
        c0, c1 = closes[i - 1], closes[i]
        if c0 <= 0 or c1 <= 0 or not (math.isfinite(c0) and math.isfinite(c1)):
            return []
        log_rets.append(math.log(c1 / c0))
    out: list[float] = []
    scale = math.sqrt(ann_days) * 100.0
    for end in range(window, len(log_rets) + 1):
        seg = log_rets[end - window : end]
        mean = sum(seg) / window
        var = sum((r - mean) ** 2 for r in seg) / (window - 1)
        out.append(math.sqrt(max(var, 0.0)) * scale)
    return out


def _calc_ema(vals: list[float], period: int) -> list[float]:
    if len(vals) < period:
        return []
    k = 2.0 / (period + 1.0)
    ema = sum(vals[:period]) / period
    out = [ema]
    for v in vals[period:]:
        ema = v * k + ema * (1.0 - k)
        out.append(ema)
    return out


def _calc_wilder_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    n = len(closes)
    if n < period + 1:
        return []
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr = sum(trs[:period]) / period
    out = [atr]
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        out.append(atr)
    return out


def compute_position_mgmt(
    dates: list[str], highs: list[float], lows: list[float], closes: list[float]
) -> dict[str, Any]:
    """Compute BitCow slow-layer (50W SMA + budget), EMA20 streak, and EMA84 extreme D price levels."""
    if not dates or len(dates) != len(closes) or len(closes) < 85:
        return {"available": False, "reason": f"日线样本不足 ({len(closes)}/85)"}

    cur_close = closes[-1]
    weekly_closes: list[float] = []
    last_wk: tuple[int, int] | None = None
    for d_str, c in zip(dates, closes):
        iso = dt.date.fromisoformat(d_str).isocalendar()
        wk = (iso.year, iso.week)
        if wk == last_wk:
            weekly_closes[-1] = c
        else:
            weekly_closes.append(c)
            last_wk = wk

    if len(weekly_closes) >= 50:
        w_sma50 = sum(weekly_closes[-50:]) / 50.0
        slow_bull = cur_close > w_sma50
        slow_layer: dict[str, Any] = {
            "available": True,
            "w_sma50": round(w_sma50, 2),
            "slow_bull": slow_bull,
            "label": "牛市多头" if slow_bull else "熊市空头",
            "dist_to_w50_pct": round((cur_close / w_sma50 - 1.0) * 100.0, 2),
            "budget_long_pct": 4.5 if slow_bull else 3.0,
            "budget_short_pct": 2.5 if slow_bull else 4.5,
        }
    else:
        slow_layer = {"available": False, "reason": f"周线样本不足 ({len(weekly_closes)}/50)"}

    ema20_vals = _calc_ema(closes, 20)
    aligned_closes_20 = closes[19:]
    ema20_cur = ema20_vals[-1]
    days_above = 0
    for c, e in zip(reversed(aligned_closes_20), reversed(ema20_vals)):
        if c > e:
            days_above += 1
        else:
            break
    days_below = 0
    for c, e in zip(reversed(aligned_closes_20), reversed(ema20_vals)):
        if c < e:
            days_below += 1
        else:
            break

    ema84_cur = _calc_ema(closes, 84)[-1]
    atr14_cur = _calc_wilder_atr(highs, lows, closes, 14)[-1]
    d_val = (cur_close - ema84_cur) / atr14_cur if atr14_cur > 0 else 0.0

    return {
        "available": True,
        "as_of_date": dates[-1],
        "close": round(cur_close, 2),
        "slow_layer": slow_layer,
        "ema20": round(ema20_cur, 2),
        "vs_ema20_pct": round((cur_close / ema20_cur - 1.0) * 100.0, 2),
        "days_above_ema20": days_above,
        "days_below_ema20": days_below,
        "ema84": round(ema84_cur, 2),
        "wilder_atr14": round(atr14_cur, 2),
        "D": round(d_val, 3),
        "extreme_price_levels": {
            "D_plus_4_5": round(ema84_cur + 4.5 * atr14_cur, 2),
            "D_plus_7_0": round(ema84_cur + 7.0 * atr14_cur, 2),
            "D_minus_4_8": round(ema84_cur - 4.8 * atr14_cur, 2),
            "D_minus_5_5": round(ema84_cur - 5.5 * atr14_cur, 2),
        },
        "extreme_put_full_tp": bool(d_val <= -5.5),
        "extreme_overheated_no_chase": bool(d_val >= 4.5),
    }


def _bs_call_put_price(spot: float, strike: float, t_years: float, sigma: float, is_call: bool, r: float = 0.04) -> float:
    if t_years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, (spot - strike) if is_call else (strike - spot))
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
    disc = math.exp(-r * t_years)
    if is_call:
        return spot * nd1 - strike * disc * nd2
    return strike * disc * (1.0 - nd2) - spot * (1.0 - nd1)


def invert_bs_iv(target_price: float, spot: float, strike: float, t_years: float, is_call: bool, r: float = 0.04) -> float | None:
    """Bisection Black-Scholes IV solver when vendor impliedVolatility is missing/corrupt."""
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    if not math.isfinite(target_price) or target_price <= intrinsic + 0.02:
        return None
    lo, hi = 0.03, 4.0
    p_lo = _bs_call_put_price(spot, strike, t_years, lo, is_call, r)
    p_hi = _bs_call_put_price(spot, strike, t_years, hi, is_call, r)
    if target_price < p_lo or target_price > p_hi:
        return None
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        pm = _bs_call_put_price(spot, strike, t_years, mid, is_call, r)
        if abs(pm - target_price) < 1e-4:
            return mid
        if pm < target_price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def parse_dvol_2y(raw_rows: list[Any], now_ms: int, window_days: int = 730, min_days: int = 700) -> dict[str, Any]:
    """Parse Deribit DVOL 1D rows cleanly separating closed UTC bars from forming intraday bar."""
    today_utc_ms = (now_ms // DAY_MS) * DAY_MS
    expected_as_of_ms = today_utc_ms - DAY_MS
    closed: dict[int, float] = {}
    live_val: float | None = None

    for row in raw_rows:
        try:
            ts, val = int(row[0]), float(row[4])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if ts <= EPOCH_SENTINEL_MS or not (math.isfinite(val) and val > 0):
            continue
        if ts + DAY_MS <= now_ms:
            closed[ts] = val
        elif ts == today_utc_ms:
            live_val = val

    if not closed:
        return {"available": False, "reason": "Deribit DVOL 返回为空"}
    keys = sorted(closed)
    last_ts = keys[-1]
    if last_ts < expected_as_of_ms - DAY_MS:
        return {"available": False, "reason": f"Deribit DVOL 数据过期 (最新 {dt.datetime.fromtimestamp(last_ts/1000, UTC).date()})"}

    cutoff_ts = last_ts - (window_days - 1) * DAY_MS
    win_keys = [k for k in keys if cutoff_ts <= k <= last_ts]
    if len(win_keys) < min_days:
        return {"available": False, "reason": f"DVOL 2Y 样本不足 ({len(win_keys)}/{min_days})"}

    vals = [closed[k] for k in win_keys]
    cur = vals[-1]
    prev = vals[-2] if len(vals) >= 2 else cur
    return {
        "available": True,
        "reason": "",
        "as_of_date": dt.datetime.fromtimestamp(last_ts / 1000, UTC).strftime("%Y-%m-%d"),
        "dvol": round(cur, 2),
        "dvol_1d_chg": round(cur - prev, 2),
        "dvol_2y_pct": calc_percentile(vals, cur),
        "dvol_2y_min": round(min(vals), 2),
        "dvol_2y_max": round(max(vals), 2),
        "dvol_2y_days": len(vals),
        "dvol_live_intraday": round(live_val, 2) if live_val is not None else None,
    }


def _fetch_binance_closed_ohlc(
    now_ms: int, limit: int = 800
) -> tuple[list[str], list[float], list[float], list[float]]:
    last_err: Exception | None = None
    for url in BINANCE_KLINES_URLS:
        try:
            r = requests.get(url, params={"symbol": "BTCUSDT", "interval": "1d", "limit": limit}, timeout=12)
            r.raise_for_status()
            rows = r.json()
            dates: list[str] = []
            highs: list[float] = []
            lows: list[float] = []
            closes: list[float] = []
            for row in rows:
                open_ms, close_ms = int(row[0]), int(row[6])
                h, l, c = float(row[2]), float(row[3]), float(row[4])
                if open_ms <= EPOCH_SENTINEL_MS or not (
                    math.isfinite(h) and math.isfinite(l) and math.isfinite(c) and c > 0
                ):
                    continue
                if close_ms < now_ms:  # strictly closed UTC daily candle
                    dates.append(dt.datetime.fromtimestamp(open_ms / 1000, UTC).strftime("%Y-%m-%d"))
                    highs.append(h)
                    lows.append(l)
                    closes.append(c)
            if closes:
                return dates, highs, lows, closes
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Binance 1D klines 获取失败: {type(last_err).__name__ if last_err else 'empty'}")


def _fetch_binance_closed_closes(now_ms: int, limit: int = 800) -> tuple[list[str], list[float]]:
    dates, _, _, closes = _fetch_binance_closed_ohlc(now_ms, limit=limit)
    return dates, closes


def fetch_btc_vol(now_ms: int | None = None) -> dict[str, Any]:
    now_ms = now_ms or int(time.time() * 1000)
    out: dict[str, Any] = {}
    try:
        r = requests.get(
            DERIBIT_DVOL_URL,
            params={
                "currency": "BTC",
                "start_timestamp": now_ms - 760 * DAY_MS,
                "end_timestamp": now_ms,
                "resolution": "86400",
            },
            timeout=15,
        )
        r.raise_for_status()
        raw_rows = (r.json().get("result") or {}).get("data") or []
        out = parse_dvol_2y(raw_rows, now_ms)
    except Exception as exc:
        out = {"available": False, "reason": f"Deribit 接口异常 ({type(exc).__name__})"}

    # Enrich with BTC HV20 + 2Y percentile & VRP (DVOL - HV20) + position_mgmt
    try:
        b_dates, b_highs, b_lows, b_closes = _fetch_binance_closed_ohlc(now_ms, limit=800)
        out["_position_mgmt"] = compute_position_mgmt(b_dates, b_highs, b_lows, b_closes)
        hv_series = calc_rolling_hv(b_closes, window=20, ann_days=365.0)
        hv_2y = hv_series[-730:] if len(hv_series) >= 730 else hv_series
        if len(hv_2y) >= 700:
            hv_cur = hv_2y[-1]
            out["hv20"] = round(hv_cur, 2)
            out["hv20_2y_pct"] = calc_percentile(hv_2y, hv_cur)
            out["hv20_as_of_date"] = b_dates[-1]
            if out.get("available") and out.get("dvol") is not None:
                out["vrp_dvol_minus_hv20"] = round(out["dvol"] - hv_cur, 2)
        else:
            out["hv20"] = None
            out["hv20_reason"] = f"BTC HV20 样本不足 ({len(hv_2y)})"
    except Exception as exc:
        out["hv20"] = None
        out["hv20_reason"] = str(exc)
        out["_position_mgmt"] = {"available": False, "reason": str(exc)}

    return out


def parse_cnn_fng_2y(payload: dict[str, Any], today_et: dt.date, min_points: int = 450) -> dict[str, Any]:
    """Parse CNN Fear & Greed JSON with sentinel filtering, ET trading-date alignment, and 2Y percentile."""
    fng_cur = payload.get("fear_and_greed") or {}
    hist_rows = (payload.get("fear_and_greed_historical") or {}).get("data") or []

    by_date: dict[str, float] = {}
    for item in hist_rows:
        try:
            x_ms = float(item["x"])
            y_val = float(item["y"])
        except (TypeError, ValueError, KeyError):
            continue
        if x_ms <= EPOCH_SENTINEL_MS or not (0.0 <= y_val <= 100.0 and math.isfinite(y_val)):
            continue
        d_str = dt.datetime.fromtimestamp(x_ms / 1000.0, UTC).astimezone(ET_TZ).strftime("%Y-%m-%d")
        by_date[d_str] = y_val

    # Incorporate top-level latest reading if valid
    cur_rating_raw = str(fng_cur.get("rating") or "").strip().lower()
    if fng_cur.get("score") is not None and fng_cur.get("timestamp"):
        try:
            cur_score = float(fng_cur["score"])
            ts_str = str(fng_cur["timestamp"])
            cur_dt = dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(ET_TZ).date()
            if cur_dt.year >= 2005 and 0.0 <= cur_score <= 100.0:
                by_date[cur_dt.isoformat()] = cur_score
        except Exception:
            pass

    if not by_date:
        return {"available": False, "reason": "CNN Fear & Greed 数据为空"}

    dates_sorted = sorted(by_date)
    latest_date = dt.date.fromisoformat(dates_sorted[-1])
    if (today_et - latest_date).days > 7:
        return {"available": False, "reason": f"CNN Fear & Greed 数据过期 ({dates_sorted[-1]})"}

    cutoff_date = (latest_date - dt.timedelta(days=730)).isoformat()
    win_dates = [d for d in dates_sorted if d >= cutoff_date]
    if len(win_dates) < min_points:
        return {"available": False, "reason": f"CNN Fear & Greed 2Y 样本不足 ({len(win_dates)}/{min_points})"}

    vals = [by_date[d] for d in win_dates]
    cur = vals[-1]
    prev = vals[-2] if len(vals) >= 2 else cur

    if not cur_rating_raw:
        if cur < 25:
            cur_rating_raw = "extreme fear"
        elif cur < 45:
            cur_rating_raw = "fear"
        elif cur <= 55:
            cur_rating_raw = "neutral"
        elif cur <= 75:
            cur_rating_raw = "greed"
        else:
            cur_rating_raw = "extreme greed"

    def _safe_float(k: str) -> float | None:
        try:
            v = float(fng_cur[k])
            return round(v, 1) if math.isfinite(v) else None
        except Exception:
            return None

    return {
        "available": True,
        "reason": "",
        "as_of_date": dates_sorted[-1],
        "score": round(cur, 1),
        "chg_1d": round(cur - prev, 1),
        "rating": cur_rating_raw,
        "rating_cn": FNG_RATING_CN.get(cur_rating_raw, cur_rating_raw),
        "pct_2y": calc_percentile(vals, cur),
        "min_2y": round(min(vals), 1),
        "max_2y": round(max(vals), 1),
        "sample_days_2y": len(vals),
        "prev_1w": _safe_float("previous_1_week"),
        "prev_1m": _safe_float("previous_1_month"),
    }


def fetch_us_fear_greed(today_et: dt.date | None = None) -> dict[str, Any]:
    today_et = today_et or dt.datetime.now(ET_TZ).date()
    start_date = (today_et - dt.timedelta(days=755)).isoformat()
    try:
        r = requests.get(f"{CNN_FNG_BASE_URL}/{start_date}", headers=BROWSER_HEADERS, timeout=12)
        r.raise_for_status()
        return parse_cnn_fng_2y(r.json(), today_et)
    except Exception as exc:
        return {"available": False, "reason": f"CNN 接口异常 ({type(exc).__name__})"}


def split_us_closed_bars(
    dates: list[str], closes: list[float], now_et: dt.datetime | None = None
) -> tuple[list[str], list[float], float | None]:
    """Separate forming US intraday / CBOE global-hours bar (before 16:15 ET) from completed regular closes."""
    now_et = now_et or dt.datetime.now(ET_TZ)
    if not dates:
        return [], [], None
    last_d = dt.date.fromisoformat(dates[-1])
    if last_d == now_et.date() and (now_et.weekday() >= 5 or (now_et.hour, now_et.minute) < (16, 15)):
        return dates[:-1], closes[:-1], round(closes[-1], 2)
    return dates, closes, None


def _fetch_yf_ohlc(
    sym: str, period: str = "3y", now_et: dt.datetime | None = None
) -> tuple[list[str], list[float], list[float], list[float], float | None]:
    df = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)
    if df is None or df.empty or "Close" not in df.columns:
        raise RuntimeError(f"{sym} yfinance 返回为空")
    df = df.dropna(subset=["Close"])
    dates: list[str] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    has_hl = "High" in df.columns and "Low" in df.columns
    for idx, row in df.iterrows():
        c = float(row["Close"])
        h = float(row["High"]) if has_hl and math.isfinite(float(row["High"])) else c
        l = float(row["Low"]) if has_hl and math.isfinite(float(row["Low"])) else c
        if idx.year >= 2005 and math.isfinite(c) and c > 0:
            dates.append(idx.strftime("%Y-%m-%d"))
            highs.append(h)
            lows.append(l)
            closes.append(c)
    dates, closes, live_val = split_us_closed_bars(dates, closes, now_et=now_et)
    if live_val is not None:
        highs = highs[:-1]
        lows = lows[:-1]
    if not dates:
        raise RuntimeError(f"{sym} 无有效已收盘序列")
    return dates, highs, lows, closes, live_val


def _fetch_yf_closes(sym: str, period: str = "3y", now_et: dt.datetime | None = None) -> tuple[list[str], list[float], float | None]:
    dates, _, _, closes, live_val = _fetch_yf_ohlc(sym, period=period, now_et=now_et)
    return dates, closes, live_val


def _index_2y_stats(
    dates: list[str],
    closes: list[float],
    today_sg: dt.date,
    live_val: float | None = None,
    window_bars: int = 504,
    min_bars: int = 480,
) -> dict[str, Any]:
    last_date = dt.date.fromisoformat(dates[-1])
    stale_days = (today_sg - last_date).days
    if stale_days > 7:
        return {"available": False, "reason": f"数据过期 ({dates[-1]})"}
    win = closes[-window_bars:] if len(closes) >= window_bars else closes
    if len(win) < min_bars:
        return {"available": False, "reason": f"2Y 样本不足 ({len(win)}/{min_bars})"}
    cur = win[-1]
    prev = win[-2] if len(win) >= 2 else cur
    out = {
        "available": True,
        "reason": "",
        "as_of_date": dates[-1],
        "is_weekend_or_holiday": stale_days >= 2,
        "value": round(cur, 2),
        "chg_1d": round(cur - prev, 2),
        "pct_2y": calc_percentile(win, cur),
        "min_2y": round(min(win), 2),
        "max_2y": round(max(win), 2),
        "sample_bars_2y": len(win),
    }
    if live_val is not None:
        out["live_intraday"] = live_val
    return out


def fetch_us_equity_vol(today_sg: dt.date | None = None, now_et: dt.datetime | None = None) -> dict[str, Any]:
    today_sg = today_sg or dt.datetime.now(SG_TZ).date()
    now_et = now_et or dt.datetime.now(ET_TZ)
    res: dict[str, Any] = {}
    for label, sym in (("vix", "^VIX"), ("vxn", "^VXN")):
        try:
            d, c, live_v = _fetch_yf_closes(sym, period="3y", now_et=now_et)
            res[label] = _index_2y_stats(d, c, today_sg, live_val=live_v)
        except Exception as exc:
            res[label] = {"available": False, "reason": f"{sym} 获取失败 ({type(exc).__name__})"}

    try:
        qd, qh, ql, qc, _ = _fetch_yf_ohlc("QQQ", period="3y", now_et=now_et)
        res["_qqq_position_mgmt"] = compute_position_mgmt(qd, qh, ql, qc)
        q_hv_all = calc_rolling_hv(qc, window=20, ann_days=252.0)
        q_hv_2y = q_hv_all[-504:] if len(q_hv_all) >= 504 else q_hv_all
        if len(q_hv_2y) >= 480:
            cur_hv = q_hv_2y[-1]
            res["qqq_hv20"] = {
                "available": True,
                "as_of_date": qd[-1],
                "value": round(cur_hv, 2),
                "pct_2y": calc_percentile(q_hv_2y, cur_hv),
            }
            if res["vxn"].get("available"):
                res["vrp_vxn_minus_qqq_hv20"] = round(res["vxn"]["value"] - cur_hv, 2)
        else:
            res["qqq_hv20"] = {"available": False, "reason": f"QQQ HV20 样本不足 ({len(q_hv_2y)})"}
    except Exception as exc:
        res["qqq_hv20"] = {"available": False, "reason": f"QQQ HV20 异常 ({type(exc).__name__})"}
        res["_qqq_position_mgmt"] = {"available": False, "reason": str(exc)}

    res["available"] = bool(res.get("vix", {}).get("available") and res.get("vxn", {}).get("available"))
    res["reason"] = "" if res["available"] else "VIX 或 VXN 部分不可用，请逐项查看"
    return res


def compute_atm_iv_30d_from_chains(
    spot: float,
    today_utc: dt.date,
    expirations: list[str],
    chain_loader: Callable[[str], tuple[Any, Any]],
) -> dict[str, Any]:
    """Compute constant-maturity 30D ATM IV (%) via variance interpolation across bracketing expiries."""
    if not (math.isfinite(spot) and spot > 0):
        return {"available": False, "reason": "无效标的价格"}

    valid_exps: list[tuple[int, str]] = []
    for exp_str in expirations:
        try:
            ed = dt.date.fromisoformat(exp_str)
        except ValueError:
            continue
        dte = (ed - today_utc).days
        if 7 <= dte <= 75:
            valid_exps.append((dte, exp_str))
    valid_exps.sort()
    if not valid_exps:
        return {"available": False, "reason": "无 7~75 天可用期权到期日"}

    below = [x for x in valid_exps if x[0] <= 30]
    above = [x for x in valid_exps if x[0] >= 30]
    chosen: list[tuple[int, str]] = []
    if below:
        chosen.append(below[-1])
    if above and (not chosen or above[0][1] != chosen[0][1]):
        chosen.append(above[0])
    if not chosen:
        chosen = [min(valid_exps, key=lambda x: abs(x[0] - 30))]

    term_ivs: list[tuple[int, str, float]] = []
    for dte, exp_str in chosen:
        calls_df, puts_df = chain_loader(exp_str)
        t_years = dte / 365.0
        leg_ivs: list[float] = []
        for is_call, side_df in ((True, calls_df), (False, puts_df)):
            if side_df is None or getattr(side_df, "empty", True):
                continue
            df = side_df.copy()
            df["dist"] = (df["strike"] - spot).abs()
            nearest = df.nsmallest(4, "dist")
            side_candidates: list[tuple[float, float]] = []
            for _, row in nearest.iterrows():
                strike = float(row.get("strike") or 0.0)
                if strike <= 0 or abs(strike - spot) / spot > 0.08:
                    continue
                iv_raw = float(row.get("impliedVolatility") or 0.0)
                bid = float(row.get("bid") or 0.0)
                ask = float(row.get("ask") or 0.0)
                last_p = float(row.get("lastPrice") or 0.0)
                iv_use: float | None = None
                if 0.03 < iv_raw < 4.0 and bid > 0 and ask > 0 and (ask - bid) / max(ask, 0.01) < 0.65:
                    iv_use = iv_raw
                else:
                    ref_p = 0.5 * (bid + ask) if (bid > 0 and ask >= bid) else last_p
                    bs_iv = invert_bs_iv(ref_p, spot, strike, t_years, is_call=is_call)
                    if bs_iv is not None and 0.03 < bs_iv < 4.0:
                        iv_use = bs_iv
                    elif 0.03 < iv_raw < 4.0:
                        iv_use = iv_raw
                if iv_use is not None:
                    side_candidates.append((abs(strike - spot), iv_use))
            side_candidates.sort(key=lambda x: x[0])
            leg_ivs.extend([v for _, v in side_candidates[:2]])
        if leg_ivs:
            term_ivs.append((dte, exp_str, sum(leg_ivs) / len(leg_ivs)))

    if not term_ivs:
        return {"available": False, "reason": "期权链无有效平值隐含波动率报价"}

    if len(term_ivs) == 1 or term_ivs[0][0] == term_ivs[1][0]:
        iv30 = term_ivs[0][2]
    else:
        (t1, _, v1), (t2, _, v2) = term_ivs[0], term_ivs[1]
        var1 = (v1 ** 2) * t1
        var2 = (v2 ** 2) * t2
        w = (30.0 - t1) / (t2 - t1)
        var30 = var1 + w * (var2 - var1)
        if var30 <= 0:
            iv30 = v1 + w * (v2 - v1)
        else:
            iv30 = math.sqrt(var30 / 30.0)

    return {
        "available": True,
        "reason": "",
        "atm_iv_30d": round(iv30 * 100.0, 2),
        "interpolated_terms": [
            {"dte": d, "expiration": e, "atm_iv": round(v * 100.0, 2)} for d, e, v in term_ivs
        ],
    }


def fetch_semi_vol(today_sg: dt.date | None = None, today_utc: dt.date | None = None, now_et: dt.datetime | None = None) -> dict[str, Any]:
    today_sg = today_sg or dt.datetime.now(SG_TZ).date()
    today_utc = today_utc or dt.datetime.now(UTC).date()
    now_et = now_et or dt.datetime.now(ET_TZ)
    out: dict[str, Any] = {"symbol": "SOXX"}

    try:
        sd, sh, sl, sc, live_spot = _fetch_yf_ohlc("SOXX", period="3y", now_et=now_et)
        out["_position_mgmt"] = compute_position_mgmt(sd, sh, sl, sc)
        last_date = dt.date.fromisoformat(sd[-1])
        stale_days = (today_sg - last_date).days
        if stale_days > 7:
            return {"available": False, "reason": f"SOXX 价格过期 ({sd[-1]})"}
        hv_all = calc_rolling_hv(sc, window=20, ann_days=252.0)
        hv_2y = hv_all[-504:] if len(hv_all) >= 504 else hv_all
        if len(hv_2y) < 480:
            return {"available": False, "reason": f"SOXX HV20 2Y 样本不足 ({len(hv_2y)}/480)"}
        cur_hv = hv_2y[-1]
        prev_hv = hv_2y[-2] if len(hv_2y) >= 2 else cur_hv
        spot = live_spot if live_spot is not None else sc[-1]
        out.update({
            "available": True,
            "reason": "",
            "as_of_date": sd[-1],
            "is_weekend_or_holiday": stale_days >= 2,
            "spot": round(spot, 2),
            "hv20": round(cur_hv, 2),
            "hv20_1d_chg": round(cur_hv - prev_hv, 2),
            "hv20_pct_2y": calc_percentile(hv_2y, cur_hv),
            "hv20_min_2y": round(min(hv_2y), 2),
            "hv20_max_2y": round(max(hv_2y), 2),
            "hv20_sample_bars_2y": len(hv_2y),
        })
    except Exception as exc:
        return {"available": False, "reason": f"SOXX 历史波动率获取失败 ({type(exc).__name__})"}

    try:
        tk = yf.Ticker("SOXX")
        exps = list(tk.options or [])
        iv_info = compute_atm_iv_30d_from_chains(
            spot=out["spot"],
            today_utc=today_utc,
            expirations=exps,
            chain_loader=lambda exp: (tk.option_chain(exp).calls, tk.option_chain(exp).puts),
        )
        out["atm_iv_30d_available"] = iv_info["available"]
        out["atm_iv_30d_reason"] = iv_info["reason"]
        if iv_info["available"]:
            out["atm_iv_30d"] = iv_info["atm_iv_30d"]
            out["atm_iv_terms"] = iv_info["interpolated_terms"]
            out["vrp_atm_iv_minus_hv20"] = round(iv_info["atm_iv_30d"] - out["hv20"], 2)
        else:
            out["atm_iv_30d"] = None
    except Exception as exc:
        out["atm_iv_30d_available"] = False
        out["atm_iv_30d_reason"] = f"SOXX 期权链获取异常 ({type(exc).__name__})"
        out["atm_iv_30d"] = None

    out["percentile_note"] = "SOXX 无免费 2Y 历史隐波序列；2Y 分位基于 SOXX HV20，实时 30D ATM IV 用于对比 IV-HV20 判断期权贵贱"
    return out


def build_payload() -> dict[str, Any]:
    now_sg = dt.datetime.now(SG_TZ)
    now_utc = now_sg.astimezone(UTC)
    now_et = now_sg.astimezone(ET_TZ)
    now_ms = int(now_utc.timestamp() * 1000)

    btc = fetch_btc_vol(now_ms=now_ms)
    fng = fetch_us_fear_greed(today_et=now_et.date())
    us_vol = fetch_us_equity_vol(today_sg=now_sg.date(), now_et=now_et)
    semi = fetch_semi_vol(today_sg=now_sg.date(), today_utc=now_utc.date(), now_et=now_et)

    pos_mgmt = {
        "BTC": btc.pop("_position_mgmt", {"available": False, "reason": "未计算"}),
        "QQQ": us_vol.pop("_qqq_position_mgmt", {"available": False, "reason": "未计算"}),
        "SOXX": semi.pop("_position_mgmt", {"available": False, "reason": "未计算"}),
    }

    return {
        "generated_at_sg": now_sg.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "percentile_formula": "count(values <= current) / total * 100 (2Y rolling window)",
        "all_core_available": bool(
            btc.get("available")
            and fng.get("available")
            and us_vol.get("available")
            and semi.get("available")
            and semi.get("atm_iv_30d_available")
        ),
        "btc_vol": btc,
        "us_fear_greed": fng,
        "us_equity_vol": us_vol,
        "semi_vol": semi,
        "position_mgmt": pos_mgmt,
    }


def main() -> None:
    payload = build_payload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
