"""
Options Commands — Orchestrator for /options skill.

prepare_options_context(symbol) collects all data needed for the options
strategy discussion in one call.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from portfolio.holdings.sheet_book import load_sheet_book
from terminal.company_store import get_store as get_company_store
from src.data.market_store import get_store as get_market_store
from terminal.options.iv_tracker import get_iv_history_summary
from terminal.options.chain_analyzer import (
    fetch_and_store_chain,
    analyze_liquidity,
    get_term_structure,
    get_earnings_proximity,
    check_snapshot_freshness,
    compute_25d_skew,
)
from terminal.options.formatter import format_options_context

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent

OPRMS_STALE_DAYS = 60
EXPECTED_MOVE_DAYS = 30


def prepare_options_context(
    symbol: str,
    store=None,
    market_store=None,
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
    - Snapshot freshness, 25-delta skew, 30D 1-sigma expected move
    - Actual holdings + NAV (Whole Port 投资 Net Total) from the read-only Google Sheet; 期权腿未纳入
    - Gates summary: snapshot / liquidity / earnings / iv

    Args:
        symbol: Stock ticker
        store: CompanyStore instance for company-dimension data (defaults to singleton)
        market_store: MarketStore instance for IV/options data (defaults to singleton)
        client: MarketDataClient instance (defaults to singleton)
        fmp_client: FMPClient instance for earnings (defaults to singleton)
        skip_chain_fetch: Skip fetching new chain data (use existing snapshot)

    Returns:
        Complete context dict for the options skill
    """
    symbol = symbol.upper()

    if store is None:
        store = get_company_store()
    if market_store is None:
        market_store = get_market_store()

    # 1. Fetch and store chain data (unless skipped)
    chain_summary = None
    underlying_price = None
    if not skip_chain_fetch:
        chain_summary = fetch_and_store_chain(symbol, market_store, client=client)
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

    # 3. Deep analysis + OPRMS (from company_store)
    deep_analysis = _get_deep_analysis(symbol, store)
    oprms = _get_oprms(symbol, store)

    # 4. IV Summary (from market_store)
    iv_summary = get_iv_history_summary(symbol, market_store)

    # 5. Liquidity (from market_store)
    liquidity = analyze_liquidity(symbol, market_store)

    # 6. Term structure (from market_store)
    term_structure = get_term_structure(symbol, market_store)

    # 7. Earnings proximity
    earnings = get_earnings_proximity(symbol, fmp_client=fmp_client)

    # 8. Kill conditions (from company_store)
    kill_conditions = store.get_kill_conditions(symbol)

    # 9. Report summary path
    report_summary_path = _find_report_summary(symbol)

    # 10. Snapshot freshness + 25-delta skew + expected move (from market_store)
    snapshot_date = (chain_summary or {}).get("snapshot_date")
    if not snapshot_date:
        latest = market_store.get_options_snapshot(symbol)
        snapshot_date = latest[0].get("snapshot_date") if latest else None
    snapshot_freshness = check_snapshot_freshness(snapshot_date)
    skew_25d = compute_25d_skew(symbol, market_store)
    expected_move = _compute_expected_move(underlying_price, iv_summary, skew_25d)

    # 11. Actual holdings + NAV (read-only Google Sheet, Whole Port net total)
    portfolio = _get_portfolio_context(symbol, store, underlying_price)

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
        "snapshot_freshness": snapshot_freshness,
        "skew_25d": skew_25d,
        "expected_move": expected_move,
        "portfolio": portfolio,
        "gates": {
            "snapshot": snapshot_freshness["status"],
            "liquidity": (liquidity or {}).get("verdict"),
            "earnings": (earnings or {}).get("zone", "UNKNOWN"),
            "iv": (iv_summary or {}).get("iv_freshness", {}).get("status", "MISSING"),
        },
    }

    # 12. Format as markdown
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
    """Extract current OPRMS for options context.

    position_pct is the suggested cap at rating time, not an actual holding
    (issue 045). is_stale = rating older than OPRMS_STALE_DAYS, or undated.
    """
    oprms = store.get_current_oprms(symbol)
    if not oprms:
        return {}

    created_at = oprms.get("created_at")
    try:
        age_days = (date.today() - date.fromisoformat(str(created_at)[:10])).days
        is_stale = age_days > OPRMS_STALE_DAYS
    except ValueError:
        is_stale = True

    return {
        "dna": oprms.get("dna"),
        "timing": oprms.get("timing"),
        "timing_coeff": oprms.get("timing_coeff"),
        "position_pct": oprms.get("position_pct"),
        "investment_bucket": oprms.get("investment_bucket"),
        "verdict": oprms.get("verdict"),
        "created_at": created_at,
        "is_stale": is_stale,
    }


def _compute_expected_move(
    underlying_price: Optional[float],
    iv_summary: Optional[Dict[str, Any]],
    skew_25d: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """30D 1-sigma move = S × IV × sqrt(30/365). None if price or IV is missing."""
    iv = (iv_summary or {}).get("current_iv")
    iv_source = "iv_summary.current_iv"
    if not iv and skew_25d.get("available"):
        iv = skew_25d.get("atm_iv")
        iv_source = "skew_25d.atm_iv"
    if not underlying_price or not iv:
        return None

    move = underlying_price * iv * math.sqrt(EXPECTED_MOVE_DAYS / 365.0)
    return {
        "days": EXPECTED_MOVE_DAYS,
        "iv": iv,
        "iv_source": iv_source,
        "move_1sigma": round(move, 2),
        "move_pct": round(move / underlying_price * 100, 2),
        "lower": round(underlying_price - move, 2),
        "upper": round(underlying_price + move, 2),
    }


OPTION_LEGS_NOTE = "期权腿未纳入"


def _get_portfolio_context(symbol: str, store, underlying_price: Optional[float]) -> Dict[str, Any]:
    """Holdings + NAV from the read-only Google Sheet (book of record).

    estimated_nav = Whole Port 投资 Net Total. Option legs are not included.
    Sheet failure or a missing / non-positive net total fails closed: no
    holding, no NAV, no Max Loss budget (never falls back to company.db).
    `store` / `underlying_price` are unused, kept for call-site compatibility.
    """
    out: Dict[str, Any] = {
        "holding": None,
        "option_legs": [],
        "option_legs_note": OPTION_LEGS_NOTE,
        "estimated_nav": None,
        "nav_source": None,
        "max_loss_2pct": None,
        "max_loss_3pct": None,
        "portfolio_status": "UNKNOWN",
    }
    try:
        book = load_sheet_book()
    except Exception as exc:
        logger.warning("options portfolio context: Google Sheet unavailable (%s)",
                       type(exc).__name__)
        return out
    nav = getattr(book, "whole_port_net_total_usd", None)
    if nav is None or not math.isfinite(nav) or nav <= 0:
        logger.warning("options portfolio context: Whole Port net total unavailable")
        return out

    sym = symbol.upper()
    holding = next((h for h in book.holdings if h.symbol == sym), None)
    out.update({
        "holding": {
            "shares": holding.shares,
            "avg_cost": holding.cost_per_share,
            "open_date": None,
        } if holding else None,
        "estimated_nav": round(nav, 2),
        "nav_source": "whole_port_net_total",
        "max_loss_2pct": round(nav * 0.02, 2),
        "max_loss_3pct": round(nav * 0.03, 2),
        "portfolio_status": "OK",
    })
    return out


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
