"""
Options Commands — Orchestrator for /options skill.

prepare_options_context(symbol) collects all data needed for the options
strategy discussion in one call.
"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from terminal.company_store import get_store
from terminal.options.iv_tracker import get_iv_history_summary
from terminal.options.chain_analyzer import (
    fetch_and_store_chain,
    analyze_liquidity,
    get_term_structure,
    get_earnings_proximity,
)
from terminal.options.formatter import format_options_context

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent


def prepare_options_context(
    symbol: str,
    store=None,
    client=None,
    fmp_client=None,
    skip_chain_fetch: bool = False,
) -> Dict[str, Any]:
    """Collect all data needed for options strategy discussion.

    This is the main entry point called by the /options skill.
    It gathers:
    - Underlying price
    - Deep analysis summary + OPRMS
    - IV summary (rank, percentile, HV)
    - Options chain liquidity
    - Term structure
    - Earnings proximity
    - Kill conditions
    - Report summary path

    Args:
        symbol: Stock ticker
        store: CompanyStore instance (defaults to singleton)
        client: MarketDataClient instance (defaults to singleton)
        fmp_client: FMPClient instance for earnings (defaults to singleton)
        skip_chain_fetch: Skip fetching new chain data (use existing snapshot)

    Returns:
        Complete context dict for the options skill
    """
    symbol = symbol.upper()

    if store is None:
        store = get_store()

    # 1. Fetch and store chain data (unless skipped)
    chain_summary = None
    underlying_price = None
    if not skip_chain_fetch:
        chain_summary = fetch_and_store_chain(symbol, store, client=client)
        if chain_summary:
            underlying_price = chain_summary.get("underlying_price")

    # 2. If no underlying price from chain, try FMP
    if underlying_price is None:
        if fmp_client is None:
            try:
                from src.data.fmp_client import fmp_client as _fmp
                fmp_client = _fmp
            except ImportError:
                fmp_client = None
        if fmp_client:
            underlying_price = fmp_client.get_realtime_price(symbol)

    # 3. Deep analysis + OPRMS
    deep_analysis = _get_deep_analysis(symbol, store)
    oprms = _get_oprms(symbol, store)

    # 4. IV Summary
    iv_summary = get_iv_history_summary(symbol, store)

    # 5. Liquidity
    liquidity = analyze_liquidity(symbol, store)

    # 6. Term structure
    term_structure = get_term_structure(symbol, store)

    # 7. Earnings proximity
    earnings = get_earnings_proximity(symbol, fmp_client=fmp_client)

    # 8. Kill conditions
    kill_conditions = store.get_kill_conditions(symbol)

    # 9. Report summary path
    report_summary_path = _find_report_summary(symbol)

    # Build context
    ctx = {
        "symbol": symbol,
        "underlying_price": underlying_price,
        "deep_analysis": deep_analysis,
        "oprms": oprms,
        "iv_summary": iv_summary,
        "liquidity": liquidity,
        "term_structure": term_structure,
        "earnings": earnings,
        "chain_summary": chain_summary,
        "report_summary_path": report_summary_path,
        "kill_conditions": kill_conditions,
    }

    # 10. Format as markdown
    ctx["formatted_context"] = format_options_context(ctx)

    return ctx


def _get_deep_analysis(symbol: str, store) -> Dict[str, Any]:
    """Extract deep analysis data for options context."""
    analysis = store.get_latest_analysis(symbol)
    if not analysis:
        return {}

    return {
        "executive_summary": analysis.get("executive_summary"),
        "debate_verdict": analysis.get("debate_verdict"),
        "key_forces": analysis.get("key_forces"),
        "price_at_analysis": analysis.get("price_at_analysis"),
        "report_path": analysis.get("report_path"),
        "html_report_path": analysis.get("html_report_path"),
        "analysis_date": analysis.get("analysis_date"),
        "cycle_position": analysis.get("cycle_position"),
    }


def _get_oprms(symbol: str, store) -> Dict[str, Any]:
    """Extract current OPRMS for options context."""
    oprms = store.get_current_oprms(symbol)
    if not oprms:
        return {}

    return {
        "dna": oprms.get("dna"),
        "timing": oprms.get("timing"),
        "timing_coeff": oprms.get("timing_coeff"),
        "position_pct": oprms.get("position_pct"),
        "investment_bucket": oprms.get("investment_bucket"),
        "verdict": oprms.get("verdict"),
    }


def _find_report_summary(symbol: str) -> Optional[str]:
    """Find the report_summary.md path for a symbol.

    Looks in reports/{symbol}/ for the latest report summary.
    """
    reports_dir = _PROJECT_ROOT / "reports" / symbol.upper()
    if not reports_dir.exists():
        return None

    # Look for report_summary.md
    summary = reports_dir / "report_summary.md"
    if summary.exists():
        return str(summary)

    # Fallback: look for any *summary*.md
    summaries = list(reports_dir.glob("*summary*.md"))
    if summaries:
        return str(sorted(summaries)[-1])

    return None
