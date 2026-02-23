"""
币圈 RS 纯计算模块 — 从 Quant/scanners/binance_rs_fast_scanner.py 提取

脱离 Binance API / Telegram / 缓存依赖。
输入: {symbol: np.ndarray(close_prices)} → 输出: DataFrame[symbol, ..., rs_rank]

两种方法:
- Method B: 风险调整 Z-Score (7d/3d/1d)
- Method C: Clenow 回归动量 (7d/3d/1d)
"""

import numpy as np
import pandas as pd
from scipy.stats import rankdata, zscore as scipy_zscore, linregress
from typing import Dict


# 配置 (短周期)
CRYPTO_RS_CONFIG = {
    "window_long": 7,
    "window_mid": 3,
    "window_short": 1,
    "skip_days": 1,
    "annual_factor": 365,
    "min_data_days": 15,
}


def compute_crypto_rs_b(price_dict: Dict[str, np.ndarray]) -> pd.DataFrame:
    """
    Method B — 风险调整 Z-Score (7d/3d/1d)

    Args:
        price_dict: {symbol: close_prices_array}

    Returns:
        DataFrame[symbol, ret_7d, ret_3d, ret_1d,
                  z_7d, z_3d, z_1d, composite, rs_rank]
    """
    cfg = CRYPTO_RS_CONFIG
    skip = cfg["skip_days"]
    w_long = cfg["window_long"]
    w_mid = cfg["window_mid"]
    w_short = cfg["window_short"]
    min_days = cfg["min_data_days"]
    ann = np.sqrt(cfg["annual_factor"])

    records = []

    for symbol, close in price_dict.items():
        n = len(close)
        if n < min_days:
            continue

        end_idx = n - 1 - skip
        if end_idx < 0 or (end_idx - w_long) < 0:
            continue

        # 收益率
        ret_7d = close[end_idx] / close[end_idx - w_long] - 1
        ret_3d = close[end_idx] / close[end_idx - w_mid] - 1
        ret_1d = close[end_idx] / close[end_idx - w_short] - 1

        # 风险调整 (仅 7d)
        daily_returns = np.diff(close) / close[:-1]
        vol_start = max(0, end_idx - w_long)
        vol_7d = np.std(daily_returns[vol_start:end_idx], ddof=1) * ann

        ra_7d = ret_7d / vol_7d if vol_7d > 1e-10 else 0.0
        ra_3d = ret_3d
        ra_1d = ret_1d

        records.append({
            "symbol": symbol,
            "ret_7d": ret_7d,
            "ret_3d": ret_3d,
            "ret_1d": ret_1d,
            "_ra_7d": ra_7d,
            "_ra_3d": ra_3d,
            "_ra_1d": ra_1d,
        })

    if not records:
        return pd.DataFrame(columns=[
            "symbol", "ret_7d", "ret_3d", "ret_1d",
            "z_7d", "z_3d", "z_1d", "composite", "rs_rank",
        ])

    df = pd.DataFrame(records)

    if len(df) <= 1:
        df["z_7d"] = 0.0
        df["z_3d"] = 0.0
        df["z_1d"] = 0.0
    else:
        df["z_7d"] = np.clip(scipy_zscore(df["_ra_7d"], ddof=1), -3, 3)
        df["z_3d"] = np.clip(scipy_zscore(df["_ra_3d"], ddof=1), -3, 3)
        df["z_1d"] = np.clip(scipy_zscore(df["_ra_1d"], ddof=1), -3, 3)

    df["composite"] = (
        0.40 * df["z_7d"]
        + 0.35 * df["z_3d"]
        + 0.25 * df["z_1d"]
    )

    if len(df) <= 1:
        df["rs_rank"] = 50
    else:
        pct = rankdata(df["composite"], method="average") / len(df)
        df["rs_rank"] = np.clip(np.floor(pct * 100).astype(int), 0, 99)

    df = df[[
        "symbol", "ret_7d", "ret_3d", "ret_1d",
        "z_7d", "z_3d", "z_1d", "composite", "rs_rank",
    ]].reset_index(drop=True)

    return df


def _clenow_momentum_crypto(prices: np.ndarray, window: int) -> float:
    """Clenow 动量 (币圈版, 年化365天)"""
    if window < 2 or len(prices) < window:
        return 0.0

    tail = prices[-window:]
    if np.any(tail <= 0):
        return 0.0

    log_prices = np.log(tail)
    x = np.arange(window)

    try:
        slope, _, r_value, _, _ = linregress(x, log_prices)
    except Exception:
        return 0.0

    r_squared = r_value ** 2
    annualized = (np.exp(slope) ** 365) - 1
    annualized = np.clip(annualized, -10, 100)
    return annualized * r_squared


def compute_crypto_rs_c(price_dict: Dict[str, np.ndarray]) -> pd.DataFrame:
    """
    Method C — Clenow 回归动量 (7d/3d/1d)

    Args:
        price_dict: {symbol: close_prices_array}

    Returns:
        DataFrame[symbol, clenow_7d, clenow_3d, clenow_1d, composite, rs_rank]
    """
    cfg = CRYPTO_RS_CONFIG
    w_long = cfg["window_long"]
    w_mid = cfg["window_mid"]
    w_short = cfg["window_short"]
    min_days = cfg["min_data_days"]

    records = []

    for symbol, close in price_dict.items():
        if len(close) < min_days:
            continue

        c7 = _clenow_momentum_crypto(close, w_long)
        c3 = _clenow_momentum_crypto(close, w_mid)
        c1 = _clenow_momentum_crypto(close, w_short)

        records.append({
            "symbol": symbol,
            "clenow_7d": c7,
            "clenow_3d": c3,
            "clenow_1d": c1,
        })

    if not records:
        return pd.DataFrame(columns=[
            "symbol", "clenow_7d", "clenow_3d", "clenow_1d",
            "composite", "rs_rank",
        ])

    df = pd.DataFrame(records)

    df["composite"] = (
        0.50 * df["clenow_7d"]
        + 0.30 * df["clenow_3d"]
        + 0.20 * df["clenow_1d"]
    )

    if len(df) <= 1:
        df["rs_rank"] = 50
    else:
        pct = rankdata(df["composite"], method="average") / len(df)
        df["rs_rank"] = np.clip(np.floor(pct * 100).astype(int), 0, 99)

    df = df[[
        "symbol", "clenow_7d", "clenow_3d", "clenow_1d",
        "composite", "rs_rank",
    ]].reset_index(drop=True)

    return df
