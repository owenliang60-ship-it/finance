"""
Top-level commands — Claude calls these directly in conversation.

Each command returns a structured dict that Claude formats for the user.
"""
import logging
from typing import Any, Dict, Optional

from terminal.company_db import (
    get_company_record,
    list_all_companies,
    get_oprms,
    get_kill_conditions,
    get_all_memos,
    get_analyses,
    get_meta,
)
from terminal.pipeline import (
    collect_data,
    prepare_lens_prompts,
    prepare_memo_skeleton,
    calculate_position,
    DataPackage,
)
from terminal.scratchpad import (
    AnalysisScratchpad,
    list_scratchpads,
    read_scratchpad,
)
from pathlib import Path

logger = logging.getLogger(__name__)


def analyze_ticker(
    symbol: str,
    depth: str = "quick",
    price_days: int = 60,
) -> Dict[str, Any]:
    """
    Entry point for ticker analysis.

    Depth levels:
    - "quick":    Data + indicators snapshot (~5 sec)
    - "standard": + 6 lens prompts for Claude to run
    - "full":     + debate prompts + memo skeleton + scoring rubric

    Returns a dict with data and (for standard/full) prompt sequences.
    Creates a scratchpad log for full analysis tracking.
    """
    symbol = symbol.upper()
    result: Dict[str, Any] = {"symbol": symbol, "depth": depth}

    # Create scratchpad for analysis tracking
    scratchpad = AnalysisScratchpad(symbol, depth)
    result["scratchpad_path"] = str(scratchpad.log_path)

    try:
        # Phase 1: Data collection (always)
        scratchpad.log_reasoning(
            "phase_1_start",
            f"Starting {depth} depth analysis for {symbol}"
        )

        data_pkg = collect_data(symbol, price_days=price_days, scratchpad=scratchpad)
        result["data"] = {
            "info": data_pkg.info,
            "profile": data_pkg.profile,
            "fundamentals": data_pkg.fundamentals,
            "latest_price": data_pkg.latest_price,
            "indicators": data_pkg.indicators,
            "has_financials": data_pkg.has_financials,
        }

        scratchpad.log_reasoning(
            "phase_1_complete",
            f"Data collection complete. Financials available: {data_pkg.has_financials}"
        )

        # Existing company record
        record = data_pkg.company_record
        if record and record.has_data:
            result["existing_record"] = {
                "oprms": record.oprms,
                "kill_conditions_count": len(record.kill_conditions),
                "memos_count": len(record.memos),
                "analyses_count": len(record.analyses),
            }
            scratchpad.log_reasoning(
                "company_record",
                f"Found existing company record: {len(record.memos)} memos, "
                f"{len(record.analyses)} analyses"
            )

        # Phase 1.5: Macro briefing prompt (if macro data available)
        if data_pkg.macro:
            try:
                from terminal.macro_briefing import generate_briefing_prompt, detect_signals
                signals = detect_signals(data_pkg.macro)
                active_signals = [s for s in signals if s.fired]
                if active_signals or depth in ("standard", "full"):
                    briefing_prompt = generate_briefing_prompt(data_pkg.macro, signals)
                    result["macro_briefing_prompt"] = briefing_prompt
                    result["macro_signals"] = [
                        {"name": s.name, "label": s.label, "strength": s.strength,
                         "evidence": s.evidence}
                        for s in active_signals
                    ]
                    result["macro_briefing_instructions"] = (
                        "IMPORTANT: Run this macro briefing FIRST, before the 6 lens analyses. "
                        "Your response will be injected as context for all subsequent lenses. "
                        "After you respond, store the narrative in data_pkg.macro_briefing."
                    )
                    scratchpad.log_reasoning(
                        "macro_briefing",
                        f"Generated briefing prompt with {len(active_signals)} active signals"
                    )
            except Exception as e:
                logger.warning(f"Macro briefing generation failed: {e}")

        # Phase 2: Lens prompts (standard and full)
        if depth in ("standard", "full"):
            scratchpad.log_reasoning(
                "phase_2_start",
                f"Preparing lens analysis prompts for {depth} depth"
            )

            prompts = prepare_lens_prompts(symbol, data_pkg)
            result["lens_prompts"] = prompts
            result["lens_instructions"] = (
                f"Run each of the {len(prompts)} lens analyses below in sequence. "
                f"After all lenses, identify the 3 key tensions across perspectives."
            )

            scratchpad.log_reasoning(
                "phase_2_complete",
                f"Prepared {len(prompts)} lens prompts: {[p['lens_name'] for p in prompts]}"
            )

        # Phase 3+: Debate + memo (full only)
        if depth == "full":
            scratchpad.log_reasoning(
                "phase_3_start",
                "Full depth: enabling debate + memo generation"
            )

            result["debate_instructions"] = (
                "After completing lens analyses and identifying 3 tensions, "
                "use terminal.pipeline.prepare_debate_prompts() with the tensions "
                "to generate 5-round debate prompts."
            )
            result["memo_skeleton"] = prepare_memo_skeleton(symbol)
            result["scoring_rubric"] = (
                "After writing the memo, use terminal.pipeline.score_memo() "
                "to check completeness and writing standards. Target score: > 7.0/10."
            )

            scratchpad.log_reasoning(
                "phase_3_complete",
                "Debate and memo templates prepared"
            )

        # Context summary for Claude
        result["context_summary"] = data_pkg.format_context()

    except Exception as e:
        # Log error before re-raising
        scratchpad.log_reasoning("error", f"Analysis failed: {str(e)}")
        logger.error(f"analyze_ticker failed for {symbol}: {e}")
        raise

    return result


