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
    price_days: int = 120,
) -> Dict[str, Any]:
    """
    Setup phase for deep analysis — prepares all data and prompts.

    Returns a slim dict consumed by the /deep-analysis skill. All large
    prompt strings are written to research_dir/prompts/ and only file
    paths are returned (~4KB instead of ~33KB).

    This function does NOT run any LLM analysis. It only:
    1. Collects data (FMP + FRED + indicators)
    2. Writes data_context.md to research dir
    3. Prepares research queries for web search agents
    4. Writes all agent prompts to files (lens, gemini, synthesis, alpha)
    """
    from terminal.deep_pipeline import (
        get_research_dir,
        write_data_context,
        prepare_research_queries,
        build_profiler_prompt,
        build_lens_agent_prompt,
        build_synthesis_agent_prompt,
        build_alpha_agent_prompt,
        build_alpha_debate_prompt,
        write_agent_prompts,
    )

    symbol = symbol.upper()
    result: Dict[str, Any] = {"symbol": symbol}

    # 1. Collect data
    scratchpad = AnalysisScratchpad(symbol, "deep")
    data_pkg = collect_data(symbol, price_days=price_days, scratchpad=scratchpad)

    # 2. Research directory (timestamped subdir per run)
    from datetime import datetime as _dt
    base_research_dir = get_research_dir(symbol)
    research_dir = base_research_dir / _dt.now().strftime("%Y%m%d_%H%M%S")
    research_dir.mkdir(parents=True, exist_ok=True)
    ctx_path = write_data_context(data_pkg, research_dir)
    result["research_dir"] = str(research_dir)
    result["data_context_path"] = str(ctx_path)

    # 3. Research queries
    info = data_pkg.info or {}
    result["research_queries"] = prepare_research_queries(
        symbol=symbol,
        company_name=info.get("companyName", symbol),
        sector=info.get("sector", ""),
        industry=info.get("industry", ""),
    )

    # 3b. Build profiler prompt
    profiler_prompt = build_profiler_prompt(research_dir, ctx_path)

    # 4. Build all prompts (in memory temporarily)
    lens_prompts = prepare_lens_prompts(symbol, data_pkg)
    from terminal.deep_pipeline import _slugify

    lens_agent_prompts = []
    for lp in lens_prompts:
        agent_prompt = build_lens_agent_prompt(lp, research_dir)
        slug = _slugify(lp["lens_name"])
        lens_agent_prompts.append({
            "lens_name": lp["lens_name"],
            "agent_prompt": agent_prompt,
            "output_path": str(research_dir / f"lens_{slug}.md"),
        })

    company_name = info.get("companyName", symbol)
    gemini_prompt = (
        f"You are a contrarian investment analyst. Given the following data about "
        f"{company_name} ({symbol}), provide a 500-word bearish counter-thesis. "
        f"Focus on risks the market is ignoring, historical analogs of similar "
        f"companies that failed, and structural weaknesses in the business model.\n\n"
        f"Key data:\n{data_pkg.format_context()[:3000]}"
    )

    synthesis_prompt = build_synthesis_agent_prompt(research_dir, symbol)

    record = data_pkg.company_record
    alpha_prompt = build_alpha_agent_prompt(
        research_dir=research_dir,
        symbol=symbol,
        sector=info.get("sector", ""),
        current_price=data_pkg.latest_price,
        l1_oprms=record.oprms if record and record.has_data else None,
    )

    # 4b. Retrieve past experiences for memory injection
    past_experiences = ""
    try:
        from terminal.memory import (
            retrieve_same_ticker_experiences,
            format_past_experiences,
        )
        same_ticker = retrieve_same_ticker_experiences(symbol, limit=3)
        if same_ticker:
            past_experiences = format_past_experiences(same_ticker)
            logger.info(
                "Retrieved %d past experiences for %s", len(same_ticker), symbol
            )
    except Exception as e:
        logger.warning("Memory retrieval failed (non-fatal): %s", e)

    # 4c. Build alpha debate prompt (Phase 4)
    alpha_debate_prompt = build_alpha_debate_prompt(
        research_dir=research_dir,
        symbol=symbol,
        past_experiences=past_experiences,
    )

    # 5. Write all prompts to disk, get back paths only
    prompt_paths = write_agent_prompts(
        research_dir=research_dir,
        lens_agent_prompts=lens_agent_prompts,
        gemini_prompt=gemini_prompt,
        synthesis_prompt=synthesis_prompt,
        alpha_prompt=alpha_prompt,
        alpha_debate_prompt=alpha_debate_prompt,
        profiler_prompt=profiler_prompt,
    )
    result.update(prompt_paths)

    # 6. Data summary for reference (small, kept in-memory)
    result["data"] = {
        "info": data_pkg.info,
        "latest_price": data_pkg.latest_price,
        "indicators": data_pkg.indicators,
        "has_financials": data_pkg.has_financials,
    }

    result["scratchpad_path"] = str(scratchpad.log_path)
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

    # Analysis freshness summary
    try:
        from terminal.freshness import check_all_freshness
        reports = check_all_freshness()
        if reports:
            red = [r for r in reports if r.level.value == "RED"]
            yellow = [r for r in reports if r.level.value == "YELLOW"]
            green = [r for r in reports if r.level.value == "GREEN"]
            result["analysis_freshness"] = {
                "red_count": len(red),
                "yellow_count": len(yellow),
                "green_count": len(green),
                "red_tickers": [
                    {"symbol": r.symbol, "reasons": r.reasons} for r in red
                ],
                "yellow_tickers": [
                    {"symbol": r.symbol, "reasons": r.reasons} for r in yellow
                ],
            }
    except Exception as e:
        result["freshness_error"] = str(e)

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
            f"Run `analyze_ticker('{symbol}')` first."
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


