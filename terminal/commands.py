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
    prepare_alpha_prompts,
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
    - "standard": + 5 lens prompts for Claude to run (macro data injected into each lens)
    - "full":     + debate + memo + scoring + Layer 2 (red team + cycle + bet) + HB sync

    Returns a dict with data and (for standard/full/alpha) prompt sequences.
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

        # Phase 2: Lens prompts (standard, full, alpha)
        if depth in ("standard", "full", "alpha"):
            scratchpad.log_reasoning(
                "phase_2_start",
                f"Preparing lens analysis prompts for {depth} depth"
            )

            prompts = prepare_lens_prompts(symbol, data_pkg)
            result["lens_prompts"] = prompts
            result["lens_instructions"] = (
                "ANALYSIS SEQUENCE:\n"
                f"1. Run each of the {len(prompts)} lens analyses below in sequence.\n"
                "   Each lens prompt already includes macro data context.\n"
                "2. After all lenses, identify the 3 key tensions across perspectives."
            )

            scratchpad.log_reasoning(
                "phase_2_complete",
                f"Prepared {len(prompts)} lens prompts: {[p['lens_name'] for p in prompts]}"
            )

        # Phase 3+: Debate + memo (full and alpha)
        if depth in ("full", "alpha"):
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

        # Phase 6: Layer 2 — Second-Order Thinking (full depth)
        if depth in ("full", "alpha"):
            scratchpad.log_reasoning(
                "phase_6_start",
                "Alpha depth: enabling Layer 2 second-order thinking"
            )

            alpha_prompts = prepare_alpha_prompts(
                symbol=symbol,
                data_package=data_pkg,
                l1_memo_summary="[Claude fills from L1 memo executive summary]",
                l1_verdict="[Claude fills from L1 debate verdict: BUY/HOLD/SELL]",
                l1_key_forces="[Claude fills from L1 debate: 3 key forces]",
                l1_oprms=record.oprms if record and record.has_data else None,
            )
            result["alpha_prompts"] = alpha_prompts
            result["alpha_instructions"] = (
                "LAYER 2 — SECOND-ORDER THINKING\n\n"
                "After completing ALL Layer 1 stages (lenses → debate → memo → OPRMS),\n"
                "run these 3 prompts SEQUENTIALLY:\n\n"
                "1. Fill in l1_memo_summary, l1_verdict, l1_key_forces from your L1 output\n"
                "2. Run Prompt A (Red Team) — it's fully rendered in alpha_prompts[0]['prompt']\n"
                "3. For Prompt B (Cycle), call:\n"
                "   from knowledge.alpha.cycle_pendulum import generate_cycle_prompt\n"
                "   prompt = generate_cycle_prompt(**alpha_prompts[1]['prompt_args'], red_team_summary=YOUR_A_OUTPUT)\n"
                "4. For Prompt C (Bet), call:\n"
                "   from knowledge.alpha.asymmetric_bet import generate_bet_prompt\n"
                "   prompt = generate_bet_prompt(**alpha_prompts[2]['prompt_args'], red_team_summary=YOUR_A_OUTPUT, cycle_summary=YOUR_B_OUTPUT)\n"
                "5. After all 3 phases, save with terminal.company_db.save_alpha_package()\n"
            )

            scratchpad.log_reasoning(
                "phase_6_complete",
                f"Prepared {len(alpha_prompts)} alpha prompts"
            )

        # Heptabase sync hint (full and alpha depth)
        if depth in ("full", "alpha"):
            result["heptabase_sync_ready"] = True
            result["heptabase_sync_instructions"] = (
                "HEPTABASE SYNC:\n"
                "分析完成后，执行以下步骤同步到 Heptabase：\n"
                f"1. 调用 terminal.heptabase_sync.prepare_heptabase_sync('{symbol}')\n"
                "2. 用返回的 card_content 调用 mcp__heptabase__save_to_note_card\n"
                "3. 用返回的 journal_entry 调用 mcp__heptabase__append_to_journal\n"
                "4. 提醒用户将卡片从 main space 拖到「未来资本」白板"
            )

    except Exception as e:
        # Log error before re-raising
        scratchpad.log_reasoning("error", f"Analysis failed: {str(e)}")
        logger.error(f"analyze_ticker failed for {symbol}: {e}")
        raise

    return result


def deep_analyze_ticker(
    symbol: str,
    price_days: int = 120,
) -> Dict[str, Any]:
    """
    Setup phase for deep analysis — prepares all data and prompts.

    Returns a dict consumed by the /deep-analysis skill, which handles
    agent dispatch and LLM synthesis. All intermediate files go to
    data/companies/{SYMBOL}/research/.

    This function does NOT run any LLM analysis. It only:
    1. Collects data (FMP + FRED + indicators)
    2. Writes data_context.md to research dir
    3. Prepares research queries for web search agents
    4. Prepares lens agent prompts (with file read/write instructions)
    5. Prepares Gemini contrarian prompt
    """
    from terminal.deep_pipeline import (
        get_research_dir,
        write_data_context,
        prepare_research_queries,
        build_lens_agent_prompt,
    )

    symbol = symbol.upper()
    result: Dict[str, Any] = {"symbol": symbol}

    # 1. Collect data
    scratchpad = AnalysisScratchpad(symbol, "deep")
    data_pkg = collect_data(symbol, price_days=price_days, scratchpad=scratchpad)

    # 2. Research directory + data context
    research_dir = get_research_dir(symbol)
    ctx_path = write_data_context(data_pkg, research_dir)
    result["research_dir"] = str(research_dir)
    result["data_context_path"] = str(ctx_path)
    result["context_summary"] = data_pkg.format_context()

    # 3. Research queries
    info = data_pkg.info or {}
    result["research_queries"] = prepare_research_queries(
        symbol=symbol,
        company_name=info.get("companyName", symbol),
        sector=info.get("sector", ""),
        industry=info.get("industry", ""),
    )

    # 4. Lens agent prompts
    lens_prompts = prepare_lens_prompts(symbol, data_pkg)
    result["lens_agent_prompts"] = []
    for lp in lens_prompts:
        agent_prompt = build_lens_agent_prompt(lp, research_dir)
        slug = lp["lens_name"].lower().replace("/", "_").replace(" ", "_")
        slug = "".join(c for c in slug if c.isalnum() or c == "_")
        result["lens_agent_prompts"].append({
            "lens_name": lp["lens_name"],
            "agent_prompt": agent_prompt,
            "output_path": str(research_dir / f"lens_{slug}.md"),
        })

    # 5. Gemini contrarian prompt
    company_name = info.get("companyName", symbol)
    result["gemini_prompt"] = (
        f"You are a contrarian investment analyst. Given the following data about "
        f"{company_name} ({symbol}), provide a 500-word bearish counter-thesis. "
        f"Focus on risks the market is ignoring, historical analogs of similar "
        f"companies that failed, and structural weaknesses in the business model.\n\n"
        f"Key data:\n{data_pkg.format_context()[:3000]}"
    )

    # 6. Data summary for reference
    result["data"] = {
        "info": data_pkg.info,
        "latest_price": data_pkg.latest_price,
        "indicators": data_pkg.indicators,
        "has_financials": data_pkg.has_financials,
    }

    # 7. Alpha prompt args (for Phase 4 in skill)
    record = data_pkg.company_record
    result["alpha_prompt_args"] = {
        "symbol": symbol,
        "sector": info.get("sector", ""),
        "data_context_path": str(ctx_path),
        "l1_oprms": record.oprms if record and record.has_data else None,
        "current_price": data_pkg.latest_price,
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
