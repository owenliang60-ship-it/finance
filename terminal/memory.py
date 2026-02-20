"""
Agent Memory — cross-session experience retrieval and storage.

Extracts structured situation summaries from research files and stores
them in SQLite (via company_store) for retrieval in future analyses.

Memory flow:
  compile_deep_report() → extract_situation_summary() → store_situation()
  deep_analyze_ticker()  → retrieve_same_ticker_experiences() → format_past_experiences()
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def extract_situation_summary(
    symbol: str,
    research_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Extract a structured situation summary from research files.

    Reads oprms.md, memo.md, alpha_red_team.md, alpha_cycle.md,
    alpha_bet.md, and alpha_debate.md to build a memory snapshot.

    Args:
        symbol: Stock ticker
        research_dir: Path containing analysis files

    Returns:
        Dict with structured situation, or None if insufficient data
    """
    symbol = symbol.upper()
    summary = {"symbol": symbol}

    def _read(filename):
        p = research_dir / filename
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    oprms = _read("oprms.md")
    memo = _read("memo.md")
    red_team = _read("alpha_red_team.md")
    cycle = _read("alpha_cycle.md")
    bet = _read("alpha_bet.md")
    debate = _read("alpha_debate.md")
    ctx = _read("data_context.md")

    if not oprms and not memo:
        return None

    # Regime from data_context
    try:
        m_regime = re.search(r"\*\*Regime:\s*(\w+)\*\*", ctx)
        summary["regime"] = m_regime.group(1).upper() if m_regime else None
    except (AttributeError, IndexError):
        summary["regime"] = None

    # Price from data_context
    try:
        m_price = re.search(r"Latest:\s*\$?([\d,.]+)", ctx)
        summary["price"] = float(m_price.group(1).replace(",", "")) if m_price else None
    except (ValueError, TypeError, AttributeError):
        summary["price"] = None

    # OPRMS snapshot — multiple format patterns
    try:
        m_dna = re.search(
            r"(?:DNA[)）]*|资产基因\s*\(DNA\)\**)[：:\s]*\**\s*([SABC])\b", oprms
        )
        m_timing = re.search(
            r"(?:Timing[)）]*|时机系数\s*\(Timing\)\**)[：:\s]*\**\s*([SABC])\b", oprms
        )
        m_coeff = re.search(r"系数[：:]\s*([\d.]+)", oprms)
        summary["oprms_snapshot"] = {
            "dna": m_dna.group(1) if m_dna else None,
            "timing": m_timing.group(1) if m_timing else None,
            "coeff": float(m_coeff.group(1)) if m_coeff else None,
        }
    except (ValueError, AttributeError):
        summary["oprms_snapshot"] = {"dna": None, "timing": None, "coeff": None}

    # Thesis summary from memo (first 200 chars of executive summary)
    try:
        m_summary = re.search(
            r"(?:执行摘要|Executive Summary)\s*[)）]?\s*\n+(.+?)(?=\n##|\n---|\Z)",
            memo, re.DOTALL,
        )
        if m_summary:
            text = m_summary.group(1).strip()
            summary["thesis_summary"] = text[:200]
        else:
            summary["thesis_summary"] = None
    except (AttributeError, IndexError):
        summary["thesis_summary"] = None

    # Key risks from red team (top 2-3 lines)
    try:
        if red_team:
            risk_lines = []
            for line in red_team.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and len(risk_lines) < 3:
                    if any(kw in stripped.lower() for kw in
                           ["attack", "risk", "失效", "威胁", "脆弱", "danger"]):
                        risk_lines.append(stripped[:100])
            if not risk_lines:
                # Fallback: first 3 non-empty non-heading lines
                for line in red_team.split("\n"):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        risk_lines.append(stripped[:100])
                        if len(risk_lines) >= 3:
                            break
            summary["key_risks"] = risk_lines
    except Exception:
        summary["key_risks"] = []

    # Cycle position from alpha_cycle
    try:
        if cycle:
            m_score = re.search(r"(\d+)\s*/\s*10", cycle)
            m_direction = re.search(r"(toward_\w+|趋向\w+)", cycle)
            summary["cycle_position"] = {
                "score": int(m_score.group(1)) if m_score else None,
                "direction": m_direction.group(1) if m_direction else None,
            }
    except (ValueError, AttributeError):
        summary["cycle_position"] = {"score": None, "direction": None}

    # Action + conviction from alpha_bet or alpha_debate
    # Debate overrides bet (it's the final verdict)
    try:
        source = debate if debate else bet
        if source:
            m_cm = re.search(r"conviction_modifier[：:]\s*([\d.]+)", source)
            m_action = re.search(
                r"(?:final_action|最终行动|行动建议)[：:]\s*(执行|搁置|放弃)",
                source,
            )
            summary["action"] = m_action.group(1) if m_action else None
            summary["conviction_modifier"] = float(m_cm.group(1)) if m_cm else None
    except (ValueError, TypeError, AttributeError):
        summary.setdefault("action", None)
        summary.setdefault("conviction_modifier", None)

    # Debate-specific fields
    try:
        if debate:
            m_disagree = re.search(
                r"(?:核心分歧|key_disagreement)[：:]\s*(.+?)(?:\n|$)",
                debate,
            )
            summary["debate_key_disagreement"] = (
                m_disagree.group(1).strip()[:200] if m_disagree else None
            )
    except (AttributeError, IndexError):
        summary["debate_key_disagreement"] = None

    return summary


