"""
数据适配器 — 加载不同市场的价格数据和 RS 计算函数
"""

from backtest.adapters.us_stocks import USStocksAdapter
from backtest.adapters.crypto import CryptoAdapter

__all__ = ["USStocksAdapter", "CryptoAdapter"]