def portfolio_status() -> Dict[str, Any]:
    """
    Comprehensive portfolio status check.

    Combines holdings, exposure alerts, and company DB records.
    """
    result: Dict[str, Any] = {}

    # Holdings
    try:
        from portfolio.holdings.manager import (
            load_holdings,
            refresh_prices,
            get_portfolio_summary,
        )
        positions = load_holdings()
        if positions:
            positions = refresh_prices(positions)
            result["summary"] = get_portfolio_summary(positions)

            # Run exposure alerts
            try:
                from portfolio.exposure.alerts import run_all_checks
                alerts = run_all_checks(positions)
                result["alerts"] = [a.to_dict() for a in alerts]
                result["alert_counts"] = {
                    "CRITICAL": sum(1 for a in alerts if a.level.value == "CRITICAL"),
                    "WARNING": sum(1 for a in alerts if a.level.value == "WARNING"),
                    "INFO": sum(1 for a in alerts if a.level.value == "INFO"),
                }
            except Exception as e:
                result["alerts_error"] = str(e)
        else:
            result["summary"] = {"total_positions": 0, "message": "No holdings found."}
    except Exception as e:
        result["error"] = f"Failed to load holdings: {e}"

    # Company DB coverage
    tracked = list_all_companies()
    result["company_db"] = {
        "tracked_tickers": len(tracked),
        "tickers": tracked,
    }

    return result


def position_advisor(
    symbol: str,
    total_capital: float = 1_000_000,
) -> Dict[str, Any]:
    """
    Position sizing advisor for a specific ticker.

    Checks OPRMS rating, IPS constraints, portfolio impact.
    """
    symbol = symbol.upper()
    result: Dict[str, Any] = {"symbol": symbol}

    # Current OPRMS
    oprms = get_oprms(symbol)
    if oprms:
        result["oprms"] = oprms
        sizing = calculate_position(
            symbol=symbol,
            dna=oprms["dna"],
            timing=oprms["timing"],
            timing_coeff=oprms.get("timing_coeff"),
            total_capital=total_capital,
            evidence_count=len(oprms.get("evidence", [])),
        )
        result["sizing"] = sizing
    else:
        result["oprms"] = None
        result["sizing_note"] = (
            f"No OPRMS rating found for {symbol}. "
            f"Run `analyze_ticker('{symbol}', depth='standard')` first."
        )

    # Kill conditions
    kc = get_kill_conditions(symbol)
    result["kill_conditions"] = kc
    if not kc:
        result["kill_warning"] = (
            f"No kill conditions defined for {symbol}. "
            f"Every position must have observable invalidation triggers."
        )

    # Current position (if held)
    try:
        from portfolio.holdings.manager import get_position
        pos = get_position(symbol)
        if pos:
            result["current_position"] = pos.to_dict()
        else:
            result["current_position"] = None
    except Exception:
        result["current_position"] = None

    return result


def company_lookup(symbol: str) -> Dict[str, Any]:
    """
    Everything we know about a company from the company DB.
    """
    symbol = symbol.upper()
    record = get_company_record(symbol)

    result: Dict[str, Any] = {
        "symbol": symbol,
        "has_data": record.has_data,
    }

    if not record.has_data:
        result["message"] = (
            f"No records found for {symbol} in company DB. "
            f"Run `analyze_ticker('{symbol}')` to start building the knowledge base."
        )
        return result

    result["oprms"] = record.oprms
    result["oprms_history_count"] = len(record.oprms_history)
    result["kill_conditions"] = record.kill_conditions
    result["memos"] = record.memos
    result["analyses"] = record.analyses
    result["meta"] = record.meta

    # Theme memberships
    themes = record.meta.get("themes", [])
    result["themes"] = themes

    return result


def run_monitor() -> Dict[str, Any]:
    """
    Run the full portfolio monitoring sweep.

    Delegates to terminal.monitor.run_full_monitor().
    """
    from terminal.monitor import run_full_monitor
    return run_full_monitor()


