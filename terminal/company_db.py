"""
Per-company persistent storage.

Every analysis, rating, memo, kill condition saves to data/companies/{SYMBOL}/.
Files (JSON/JSONL/Markdown) — human-readable, git-trackable.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_COMPANIES_DIR = _PROJECT_ROOT / "data" / "companies"


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def get_company_dir(symbol: str) -> Path:
    """Get (or create) the per-company data directory."""
    symbol = symbol.upper()
    d = _COMPANIES_DIR / symbol
    for sub in ["memos", "analyses", "debates", "strategies", "trades", "research"]:
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path, default: Any = None) -> Any:
    """Read a JSON file, returning default if missing or corrupt."""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read {path}: {e}")
        return default


def _write_json(path: Path, data: Any) -> None:
    """Write JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_jsonl(path: Path, record: dict) -> None:
    """Append a single JSON line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# OPRMS ratings
# ---------------------------------------------------------------------------

def save_oprms(symbol: str, rating: dict) -> None:
    """
    Save current OPRMS rating and append to changelog.

    rating dict: {dna, timing, timing_coeff, evidence, investment_bucket, ...}
    Dual-writes to both JSON files and SQLite company.db.
    """
    d = get_company_dir(symbol)
    now = datetime.now().isoformat()
    rating["updated_at"] = now
    rating["symbol"] = symbol.upper()

    # Primary: JSON files
    _write_json(d / "oprms.json", rating)
    _append_jsonl(d / "oprms_changelog.jsonl", rating)
    logger.info(f"Saved OPRMS for {symbol}: DNA={rating.get('dna')} Timing={rating.get('timing')}")

    # Secondary: SQLite dual-write
    try:
        from terminal.company_store import get_store
        store = get_store()
        store.upsert_company(symbol, source="analysis")
        store.save_oprms_rating(
            symbol=symbol,
            dna=rating.get("dna", "?"),
            timing=rating.get("timing", "?"),
            timing_coeff=rating.get("timing_coeff", 0.5),
            conviction_modifier=rating.get("conviction_modifier"),
            evidence=rating.get("evidence", []),
            investment_bucket=rating.get("investment_bucket", ""),
            verdict=rating.get("verdict", ""),
            position_pct=rating.get("position_pct"),
        )
    except Exception as e:
        logger.warning(f"SQLite dual-write failed (non-fatal): {e}")


def get_oprms(symbol: str) -> Optional[dict]:
    """Get current OPRMS rating for a ticker. Reads SQLite first, falls back to JSON."""
    # Try SQLite first
    try:
        from terminal.company_store import get_store
        store = get_store()
        rating = store.get_current_oprms(symbol)
        if rating:
            return rating
    except Exception:
        pass
    # Fallback: JSON file
    d = _COMPANIES_DIR / symbol.upper()
    return _read_json(d / "oprms.json")


def get_oprms_history(symbol: str) -> List[dict]:
    """Get full OPRMS changelog as list of dicts."""
    path = _COMPANIES_DIR / symbol.upper() / "oprms_changelog.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


# ---------------------------------------------------------------------------
# Kill conditions
# ---------------------------------------------------------------------------

def save_kill_conditions(symbol: str, conditions: List[dict]) -> None:
    """
    Save active kill conditions.

    Each condition: {description, metric, threshold, status}
    """
    d = get_company_dir(symbol)
    data = {
        "symbol": symbol.upper(),
        "updated_at": datetime.now().isoformat(),
        "conditions": conditions,
    }
    _write_json(d / "kill_conditions.json", data)


def get_kill_conditions(symbol: str) -> List[dict]:
    """Get active kill conditions for a ticker."""
    d = _COMPANIES_DIR / symbol.upper()
    data = _read_json(d / "kill_conditions.json", {})
    return data.get("conditions", [])


# ---------------------------------------------------------------------------
# Memos
# ---------------------------------------------------------------------------

def save_memo(symbol: str, text: str, memo_type: str = "investment") -> Path:
    """
    Save an investment memo as timestamped markdown.

    Returns the path to the saved file.
    """
    d = get_company_dir(symbol)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{memo_type}.md"
    path = d / "memos" / filename
    path.write_text(text, encoding="utf-8")
    logger.info(f"Saved memo for {symbol}: {path.name}")
    return path


def get_all_memos(symbol: str) -> List[dict]:
    """Get all memos for a ticker, newest first."""
    d = _COMPANIES_DIR / symbol.upper() / "memos"
    if not d.exists():
        return []
    memos = []
    for f in sorted(d.glob("*.md"), reverse=True):
        memos.append({
            "filename": f.name,
            "path": str(f),
            "size_chars": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return memos


# ---------------------------------------------------------------------------
# Analyses (individual lens outputs)
# ---------------------------------------------------------------------------

def save_analysis(symbol: str, lens_name: str, text: str) -> Path:
    """Save a single lens analysis."""
    d = get_company_dir(symbol)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = lens_name.lower().replace(" ", "_").replace("-", "_")
    filename = f"{ts}_{slug}.md"
    path = d / "analyses" / filename
    path.write_text(text, encoding="utf-8")
    return path


def get_analyses(symbol: str) -> List[dict]:
    """Get all analyses for a ticker."""
    d = _COMPANIES_DIR / symbol.upper() / "analyses"
    if not d.exists():
        return []
    analyses = []
    for f in sorted(d.glob("*.md"), reverse=True):
        analyses.append({
            "filename": f.name,
            "path": str(f),
            "size_chars": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return analyses


# ---------------------------------------------------------------------------
# Alpha Layer (Layer 2) analyses
# ---------------------------------------------------------------------------

def save_alpha_package(symbol: str, alpha_data: dict) -> Path:
    """
    Save Layer 2 analysis to data/companies/{SYM}/analyses/{ts}_alpha.json.

    Returns the path to the saved file.
    """
    d = get_company_dir(symbol)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = d / "analyses" / f"{ts}_alpha.json"
    alpha_data["symbol"] = symbol.upper()
    alpha_data["saved_at"] = datetime.now().isoformat()
    _write_json(path, alpha_data)
    logger.info(f"Saved alpha package for {symbol}: {path.name}")
    return path


def get_latest_alpha(symbol: str) -> Optional[dict]:
    """Get the most recent Layer 2 analysis for a ticker."""
    d = _COMPANIES_DIR / symbol.upper() / "analyses"
    if not d.exists():
        return None
    alpha_files = sorted(d.glob("*_alpha.json"), reverse=True)
    if not alpha_files:
        return None
    return _read_json(alpha_files[0])


# ---------------------------------------------------------------------------
# Debates
# ---------------------------------------------------------------------------

def save_debate(symbol: str, debate_summary: dict) -> Path:
    """Save a debate summary as JSON."""
    d = get_company_dir(symbol)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = d / "debates" / f"{ts}_debate.json"
    _write_json(path, debate_summary)
    return path


# ---------------------------------------------------------------------------
# Trade log
# ---------------------------------------------------------------------------

def log_trade(symbol: str, trade: dict) -> None:
    """Append a trade entry to the ticker's trade log."""
    d = get_company_dir(symbol)
    trade["timestamp"] = datetime.now().isoformat()
    _append_jsonl(d / "trades" / "log.jsonl", trade)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def save_meta(symbol: str, meta: dict) -> None:
    """Save ticker metadata (themes, timestamps, etc.)."""
    d = get_company_dir(symbol)
    existing = _read_json(d / "meta.json", {})
    existing.update(meta)
    existing["updated_at"] = datetime.now().isoformat()
    _write_json(d / "meta.json", existing)


