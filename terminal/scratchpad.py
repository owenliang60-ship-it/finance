"""
Analysis scratchpad — JSONL logging for transparency and replay.

Every analysis run creates a timestamped scratchpad log recording:
- Initial query/parameters
- Tool calls (data fetched, API calls)
- Reasoning steps (lens analysis, debate)
- Final output (memo, OPRMS rating)

Storage: data/companies/{SYMBOL}/scratchpad/{timestamp}_{hash}.jsonl
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_COMPANIES_DIR = _PROJECT_ROOT / "data" / "companies"


# ---------------------------------------------------------------------------
# AnalysisScratchpad
# ---------------------------------------------------------------------------

class AnalysisScratchpad:
    """
    Records every step of an analysis run to a JSONL log.

    Each log file is uniquely identified by timestamp + hash of query parameters.
    Events are appended line-by-line for stream processing.
    """

    def __init__(self, symbol: str, depth: str, query: Optional[str] = None):
        """
        Initialize scratchpad and log initial query.

        Args:
            symbol: Stock ticker
            depth: Analysis depth (quick/standard/full)
            query: Optional custom query (defaults to standard analysis)
        """
        self.symbol = symbol.upper()
        self.depth = depth
        self.query = query or f"Analyze {symbol} at {depth} depth"

        # Generate unique filename
        self.timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        query_str = f"{symbol}_{depth}_{self.query}"
        self.hash = hashlib.md5(query_str.encode()).hexdigest()[:8]

        # Setup log path
        scratchpad_dir = _COMPANIES_DIR / self.symbol / "scratchpad"
        scratchpad_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = scratchpad_dir / f"{self.timestamp}_{self.hash}.jsonl"

        # Log initial query
        self._append({
            "type": "query",
            "timestamp": datetime.now().isoformat(),
            "symbol": self.symbol,
            "depth": self.depth,
            "query": self.query,
        })

        logger.info(f"Scratchpad started: {self.log_path}")

    def log_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: Any,
    ) -> None:
        """
        Log a tool/API call.

        Args:
            tool_name: Name of tool (e.g., "FMPPriceTool", "fetch_income_statement")
            args: Input arguments
            result: Raw result (will be truncated if too large)
        """
        # Calculate result size
        result_str = json.dumps(result, ensure_ascii=False)
        result_size = len(result_str)

        # Truncate large results for storage
        if result_size > 5000:
            if isinstance(result, dict):
                result = {
                    "_truncated": True,
                    "_size": result_size,
                    "_preview": str(result)[:500] + "...",
                }
            elif isinstance(result, list):
                result = {
                    "_truncated": True,
                    "_size": result_size,
                    "_count": len(result),
                    "_preview": str(result[:3]) + "...",
                }
            else:
                result = {
                    "_truncated": True,
                    "_size": result_size,
                    "_preview": str(result)[:500] + "...",
                }

        self._append({
            "type": "tool_call",
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "args": args,
            "result": result,
            "result_size": result_size,
        })

    def log_reasoning(self, step: str, content: str) -> None:
        """
        Log a reasoning/analysis step.

        Args:
            step: Step identifier (e.g., "valuation_lens", "debate_growth_vs_risk")
            content: Reasoning content (truncated to 1000 chars)
        """
        # Truncate long reasoning
        if len(content) > 1000:
            content = content[:1000] + f"... [truncated, {len(content)} chars total]"

        self._append({
            "type": "reasoning",
            "timestamp": datetime.now().isoformat(),
            "step": step,
            "content": content,
        })

    def log_lens_complete(self, lens_name: str, output_path: Optional[str] = None) -> None:
        """
        Log completion of a lens analysis.

        Args:
            lens_name: Name of lens (e.g., "growth_story", "valuation_check")
            output_path: Optional path to saved lens output
        """
        self._append({
            "type": "lens_complete",
            "timestamp": datetime.now().isoformat(),
            "lens": lens_name,
            "output_path": output_path,
        })

    def log_final_rating(self, oprms: Dict[str, Any]) -> None:
        """
        Log final OPRMS rating.

        Args:
            oprms: OPRMS rating dict (dna, timing, timing_coeff, etc.)
        """
        self._append({
            "type": "final_rating",
            "timestamp": datetime.now().isoformat(),
            "oprms": oprms,
        })
        logger.info(f"Scratchpad complete: {self.log_path}")

    def get_path(self) -> Path:
        """Return path to scratchpad log file."""
        return self.log_path

    def _append(self, entry: Dict[str, Any]) -> None:
        """Append a single JSON line to the log."""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def read_scratchpad(log_path: Path) -> List[Dict[str, Any]]:
    """
    Read and parse a scratchpad JSONL file.

    Args:
        log_path: Path to scratchpad log file

    Returns:
        List of event dicts in chronological order
    """
    if not log_path.exists():
        logger.warning(f"Scratchpad not found: {log_path}")
        return []

    events = []
    with open(log_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON at line {i} in {log_path}: {e}")

    return events


def list_scratchpads(symbol: str) -> List[Path]:
    """
    List all scratchpad logs for a ticker (newest first).

    Args:
        symbol: Stock ticker

    Returns:
        List of scratchpad log paths, sorted by timestamp descending
    """
    scratchpad_dir = _COMPANIES_DIR / symbol.upper() / "scratchpad"
    if not scratchpad_dir.exists():
        return []

    logs = list(scratchpad_dir.glob("*.jsonl"))
    logs.sort(reverse=True)  # Newest first
    return logs


def get_latest_scratchpad(symbol: str) -> Optional[Path]:
    """
    Get path to most recent scratchpad log for a ticker.

    Args:
        symbol: Stock ticker

    Returns:
        Path to latest scratchpad, or None if no logs exist
    """
    logs = list_scratchpads(symbol)
    return logs[0] if logs else None