def theme_status(slug: str) -> Dict[str, Any]:
    """
    Get the status of an investment theme.

    Delegates to terminal.themes.
    """
    from terminal.themes import get_theme
    theme = get_theme(slug)
    if theme is None:
        return {"error": f"Theme '{slug}' not found."}
    return theme


# ---------------------------------------------------------------------------
# Scratchpad viewer commands
# ---------------------------------------------------------------------------

def list_analysis_scratchpads(symbol: str, limit: int = 10) -> Dict[str, Any]:
    """
    List recent analysis scratchpads for a ticker.

    Args:
        symbol: Stock ticker
        limit: Maximum number of logs to return (default 10)

    Returns:
        Dict with list of scratchpad paths and metadata
    """
    symbol = symbol.upper()
    logs = list_scratchpads(symbol)

    if not logs:
        return {
            "symbol": symbol,
            "count": 0,
            "message": f"No analysis scratchpads found for {symbol}.",
        }

    # Limit results
    logs = logs[:limit]

    # Extract metadata from paths
    scratchpads = []
    for log_path in logs:
        # Parse filename: {timestamp}_{hash}.jsonl
        stem = log_path.stem  # e.g. "2026-02-08-143000_a1b2c3d4"
        parts = stem.split("_")
        timestamp = parts[0] if len(parts) > 0 else "unknown"

        # Get first event (query)
        events = read_scratchpad(log_path)
        query_event = next((e for e in events if e["type"] == "query"), None)

        scratchpads.append({
            "path": str(log_path),
            "filename": log_path.name,
            "timestamp": timestamp,
            "depth": query_event.get("depth") if query_event else None,
            "query": query_event.get("query") if query_event else None,
            "events_count": len(events),
        })

    return {
        "symbol": symbol,
        "count": len(scratchpads),
        "total_available": len(list_scratchpads(symbol)),
        "limit": limit,
        "scratchpads": scratchpads,
    }


def replay_analysis_scratchpad(log_path: str) -> Dict[str, Any]:
    """
    Replay an analysis scratchpad with stats and timeline.

    Args:
        log_path: Path to scratchpad JSONL file

    Returns:
        Dict with stats and timeline of events
    """
    path = Path(log_path)

    if not path.exists():
        return {"error": f"Scratchpad not found: {log_path}"}

    events = read_scratchpad(path)

    if not events:
        return {"error": f"No events found in scratchpad: {log_path}"}

    # Calculate stats
    stats = {
        "total_events": len(events),
        "tool_calls": sum(1 for e in events if e["type"] == "tool_call"),
        "reasoning_steps": sum(1 for e in events if e["type"] == "reasoning"),
        "lens_completed": sum(1 for e in events if e["type"] == "lens_complete"),
        "has_final_rating": any(e["type"] == "final_rating" for e in events),
    }

    # Build timeline
    timeline = []
    for event in events:
        timeline.append({
            "timestamp": event.get("timestamp"),
            "type": event["type"],
            "summary": _summarize_event(event),
        })

    # Extract query info
    query_event = next((e for e in events if e["type"] == "query"), None)
    query_info = {
        "symbol": query_event.get("symbol") if query_event else None,
        "depth": query_event.get("depth") if query_event else None,
        "query": query_event.get("query") if query_event else None,
    }

    # Extract final rating if exists
    rating_event = next((e for e in events if e["type"] == "final_rating"), None)
    final_rating = rating_event.get("oprms") if rating_event else None

    return {
        "log_path": log_path,
        "query": query_info,
        "stats": stats,
        "timeline": timeline,
        "final_rating": final_rating,
    }


def _summarize_event(event: Dict[str, Any]) -> str:
    """
    Generate a human-readable summary for a scratchpad event.

    Args:
        event: Scratchpad event dict

    Returns:
        Summary string
    """
    event_type = event["type"]

    if event_type == "query":
        return f"Query: {event.get('query', 'N/A')} (depth: {event.get('depth', 'N/A')})"

    elif event_type == "tool_call":
        tool = event.get("tool", "unknown")
        args = event.get("args", {})
        size = event.get("result_size", 0)
        args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
        return f"Tool: {tool}({args_str}) → {size} bytes"

    elif event_type == "reasoning":
        step = event.get("step", "unknown")
        content = event.get("content", "")
        preview = content[:80] + "..." if len(content) > 80 else content
        return f"Reasoning: {step} — {preview}"

    elif event_type == "lens_complete":
        lens = event.get("lens", "unknown")
        path = event.get("output_path", "no output")
        return f"Lens complete: {lens} → {path}"

    elif event_type == "final_rating":
        oprms = event.get("oprms", {})
        dna = oprms.get("dna", "?")
        timing = oprms.get("timing", "?")
        return f"Final rating: DNA={dna}, Timing={timing}"

    else:
        return f"Unknown event type: {event_type}"
