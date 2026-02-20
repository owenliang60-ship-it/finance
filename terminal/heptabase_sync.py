"""
Heptabase sync — format analysis results for Heptabase cards and journal.

MCP tools (save_to_note_card, append_to_journal) are Claude's tools,
not Python APIs. This module prepares the content; Claude calls MCP.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from terminal.company_db import (
    get_oprms,
    get_kill_conditions,
    get_all_memos,
    get_latest_alpha,
    get_meta,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent


def prepare_heptabase_sync(symbol: str) -> dict:
    """
    Read saved analysis results and format for Heptabase.

    Returns:
        {
            "card_content": str,     # Full markdown, h1 = card title
            "journal_entry": str,    # Brief journal entry
            "symbol": str,
            "has_data": bool,        # False if nothing to sync
        }
    """
    symbol = symbol.upper()

    oprms = get_oprms(symbol)
    if not oprms:
        return {
            "card_content": "",
            "journal_entry": "",
            "symbol": symbol,
            "has_data": False,
        }

    meta = get_meta(symbol)
    kill_conditions = get_kill_conditions(symbol)
    memos = get_all_memos(symbol)
    alpha = get_latest_alpha(symbol)

    company_name = meta.get("company_name", symbol)
    analysis_date = oprms.get("updated_at", datetime.now().isoformat())[:10]

    # Read latest memo content if available
    memo_content = ""
    if memos:
        memo_path = Path(memos[0]["path"])
        if memo_path.exists():
            memo_content = memo_path.read_text(encoding="utf-8")

    card = _format_card(
        symbol=symbol,
        company_name=company_name,
        oprms=oprms,
        kill_conditions=kill_conditions,
        memo_content=memo_content,
        alpha=alpha,
        meta=meta,
        analysis_date=analysis_date,
    )

    journal = _format_journal(
        symbol=symbol,
        company_name=company_name,
        oprms=oprms,
    )

    return {
        "card_content": card,
        "journal_entry": journal,
        "symbol": symbol,
        "has_data": True,
    }


def _format_card(
    symbol: str,
    company_name: str,
    oprms: dict,
    kill_conditions: list,
    memo_content: str,
    alpha: Optional[dict],
    meta: dict,
    analysis_date: str,
) -> str:
    """Format a Heptabase note card in markdown."""
    dna = oprms.get("dna", "?")
    dna_label = oprms.get("dna_label", "")
    timing = oprms.get("timing", "?")
    timing_label = oprms.get("timing_label", "")
    position_pct = oprms.get("position_pct", 0)
    verdict = oprms.get("verdict", "N/A")
    verdict_rationale = oprms.get("verdict_rationale", "")
    depth = oprms.get("analysis_depth", "standard")

    lines = []
    # H1 title (Heptabase uses first h1 as card title)
    lines.append(f"# {symbol} ({company_name}) — {analysis_date}")
    lines.append("")
    lines.append(
        f"> **OPRMS**: DNA={dna} ({dna_label}) | "
        f"Timing={timing} ({timing_label}) | "
        f"Position={position_pct}%"
    )

    # Add conviction modifier if alpha exists
    conviction = ""
    if alpha and alpha.get("asymmetric_bet", {}).get("conviction_modifier"):
        conviction = f" | Conviction: {alpha['asymmetric_bet']['conviction_modifier']}"
    lines.append(f"> **Verdict**: {verdict}{conviction} | Depth: {depth}")
    lines.append("")

    # Verdict rationale
    if verdict_rationale:
        lines.append(f"**Rationale**: {verdict_rationale}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # Memo content
    if memo_content:
        lines.append(memo_content.strip())
        lines.append("")
        lines.append("---")
        lines.append("")

    # Alpha insights (if available)
    if alpha:
        lines.append("## Layer 2 — Second-Order Thinking")
        lines.append("")

        red_team = alpha.get("red_team", {})
        if red_team:
            lines.append("### Red Team")
            for attack in red_team.get("attacks", []):
                lines.append(
                    f"- **{attack['name']}** (Lethality {attack.get('lethality', '?')}/10): "
                    f"{attack.get('summary', '')}"
                )
            if red_team.get("survival"):
                lines.append(f"- **Survival**: {red_team['survival']}")
            lines.append("")

        cycle = alpha.get("cycle_pendulum", {})
        if cycle:
            lines.append("### Cycle Pendulum")
            for dim in ["sentiment", "business", "technology"]:
                d = cycle.get(dim, {})
                if d:
                    lines.append(
                        f"- **{dim.title()}**: {d.get('label', '?')} "
                        f"({d.get('score', '?')}/10) — {d.get('evidence', '')}"
                    )
            if cycle.get("conclusion"):
                lines.append(f"- **Conclusion**: {cycle['conclusion']}")
            lines.append("")

        bet = alpha.get("asymmetric_bet", {})
        if bet:
            lines.append("### Asymmetric Bet")
            for s in bet.get("scenarios", []):
                lines.append(
                    f"- **{s.get('name', '?')}** (P={s.get('probability', '?')}): "
                    f"{s.get('target', '')} → {s.get('return_range', '')}"
                )
            if bet.get("expected_value") is not None:
                lines.append(f"- **EV**: {bet['expected_value']:+.1%}")
            if bet.get("asymmetry"):
                lines.append(f"- **Asymmetry**: {bet['asymmetry']}")
            lines.append("")

        # Alpha Debate conclusion (if available) — flat keys from AlphaPackage
        has_debate = (
            alpha.get("debate_conviction_modifier") is not None
            or alpha.get("debate_final_action")
        )
        if has_debate:
            lines.append("### Alpha Debate (Final Verdict)")
            if alpha.get("debate_key_disagreement"):
                lines.append(f"- **Core Disagreement**: {alpha['debate_key_disagreement']}")
            if alpha.get("debate_conviction_modifier") is not None:
                lines.append(f"- **Conviction Modifier**: {alpha['debate_conviction_modifier']}")
            if alpha.get("debate_final_action"):
                lines.append(f"- **Action**: {alpha['debate_final_action']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Kill conditions
    if kill_conditions:
        lines.append("## Kill Conditions")
        lines.append("")
        for i, kc in enumerate(kill_conditions, 1):
            lines.append(f"{i}. {kc.get('description', 'N/A')}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Concept links
    links = ["[[OPRMS]]"]
    sector = meta.get("sector", "")
    if sector:
        links.append(f"[[{sector}]]")
    industry = meta.get("industry", "")
    if industry:
        links.append(f"[[{industry}]]")
    for theme in meta.get("themes", []):
        links.append(f"[[{theme}]]")

    lines.append("## Related Concepts")
    lines.append("")
    lines.append(" · ".join(links))

    return "\n".join(lines)


def _format_journal(
    symbol: str,
    company_name: str,
    oprms: dict,
) -> str:
    """Format a brief Heptabase journal entry."""
    dna = oprms.get("dna", "?")
    timing = oprms.get("timing", "?")
    position_pct = oprms.get("position_pct", 0)
    verdict = oprms.get("verdict", "N/A")
    verdict_rationale = oprms.get("verdict_rationale", "")
    depth = oprms.get("analysis_depth", "standard")

    # Truncate rationale to keep it brief
    rationale_short = verdict_rationale[:100]
    if len(verdict_rationale) > 100:
        rationale_short += "..."

    lines = [
        f"## {symbol} ({company_name}) 分析完成 ({depth})",
        f"- **OPRMS**: {dna}/{timing} → {position_pct}%",
        f"- **Verdict**: {verdict} — {rationale_short}",
    ]

    return "\n".join(lines)
