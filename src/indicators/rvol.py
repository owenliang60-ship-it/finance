"""
RVOL 指标 (Relative Volume - 相对成交量)
(Vol - Mean) / StdDev 标准化成交量

公式:
    RVOL = (当前成交量 - 均值) / 标准差

信号:
    RVOL >= 4σ → 异常放量信号
    RVOL >= 2σ → 明显放量

参考: Technical Analysis of Stocks & Commodities, April 2014
      RelativeVolumeStDev by Melvin E. Dickover
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict


def calculate_rvol(
    volumes: pd.Series,
    lookback: int = 120
) -> Optional[float]:
    """
    计算 RVOL (Relative Volume)

    RVOL = (当前成交量 - 均值) / 标准差

    结果解读:
    - RVOL = 4 → 当前成交量比均值高 4 个标准差（异常放量）
    - RVOL = 2 → 当前成交量比均值高 2 个标准差（明显放量）
    - RVOL = 0 → 当前成交量等于均值

    Args:
        volumes: 成交量序列 (美股用 volume 即可)
        lookback: 回看周期 (默认 120 天)

    Returns:
        RVOL 值（几个标准差），None 表示数据不足
    """
    if len(volumes) < lookback + 1:
        return None

    # 使用前 lookback 天计算均值和标准差（不包含当天）
    historical = volumes.iloc[-(lookback + 1):-1]
    current = volumes.iloc[-1]

    avg = historical.mean()
    std = historical.std(ddof=0)  # 总体标准差

    if std == 0 or pd.isna(std):
        return 0.0

    rvol = (current - avg) / std
    return float(rvol)


def calculate_rvol_series(
    volumes: pd.Series,
    lookback: int = 120
) -> pd.Series:
    """
    计算 RVOL 序列（每一天的 RVOL）

    Args:
        volumes: 成交量序列，按时间正序
        lookback: 回看周期

    Returns:
        RVOL 序列
    """
    rvol_series = pd.Series(index=volumes.index, dtype=float)

    for i in range(lookback, len(volumes)):
        historical = volumes.iloc[i - lookback:i]
        current = volumes.iloc[i]

        avg = historical.mean()
        std = historical.std(ddof=0)

        if std == 0 or pd.isna(std):
            rvol_series.iloc[i] = 0.0
        else:
            rvol_series.iloc[i] = (current - avg) / std

    return rvol_series


def check_rvol_signal(
    rvol: float,
    threshold: float = 4.0
) -> bool:
    """
    检测 RVOL 是否触发信号

    Args:
        rvol: RVOL 值
        threshold: 阈值（默认 4 个标准差）

    Returns:
        是否触发信号
    """
    if rvol is None:
        return False
    return rvol >= threshold


def analyze_rvol(df: pd.DataFrame, lookback: int = 120) -> Dict:
    """
    分析单只股票的 RVOL

    Args:
        df: 量价数据 DataFrame，必须包含 'volume' 列，按时间正序
        lookback: 回看周期

    Returns:
        {
            "current": 当前 RVOL 值,
            "avg_volume": 历史平均成交量,
            "current_volume": 当前成交量,
            "signal": "extreme_volume" / "high_volume" / "normal" / "low_volume",
            "sigma": 几个标准差,
            "description": 描述文字
        }
    """
    result = {
        "current": None,
        "avg_volume": None,
        "current_volume": None,
        "signal": "normal",
        "sigma": None,
        "description": ""
    }

    if df is None or df.empty or 'volume' not in df.columns:
        result["description"] = "数据不足"
        return result

    # 确保按时间正序
    if 'date' in df.columns:
        df = df.sort_values('date')

    volumes = df['volume']

    if len(volumes) < lookback + 1:
        result["description"] = f"数据不足，需要 {lookback + 1} 天，实际 {len(volumes)} 天"
        return result

    # 计算 RVOL
    rvol = calculate_rvol(volumes, lookback)
    historical = volumes.iloc[-(lookback + 1):-1]

    result["current"] = round(rvol, 2) if rvol is not None else None
    result["sigma"] = result["current"]
    result["avg_volume"] = int(historical.mean())
    result["current_volume"] = int(volumes.iloc[-1])

    # 判断信号
    if rvol is None:
        result["signal"] = "unknown"
        result["description"] = "无法计算 RVOL"
    elif rvol >= 4:
        result["signal"] = "extreme_volume"
        result["description"] = f"RVOL={rvol:.1f}σ，异常放量！成交量是均值的 {result['current_volume']/result['avg_volume']:.1f} 倍"
    elif rvol >= 2:
        result["signal"] = "high_volume"
        result["description"] = f"RVOL={rvol:.1f}σ，明显放量"
    elif rvol <= -2:
        result["signal"] = "low_volume"
        result["description"] = f"RVOL={rvol:.1f}σ，成交量萎缩"
    else:
        result["signal"] = "normal"
        result["description"] = f"RVOL={rvol:.1f}σ，成交量正常"

    return result


if __name__ == "__main__":
    # 测试
    import sys
    sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
    from src.data import get_price_df

    print("测试 RVOL 指标 (NVDA):")
    df = get_price_df("NVDA")
    if df is not None:
        df = df.sort_values('date')
        result = analyze_rvol(df)
        print(f"  当前 RVOL: {result['current']}σ")
        print(f"  当前成交量: {result['current_volume']:,}")
        print(f"  平均成交量: {result['avg_volume']:,}")
        print(f"  信号: {result['signal']}")
        print(f"  描述: {result['description']}")
