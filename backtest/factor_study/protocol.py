"""
Factor ABC — 通用因子接口定义

任何因子只需实现 compute() 返回 {symbol: score} 即可接入框架。
FactorMeta 描述因子的元信息（名称、分数范围、方向性等）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd


@dataclass
class FactorMeta:
    """因子元信息"""

    name: str                       # "RS_Rating_B"
    score_name: str                 # "rs_rank"
    score_range: Tuple[float, float]  # (0, 99)
    higher_is_stronger: bool        # True = 高分看多
    min_data_days: int = 70         # 最低数据需求

    def __str__(self) -> str:
        direction = "↑" if self.higher_is_stronger else "↓"
        lo, hi = self.score_range
        return f"{self.name} ({self.score_name}: {lo}-{hi} {direction})"


class Factor(ABC):
    """
    通用因子基类

    子类实现:
    - meta: 返回 FactorMeta
    - compute(): 接收 price_dict (已 slice 到 date)，返回 {symbol: score}
    """

    @property
    @abstractmethod
    def meta(self) -> FactorMeta:
        """因子元信息"""
        ...

    @abstractmethod
    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        """
        计算因子分数

        Args:
            price_dict: {symbol: price_df}，已 slice 到 date（防前视）
            date: 计算日期

        Returns:
            {symbol: score}
        """
        ...

    def __repr__(self) -> str:
        return f"<Factor {self.meta.name}>"
