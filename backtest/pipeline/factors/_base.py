from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import pandas as pd

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

    def compute_panel(
        self,
        pit_data: PitData,
        universe_df: pd.DataFrame,
        params: Dict[str, Any],
    ) -> Optional[pd.DataFrame]:
        """Optionally return a raw score panel indexed by date with symbol columns."""
        return None