def freshness_check(symbol: str = None) -> Dict[str, Any]:
    """
    Check analysis freshness for one ticker or all rated tickers.

    Returns GREEN/YELLOW/RED status with reasons.
    """
    from terminal.freshness import check_freshness, check_all_freshness

    if symbol:
        report = check_freshness(symbol.upper())
        return report.to_dict()
    else:
        reports = check_all_freshness()
        summary = {
            "RED": sum(1 for r in reports if r.level.value == "RED"),
            "YELLOW": sum(1 for r in reports if r.level.value == "YELLOW"),
            "GREEN": sum(1 for r in reports if r.level.value == "GREEN"),
        }
        return {
            "total": len(reports),
            "summary": summary,
            "reports": [r.to_dict() for r in reports],
        }


def refresh_timing(symbol: str) -> Dict[str, Any]:
    """
    Prepare a lightweight timing refresh prompt (keeps DNA, re-evaluates Timing).

    Returns the prompt for Claude to run, or error if no OPRMS exists.
    """
    from terminal.freshness import prepare_timing_refresh_prompt

    result = prepare_timing_refresh_prompt(symbol.upper())
    if result is None:
        return {
            "error": f"No OPRMS rating found for {symbol}. "
            f"Run a full analysis first."
        }
    return result


def evolution_view(symbol: str) -> Dict[str, Any]:
    """
    View the OPRMS evolution timeline for a ticker.

    Returns structured timeline + formatted markdown text.
    """
    from terminal.freshness import get_evolution_timeline, format_evolution_text

    timeline = get_evolution_timeline(symbol.upper())
    timeline["formatted"] = format_evolution_text(timeline)
    return timeline


def dashboard() -> Dict[str, Any]:
    """
    Generate the Company Database HTML dashboard.

    Returns dict with path to generated file and stats.
    """
    from terminal.dashboard import generate_dashboard
    from terminal.company_store import get_store

    path = generate_dashboard()
    stats = get_store().get_stats()

    return {
        "dashboard_path": str(path),
        "stats": stats,
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