def store_situation(
    symbol: str,
    situation: Dict[str, Any],
    store: Optional[Any] = None,
) -> None:
    """Write situation summary into SQLite (latest analysis row).

    Args:
        symbol: Stock ticker
        situation: Dict from extract_situation_summary()
        store: Optional CompanyStore instance (uses singleton if None)
    """
    if store is None:
        from terminal.company_store import get_store
        store = get_store()

    symbol = symbol.upper()
    situation_json = json.dumps(situation, ensure_ascii=False)
    store.update_situation_summary(symbol, situation_json)
    logger.info("Stored situation memory for %s (%d chars)", symbol, len(situation_json))


def retrieve_same_ticker_experiences(
    symbol: str,
    limit: int = 3,
    store: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Query past analyses with situation summaries for the same ticker.

    Args:
        symbol: Stock ticker
        limit: Maximum results
        store: Optional CompanyStore instance

    Returns:
        List of analysis dicts with parsed situation_summary
    """
    if store is None:
        from terminal.company_store import get_store
        store = get_store()

    rows = store.get_analyses_with_memory(symbol, limit=limit)
    results = []
    for row in rows:
        situation_raw = row.get("situation_summary")
        if situation_raw:
            try:
                row["situation_parsed"] = json.loads(situation_raw)
            except (json.JSONDecodeError, TypeError):
                row["situation_parsed"] = None
        results.append(row)
    return results


def format_past_experiences(
    same_ticker: List[Dict[str, Any]],
    cross_ticker: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Format past experiences into a prompt injection block.

    Args:
        same_ticker: Results from retrieve_same_ticker_experiences()
        cross_ticker: Optional cross-ticker experiences (future)

    Returns:
        Formatted markdown string for prompt injection
    """
    if not same_ticker:
        return ""

    lines = ["## 历史经验回顾 (Agent Memory)", ""]

    for i, analysis in enumerate(same_ticker, 1):
        situation = analysis.get("situation_parsed") or {}
        date = analysis.get("analysis_date", "N/A")
        price = situation.get("price", "N/A")
        regime = situation.get("regime", "N/A")
        oprms = situation.get("oprms_snapshot", {})
        thesis = situation.get("thesis_summary", "N/A")
        action = situation.get("action", "N/A")
        cm = situation.get("conviction_modifier", "N/A")
        disagreement = situation.get("debate_key_disagreement", "")

        lines.append("### 历史分析 #%d (%s)" % (i, date))
        lines.append("- **价格**: $%s | **Regime**: %s" % (price, regime))
        if oprms:
            lines.append(
                "- **OPRMS**: DNA=%s, Timing=%s, Coeff=%s"
                % (oprms.get("dna", "?"), oprms.get("timing", "?"),
                   oprms.get("coeff", "?"))
            )
        lines.append("- **论文摘要**: %s" % thesis)
        lines.append("- **行动**: %s (conviction: %s)" % (action, cm))

        risks = situation.get("key_risks", [])
        if risks:
            lines.append("- **当时关键风险**: %s" % "; ".join(risks[:3]))

        cycle = situation.get("cycle_position", {})
        if cycle and cycle.get("score"):
            lines.append(
                "- **周期位置**: 钟摆 %s/10 %s"
                % (cycle["score"], cycle.get("direction", ""))
            )

        if disagreement:
            lines.append("- **核心分歧**: %s" % disagreement)

        lines.append("")

    lines.append("---")
    lines.append("**指示**: 参考上述历史经验，注意哪些判断被验证、哪些被推翻。")
    lines.append("")

    return "\n".join(lines)