def get_meta(symbol: str) -> dict:
    """Get ticker metadata."""
    d = _COMPANIES_DIR / symbol.upper()
    return _read_json(d / "meta.json", {})


# ---------------------------------------------------------------------------
# Aggregate record
# ---------------------------------------------------------------------------

@dataclass
class CompanyRecord:
    """Everything we know about a company, aggregated."""
    symbol: str
    oprms: Optional[dict] = None
    oprms_history: List[dict] = field(default_factory=list)
    kill_conditions: List[dict] = field(default_factory=list)
    memos: List[dict] = field(default_factory=list)
    analyses: List[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    has_data: bool = False


def get_company_record(symbol: str) -> CompanyRecord:
    """Load the full aggregate record for a company."""
    symbol = symbol.upper()
    d = _COMPANIES_DIR / symbol

    record = CompanyRecord(symbol=symbol)

    if not d.exists():
        return record

    record.has_data = True
    record.oprms = get_oprms(symbol)
    record.oprms_history = get_oprms_history(symbol)
    record.kill_conditions = get_kill_conditions(symbol)
    record.memos = get_all_memos(symbol)
    record.analyses = get_analyses(symbol)
    record.meta = get_meta(symbol)

    return record


def list_all_companies() -> List[str]:
    """List all tickers that have a company record. Merges SQLite + filesystem."""
    symbols = set()
    # SQLite source
    try:
        from terminal.company_store import get_store
        store = get_store()
        for row in store.list_companies():
            symbols.add(row["symbol"])
    except Exception:
        pass
    # Filesystem source
    if _COMPANIES_DIR.exists():
        for d in _COMPANIES_DIR.iterdir():
            if d.is_dir() and d.name.isupper():
                symbols.add(d.name)
    return sorted(symbols)
