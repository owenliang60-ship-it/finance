from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from backtest.pipeline.primitives.pit_data import PitData


class PipelineFactor(ABC):
    name: str

    @abstractmethod
    def compute(
        self,
        pit_data: PitData,
        symbols: List[str],
        as_of_date: str,
        params: Dict[str, Any],
    ) -> Dict[str, float]:
        """Return {symbol: score} for the given universe/date."""
