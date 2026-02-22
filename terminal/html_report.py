"""
HTML report builder for deep analysis pipeline.

Converts structured markdown reports into self-contained HTML with
institutional-grade visual design. Each report section maps to a
specific HTML component (cards, regime boxes, tables, etc.).

Design reference: trading-memo.html (warm bright color scheme).
"""
import html
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSS Constants — warm bright theme adapted from trading-memo.html
# ---------------------------------------------------------------------------

CSS = """
:root {
  --bg: #fdfbf7;
  --surface: #ffffff;
  --surface2: #f5f0e8;
  --border: #e8e0d0;
  --text: #2d2a26;
  --text-dim: #7a7265;
  --text-muted: #b0a898;
  --gold: #d4a012;
  --gold-dim: #d4a01222;
  --red: #dc3545;
  --red-dim: #dc354515;
  --green: #198754;
  --green-dim: #19875415;
  --amber: #e8960c;
  --amber-dim: #e8960c15;
  --blue: #2563eb;
  --blue-dim: #2563eb12;
  --purple: #7c3aed;
  --purple-dim: #7c3aed12;
  --cyan: #0891b2;
  --cyan-dim: #0891b212;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  line-height: 1.7;
  overflow-x: hidden;
}

.layout {
  display: flex;
  max-width: 1200px;
  margin: 0 auto;
}

.toc {
  width: 220px;
  position: sticky;
  top: 24px;
  align-self: flex-start;
  padding: 24px 16px;
  margin-top: 40px;
  border-right: 1px solid var(--border);
  height: fit-content;
  flex-shrink: 0;
}

.toc-label {
  font-size: 9px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 16px;
}

.toc a {
  display: block;
  padding: 6px 0;
  font-size: 11px;
  color: var(--text-dim);
  text-decoration: none;
  border-left: 2px solid transparent;
  padding-left: 12px;
  transition: all 0.2s;
}

.toc a:hover {
  color: var(--gold);
  border-left-color: var(--gold);
}

.container {
  flex: 1;
  max-width: 900px;
  padding: 40px 32px;
  min-width: 0;
}

/* Header */
.header {
  border-bottom: 1px solid var(--border);
  padding-bottom: 32px;
  margin-bottom: 48px;
  position: relative;
}

.header::before {
  content: '';
  position: absolute;
  top: -40px;
  left: -100px;
  width: 400px;
  height: 400px;
  background: radial-gradient(circle, var(--gold-dim) 0%, transparent 70%);
  pointer-events: none;
  opacity: 0.5;
}

.header-badge {
  display: inline-block;
  font-size: 10px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--gold);
  border: 1px solid var(--gold);
  padding: 4px 12px;
  margin-bottom: 16px;
}

.header h1 {
  font-family: 'Crimson Pro', serif;
  font-size: 42px;
  font-weight: 300;
  letter-spacing: -1px;
  margin-bottom: 8px;
  color: var(--text);
}

.header h1 span { color: var(--gold); font-weight: 600; }

.header-meta {
  display: flex;
  gap: 24px;
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 12px;
  flex-wrap: wrap;
}

.header-meta .val { color: var(--text); font-weight: 600; }

/* Sections */
.section {
  margin-bottom: 56px;
}

.section-label {
  font-size: 10px;
  letter-spacing: 4px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.section-title {
  font-family: 'Crimson Pro', serif;
  font-size: 28px;
  font-weight: 300;
  color: var(--text);
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.section-title span { color: var(--gold); }

/* Regime Box */
.regime-box {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-left: 4px solid var(--amber);
  padding: 24px 28px;
  margin-bottom: 24px;
}

.regime-box.green { border-left-color: var(--green); }
.regime-box.red { border-left-color: var(--red); }
.regime-box.blue { border-left-color: var(--blue); }
.regime-box.purple { border-left-color: var(--purple); }
.regime-box.cyan { border-left-color: var(--cyan); }
.regime-box.gold { border-left-color: var(--gold); }

.regime-box h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--amber);
  margin-bottom: 12px;
  letter-spacing: 1px;
}

.regime-box.green h3 { color: var(--green); }
.regime-box.red h3 { color: var(--red); }
.regime-box.blue h3 { color: var(--blue); }
.regime-box.purple h3 { color: var(--purple); }
.regime-box.cyan h3 { color: var(--cyan); }
.regime-box.gold h3 { color: var(--gold); }

.regime-box .prose { margin-bottom: 0; }

/* Snap Cards (OPRMS grid) */
.snapshot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.snap-card {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  position: relative;
  overflow: hidden;
}

.snap-card .label {
  font-size: 10px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.snap-card .value {
  font-size: 28px;
  font-weight: 700;
  font-family: 'JetBrains Mono', monospace;
}

.snap-card .sub {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 4px;
}

.snap-card .bar {
  position: absolute;
  bottom: 0;
  left: 0;
  height: 3px;
}

/* Strategy Cards (Lens cards) */
.strat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 24px;
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  position: relative;
}

.strat-card .layer-badge {
  position: absolute;
  top: 0;
  right: 0;
  font-size: 9px;
  letter-spacing: 2px;
  text-transform: uppercase;
  padding: 6px 12px;
  font-weight: 700;
}

.strat-card h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--text);
}

.strat-card .strat-meta {
  font-size: 11px;
  color: var(--text-dim);
  margin-bottom: 16px;
  letter-spacing: 0.5px;
}

/* Tags */
.tag {
  display: inline-block;
  padding: 2px 8px;
  font-size: 10px;
  letter-spacing: 1px;
  border-radius: 2px;
  font-weight: 600;
}

.tag-buy { background: var(--green-dim); color: var(--green); }
.tag-hold { background: var(--amber-dim); color: var(--amber); }
.tag-pass { background: var(--red-dim); color: var(--red); }
.tag-pair { background: var(--cyan-dim); color: var(--cyan); }
.tag-blue { background: var(--blue-dim); color: var(--blue); }
.tag-purple { background: var(--purple-dim); color: var(--purple); }
.tag-cyan { background: var(--cyan-dim); color: var(--cyan); }
.tag-green { background: var(--green-dim); color: var(--green); }
.tag-amber { background: var(--amber-dim); color: var(--amber); }
.tag-red { background: var(--red-dim); color: var(--red); }
.tag-gold { background: var(--gold-dim); color: var(--gold); }

.star { color: var(--gold); }

/* Debate: tension rows */
.tension-block {
  background: var(--surface);
  border: 1px solid var(--border);
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.tension-header {
  padding: 16px 24px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  border-bottom: 1px solid var(--border);
  background: var(--surface2);
}

.tension-side {
  padding: 16px 24px;
  border-left: 3px solid transparent;
}

.tension-side.bull { border-left-color: var(--green); }
.tension-side.bear { border-left-color: var(--red); }
.tension-side.resolution { border-left-color: var(--cyan); background: var(--surface2); }

.tension-side .side-label {
  font-size: 10px;
  letter-spacing: 2px;
  text-transform: uppercase;
  font-weight: 700;
  margin-bottom: 6px;
}

.tension-side.bull .side-label { color: var(--green); }
.tension-side.bear .side-label { color: var(--red); }
.tension-side.resolution .side-label { color: var(--cyan); }

/* Risk Callout */
.risk-callout {
  background: var(--surface);
  border: 1px solid var(--red);
  border-left: 4px solid var(--red);
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.risk-callout.amber {
  border-color: var(--amber);
  border-left-color: var(--amber);
}

.risk-callout h3 {
  color: var(--red);
  font-size: 12px;
  letter-spacing: 2px;
  text-transform: uppercase;
  margin-bottom: 12px;
}

.risk-callout.amber h3 { color: var(--amber); }

/* Portfolio Table */
.portfolio-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 24px;
  font-size: 13px;
}

.portfolio-table th {
  font-size: 10px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-muted);
  text-align: left;
  padding: 12px 16px;
  border-bottom: 2px solid var(--border);
}

.portfolio-table td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.portfolio-table tr:nth-child(even) td { background: var(--surface2); }
.portfolio-table tr:hover td { background: var(--surface2); }

/* Prose */
.prose {
  font-family: 'Crimson Pro', serif;
  font-size: 16px;
  line-height: 1.9;
  color: var(--text);
  margin-bottom: 20px;
}

.prose strong { color: var(--text); font-weight: 700; }
.prose em { color: var(--gold); font-style: italic; }
.prose code {
  background: var(--surface2);
  padding: 1px 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  border-radius: 2px;
}

.prose ul, .prose ol {
  margin: 12px 0;
  padding-left: 24px;
}

.prose li { margin-bottom: 4px; }

.prose blockquote {
  border-left: 3px solid var(--gold);
  padding: 8px 16px;
  margin: 12px 0;
  color: var(--text-dim);
  background: var(--surface2);
}

.prose hr {
  border: none;
  border-top: 1px solid var(--border);
  margin: 24px 0;
}

.prose h2 {
  font-family: 'JetBrains Mono', monospace;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  margin: 24px 0 12px 0;
}

.prose h3 {
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  margin: 20px 0 8px 0;
}

/* Verdict box */
.verdict-box {
  background: var(--surface);
  border: 2px solid var(--gold);
  padding: 24px 28px;
  margin-bottom: 24px;
  text-align: center;
}

.verdict-box .verdict-text {
  font-family: 'Crimson Pro', serif;
  font-size: 24px;
  font-weight: 600;
  color: var(--gold);
  margin-bottom: 8px;
}

.verdict-box .verdict-sub {
  font-size: 12px;
  color: var(--text-dim);
}

/* Disclaimer */
.disclaimer {
  margin-top: 64px;
  padding-top: 24px;
  border-top: 1px solid var(--border);
  font-size: 10px;
  color: var(--text-muted);
  line-height: 1.8;
  letter-spacing: 0.5px;
}

/* Responsive */
@media (max-width: 900px) {
  .toc { display: none; }
  .container { padding: 24px 16px; }
  .snapshot-grid { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 600px) {
  .snapshot-grid { grid-template-columns: 1fr; }
  .header h1 { font-size: 28px; }
}

/* Print */
@media print {
  .toc { display: none; }
  body { font-size: 11px; }
  .section { page-break-inside: avoid; }
  .header::before { display: none; }
  .strat-card, .snap-card, .tension-block { box-shadow: none; }
}
"""

# ---------------------------------------------------------------------------
# Lens color mapping
# ---------------------------------------------------------------------------

LENS_COLORS = {
    "quality_compounder": "blue",
    "imaginative_growth": "purple",
    "fundamental_long_short": "cyan",
    "deep_value": "green",
    "event_driven": "amber",
}

LENS_LABELS = {
    "quality_compounder": "Quality Compounder",
    "imaginative_growth": "Imaginative Growth",
    "fundamental_long_short": "Fundamental L/S",
    "deep_value": "Deep Value",
    "event_driven": "Event-Driven",
}


# ---------------------------------------------------------------------------
# md_to_html() — lightweight line-level markdown to HTML
# ---------------------------------------------------------------------------

def md_to_html(text: str) -> str:
    """Convert a subset of markdown to HTML.

    Handles: bold, italic, inline code, tables, unordered/ordered lists,
    blockquotes, horizontal rules, headings (h2/h3), paragraphs.
    """
    if not text or not text.strip():
        return ""

    lines = text.split("\n")
    out = []  # type: List[str]
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Blank line — close nothing, just skip
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            out.append("<hr>")
            i += 1
            continue

        # Headings
        m_h2 = re.match(r"^##\s+(.+)$", stripped)
        if m_h2:
            out.append('<h2>' + _inline(m_h2.group(1)) + '</h2>')
            i += 1
            continue

        m_h3 = re.match(r"^###\s+(.+)$", stripped)
        if m_h3:
            out.append('<h3>' + _inline(m_h3.group(1)) + '</h3>')
            i += 1
            continue

        # H4+ headings — render as bold paragraph
        m_h4 = re.match(r"^#{4,}\s+(.+)$", stripped)
        if m_h4:
            out.append('<p><strong>' + _inline(m_h4.group(1)) + '</strong></p>')
            i += 1
            continue

        # Skip H1 (already handled by section builders)
        m_h1 = re.match(r"^#\s+", stripped)
        if m_h1:
            i += 1
            continue

        # Blockquote
        if stripped.startswith("> ") or stripped == ">":
            bq_lines = []
            while i < n and (lines[i].strip().startswith(">") or lines[i].strip() == ">"):
                bq_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            out.append("<blockquote>" + _inline(" ".join(bq_lines)) + "</blockquote>")
            continue

        # Table
        if "|" in stripped and stripped.startswith("|"):
            table_lines = []
            while i < n and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            out.append(_build_table(table_lines))
            continue

        # Unordered list (- or *)
        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]).strip())
                i += 1
            out.append("<ul>" + "".join(
                "<li>" + _inline(it) + "</li>" for it in items
            ) + "</ul>")
            continue

        # Ordered list
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]).strip())
                i += 1
            out.append("<ol>" + "".join(
                "<li>" + _inline(it) + "</li>" for it in items
            ) + "</ol>")
            continue

        # Paragraph — collect consecutive non-empty, non-special lines
        para_lines = []
        while i < n:
            cl = lines[i].strip()
            if not cl:
                i += 1
                break
            # Stop if next line looks like a block element
            if (cl.startswith("#") or cl.startswith(">") or
                    cl.startswith("|") or re.match(r"^[-*]\s+", cl) or
                    re.match(r"^\d+\.\s+", cl) or re.match(r"^-{3,}$", cl)):
                break
            para_lines.append(cl)
            i += 1
        if para_lines:
            out.append("<p>" + _inline(" ".join(para_lines)) + "</p>")

    return "\n".join(out)


def _inline(text: str) -> str:
    """Convert inline markdown: bold, italic, code."""
    t = html.escape(text)
    # Code (backtick) — must come first to protect content
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    # Bold
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    # Italic
    t = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", t)
    return t


def _build_table(lines: List[str]) -> str:
    """Convert markdown table lines to HTML table."""
    if len(lines) < 2:
        return ""

    def parse_row(line: str) -> List[str]:
        cells = line.strip().strip("|").split("|")
        return [c.strip() for c in cells]

    headers = parse_row(lines[0])
    # Skip separator line (line[1] is usually |---|---|)
    data_start = 1
    if len(lines) > 1 and re.match(r"^[\s|:-]+$", lines[1]):
        data_start = 2

    rows = [parse_row(l) for l in lines[data_start:]]

    parts = ['<table class="portfolio-table">']
    parts.append("<thead><tr>")
    for h in headers:
        parts.append("<th>" + _inline(h) + "</th>")
    parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>")
        for cell in row:
            parts.append("<td>" + _inline(cell) + "</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_meta_line(text: str) -> Optional[str]:
    """Extract blockquote meta line (> date | price | ...)."""
    for line in text.split("\n")[:10]:
        stripped = line.strip()
        if stripped.startswith("> "):
            return stripped[2:].strip()
    return None


def _extract_regime(text: str) -> Tuple[str, str]:
    """Extract regime from macro briefing. Returns (regime, color)."""
    m = re.search(r"\*\*Regime\*\*:\s*(\w+)", text)
    if m:
        regime = m.group(1).upper()
        colors = {
            "RISK_ON": "green",
            "RISK_OFF": "red",
            "NEUTRAL": "amber",
            "CRISIS": "red",
        }
        return regime, colors.get(regime, "amber")
    return "NEUTRAL", "amber"


def _extract_one_liner(text: str) -> Optional[str]:
    """Extract one-liner from macro briefing."""
    m = re.search(r"\*\*一句话\*\*:\s*(.+)", text)
    if m:
        return m.group(1).strip()
    return None


def _extract_rating_line(text: str) -> Optional[Dict[str, str]]:
    """Extract star/verdict/IRR from lens rating section.

    Handles multiple LLM output formats:
    1. Pipe-delimited: **星级：X / 5** | **判定：BUY** | **目标 IRR：X%**
    2. Star-emoji: **★★★★☆ (4/5) | BUY | IRR 14%**
    3. Table row: | 星级 | **★★★★☆ (4/5)** |
    """
    # Format 1: pipe-delimited bold labels (supports multi-word verdicts)
    m = re.search(
        r"\*\*星级[：:]\s*([\d.]+)\s*/\s*5\*\*"
        r"\s*\|\s*\*\*判定[：:]\s*([^*]+?)\*\*"
        r"(?:\s*\|\s*\*\*目标\s*IRR[：:]\s*([^*]+)\*\*)?",
        text,
    )
    if m:
        return {
            "stars": m.group(1),
            "verdict": m.group(2).strip(),
            "irr": m.group(3).strip() if m.group(3) else "",
        }
    # Format 2: star-emoji inline ★★★★☆ (4/5) — BUY or | BUY
    m = re.search(
        r"[★☆]{3,}\s*\(?([\d.]+)/5\)?"
        r"\s*[|—\-]\s*\*?\*?([A-Z][A-Z /()a-z]+?)\*?\*?"
        r"(?:\s*[|—\-]\s*(?:目标\s*)?IRR[：:]\s*([^*\n]+))?",
        text,
    )
    if m:
        return {
            "stars": m.group(1),
            "verdict": m.group(2).strip(),
            "irr": m.group(3).strip() if m.group(3) else "",
        }
    # Format 3: table row | 星级 | **★...X/5** |
    m = re.search(r"\|\s*星级\s*\|\s*\*\*[★☆]*([\d.]+)\s*/?\s*5\)?\*\*\s*\|", text)
    if m:
        v = re.search(r"\|\s*判定\s*\|\s*\*\*([^*|]+)\*\*\s*\|", text)
        irr_m = re.search(r"\|\s*(?:目标\s*)?IRR\s*\|\s*\*\*([^*|]+)\*\*\s*\|", text)
        return {
            "stars": m.group(1),
            "verdict": v.group(1).strip() if v else "",
            "irr": irr_m.group(1).strip() if irr_m else "",
        }
    return None


def _extract_oprms_dna(text: str) -> Dict[str, str]:
    """Extract DNA rating from OPRMS section.

    Handles formats:
    1. Current: **资产基因 (DNA)**: A — 猛将
    2. Legacy:  **评级: B — 黑马**
    """
    result = {"grade": "?", "name": "", "cap": "?%"}
    # Format 1 (current): **资产基因 (DNA)**: A — 猛将
    m = re.search(r"\*\*资产基因\s*\(DNA\)\*\*[：:]\s*([SABC])\s*[—-]\s*(.+?)(?:\n|$)", text)
    if m:
        result["grade"] = m.group(1)
        result["name"] = m.group(2).strip()
    # Format 2 (legacy): **评级: B — 黑马**
    if result["grade"] == "?":
        m = re.search(r"\*\*评级[：:]\s*([SABC])\s*[—-]\s*(.+?)\*\*", text)
        if m:
            result["grade"] = m.group(1)
            result["name"] = m.group(2).strip()
    # Cap: "- 仓位上限: 7%" or "DNA 仓位上限：7%"
    m2 = re.search(r"仓位上限[：:]\s*(\d+(?:-\d+)?%)", text)
    if m2:
        result["cap"] = m2.group(1)
    return result


def _extract_oprms_timing(text: str) -> Dict[str, str]:
    """Extract timing rating from OPRMS section.

    Handles formats:
    1. Current: **时机系数 (Timing)**: B — 正常波动  /  - 系数: 0.5
    2. Legacy:  ## 时机系数 section with **评级: B — ...** and **系数: 0.5**
    """
    result = {"grade": "?", "name": "", "coeff": "?"}
    # Find Timing section (if exists as H2)
    timing_section = ""
    m = re.search(r"## 时机系数.*?\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if m:
        timing_section = m.group(1)
    # Format 1 (current): **时机系数 (Timing)**: B — 正常波动
    m1 = re.search(
        r"\*\*时机系数\s*\(Timing\)\*\*[：:]\s*([SABC])\s*[—-]\s*(.+?)(?:\n|$)",
        text,
    )
    if m1:
        result["grade"] = m1.group(1)
        result["name"] = m1.group(2).strip()
    # Format 2 (legacy): **评级: B — 正常波动** inside timing section
    if result["grade"] == "?" and timing_section:
        m2 = re.search(r"\*\*评级[：:]\s*([SABC])\s*[—-]\s*(.+?)\*\*", timing_section)
        if m2:
            result["grade"] = m2.group(1)
            result["name"] = m2.group(2).strip()
    # Coefficient: "- 系数: 0.5" (plain) or "**系数: 0.5**" (bold)
    search_text = timing_section if timing_section else text
    m3 = re.search(r"系数[：:]\s*([\d.]+)", search_text)
    if m3:
        result["coeff"] = m3.group(1)
    return result


def _extract_oprms_position(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract final position from OPRMS section.

    Returns (display_value, formula). Handles:
    1. Table: | **最终仓位** | **formula = result** |
    2. Inline: **最终仓位**: formula = **result**
    3. Plain: **最终仓位**: formula = result
    """
    # Format 1 (table): | **最终仓位** | **formula = result** |
    m = re.search(r"\*\*最终仓位\*\*\s*\|\s*\*\*(.+?)\*\*", text)
    if m:
        raw = m.group(1).strip()
        if "=" in raw:
            parts = raw.split("=")
            return parts[-1].strip(), parts[0].strip()
        return raw, None
    # Format 2/3 (inline): **最终仓位**: formula = result
    m = re.search(r"\*\*最终仓位\*\*[：:]\s*(.+?)(?:\n|$)", text)
    if m:
        raw = m.group(1).strip()
        eq_m = re.search(r"=\s*\*?\*?([\d.]+%?)\*?\*?", raw)
        if eq_m:
            return eq_m.group(1).strip("*"), raw[:raw.rfind("=")].strip()
        return raw, None
    return None, None


def _extract_oprms_verdict(text: str) -> Optional[str]:
    """Extract overall verdict from OPRMS.

    Handles:
    1. Table: | Verdict | **BUY** |
    2. Chinese: **投资桶**: Long-term Compounder
    """
    # Format 1 (English table)
    m = re.search(r"Verdict\s*\|\s*\*\*(.+?)\*\*", text)
    if m:
        return m.group(1).strip()
    # Format 2 (Chinese): **投资桶**: Long-term Compounder / Watch / Pass
    m = re.search(
        r"\*\*投资桶\*\*[：:]\s*(.+?)(?:\n|$)",
        text,
    )
    if m:
        return m.group(1).strip().strip("*")
    return None


def _extract_conviction_modifier(text: str) -> Optional[str]:
    """Extract conviction modifier from alpha_bet."""
    m = re.search(r"\*\*Value\*\*[：:]\s*([\d.]+)", text)
    if m:
        return m.group(1)
    return None


def _extract_debate_verdict(text: str) -> Optional[str]:
    """Extract overall verdict from debate.

    Handles formats:
    - H2 heading: ## 总体判定\\n\\n**VERDICT**
    - H2 heading: ## 总裁决\\n\\n**VERDICT**
    - Bold inline: **总裁决**: VERDICT  or  **总裁决**：VERDICT
    - English:   ## Overall Verdict: VERDICT
    """
    # Chinese H2: ## 总体判定 / ## 总裁决 followed by **verdict**
    m = re.search(r"##\s*(?:总体判定|总裁决)\s*\n+\*\*(.+?)\*\*", text)
    if m:
        return m.group(1).strip()
    # Bold-inline: **总裁决**: VERDICT or **总体判定**: VERDICT
    m = re.search(
        r"\*\*(?:总体判定|总裁决)\*\*[：:]\s*(.+?)(?:\n|$)",
        text,
    )
    if m:
        return m.group(1).strip().strip("*")
    # Variant: 总裁决/总体判定 (without bold) followed by bold content
    m = re.search(r"(?:总体判定|总裁决)[：:]\s*\*\*(.+?)\*\*", text)
    if m:
        return m.group(1).strip()
    # English: ## Overall Verdict: verdict text (in heading itself)
    m = re.search(r"##\s*Overall Verdict[：:]\s*(.+?)(?=\n)", text)
    if m:
        return m.group(1).strip()
    return None


# Regex matching all verdict heading variants
_VERDICT_HEADING_RE = re.compile(
    r"^##\s*(?:总体判定|总裁决|Overall Verdict)", re.MULTILINE
)

# Regex matching all tension heading variants (H2 or H3)
_TENSION_HEADING_RE = re.compile(
    r"^(#{2,3})\s*(?:张力\s*[一二三四五六七八九十\d]+|Tension\s*\d+)[：:]\s*(.+?)$",
    re.MULTILINE,
)


def _extract_sides(block: str) -> tuple:
    """Extract bull, bear, resolution from a tension block.

    Handles two formats:
    1. H3 subheading: ### 多头论证 / ### 空头论证 / ### 裁决  (GOOG style)
    2. Inline bold: **多头（...）**: / **Bull (...)**: / **决议**: (TSLA/MSFT style)
    """
    bull = bear = resolution = ""

    # Strategy 1: H3 subheading style
    if re.search(r"^###\s*(?:多头|空头|裁决)", block, re.MULTILINE):
        sections = re.split(r"\n(?=###\s)", block)
        for section in sections:
            section = section.strip()
            if not section.startswith("###"):
                continue
            header_line = section.split("\n", 1)[0]
            body = section.split("\n", 1)[1].strip() if "\n" in section else ""
            if re.match(r"###\s*多头", header_line):
                bull = body
            elif re.match(r"###\s*空头", header_line):
                bear = body
            elif re.match(r"###\s*裁决", header_line):
                resolution = body
        return bull, bear, resolution

    # Strategy 2: Inline bold labels
    m = re.search(
        r"\*\*(?:多头|Bull)\s*[（(].*?[)）]\*\*[：:]\s*(.+?)(?=\n\n|\Z)",
        block, re.DOTALL,
    )
    if m:
        bull = m.group(1).strip()

    m = re.search(
        r"\*\*(?:空头|Bear)\s*[（(].*?[)）]\*\*[：:]\s*(.+?)(?=\n\n|\Z)",
        block, re.DOTALL,
    )
    if m:
        bear = m.group(1).strip()

    m = re.search(
        r"\*\*(?:决议|Resolution)\*\*[：:]\s*(.+?)(?=\n\n|\n---|\Z)",
        block, re.DOTALL,
    )
    if m:
        resolution = m.group(1).strip()

    return bull, bear, resolution


def _stars_html(rating: str) -> str:
    """Convert numeric rating to star display."""
    try:
        val = float(rating)
    except (ValueError, TypeError):
        return html.escape(str(rating))
    full = int(val)
    half = val - full >= 0.5
    empty = 5 - full - (1 if half else 0)
    parts = []
    for _ in range(full):
        parts.append('<span class="star">&#9733;</span>')
    if half:
        parts.append('<span class="star">&#9734;</span>')
    for _ in range(empty):
        parts.append('<span style="color:var(--border);">&#9734;</span>')
    return "".join(parts)


def _verdict_tag(verdict: str) -> str:
    """Create colored verdict tag."""
    v = verdict.upper()
    if "BUY" in v:
        return '<span class="tag tag-buy">' + html.escape(verdict) + '</span>'
    elif "PASS" in v:
        return '<span class="tag tag-pass">' + html.escape(verdict) + '</span>'
    elif "HOLD" in v:
        return '<span class="tag tag-hold">' + html.escape(verdict) + '</span>'
    elif "PAIR" in v:
        return '<span class="tag tag-pair">' + html.escape(verdict) + '</span>'
    return '<span class="tag tag-gold">' + html.escape(verdict) + '</span>'


def _strip_first_heading(text: str) -> str:
    """Strip the first H1 heading and optional following blockquote/hr."""
    lines = text.split("\n")
    out = []
    skipping = True
    for line in lines:
        stripped = line.strip()
        if skipping:
            if stripped.startswith("# ") and not stripped.startswith("## "):
                continue
            if stripped.startswith("> "):
                continue
            if re.match(r"^-{3,}$", stripped):
                skipping = False
                continue
            if not stripped:
                continue
            skipping = False
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Section Builders
# ---------------------------------------------------------------------------

def build_header(symbol: str, research_dir: Path) -> str:
    """Build the header section with ticker, date, and metadata."""
    date = datetime.now().strftime("%Y-%m-%d")

    # Try to extract price/market cap from data_context or oprms
    price = ""
    mcap = ""
    regime = ""

    ctx_path = research_dir / "data_context.md"
    if ctx_path.exists():
        ctx = ctx_path.read_text(encoding="utf-8")
        m = re.search(r"Latest:\s*\$?([\d,.]+)", ctx)
        if m:
            price = "$" + m.group(1)
        m2 = re.search(r"Market Cap:\s*\$?([\d,.]+[BMT]?)", ctx)
        if m2:
            mcap = "$" + m2.group(1)

    # Extract regime from data_context.md (MacroSnapshot.format_for_prompt())
    if ctx_path.exists():
        m_regime = re.search(r"\*\*Regime:\s*(\w+)\*\*", ctx)
        if m_regime:
            regime = m_regime.group(1).upper()

    parts = ['<div class="header">']
    parts.append('  <div class="header-badge">CONFIDENTIAL &mdash; RESEARCH DESK</div>')
    parts.append(
        '  <h1><span>' + html.escape(symbol) + '</span> '
        + '\u6df1\u5ea6\u7814\u7a76\u62a5\u544a</h1>'
    )
    parts.append('  <div class="header-meta">')
    parts.append('    <span>Date: <span class="val">' + date + '</span></span>')
    parts.append('    <span>Desk: <span class="val">'
                 + '\u672a\u6765\u8d44\u672c AI Trading Desk</span></span>')
    if price:
        parts.append('    <span>Price: <span class="val">' + html.escape(price) + '</span></span>')
    if mcap:
        parts.append('    <span>MCap: <span class="val">' + html.escape(mcap) + '</span></span>')
    if regime:
        color = {"RISK_ON": "var(--green)", "RISK_OFF": "var(--red)",
                 "CRISIS": "var(--red)", "NEUTRAL": "var(--amber)"}.get(regime, "var(--text)")
        parts.append('    <span>Regime: <span class="val" style="color:' + color + ';">'
                     + html.escape(regime) + '</span></span>')
    parts.append('  </div>')
    parts.append('</div>')
    return "\n".join(parts)


def build_toc() -> str:
    """Build table of contents sidebar."""
    items = [
        ("sec-lenses", "I. \u4e94\u7ef4\u900f\u955c"),
        ("sec-debate", "II. \u6838\u5fc3\u8fa9\u8bba"),
        ("sec-memo", "III. \u6295\u8d44\u5907\u5fd8\u5f55"),
        ("sec-oprms", "IV. OPRMS \u8bc4\u7ea7"),
        ("sec-alpha", "V. \u6c42\u5bfc\u601d\u7ef4"),
    ]
    parts = ['<div class="toc">']
    parts.append('  <div class="toc-label">CONTENTS</div>')
    for anchor, label in items:
        parts.append('  <a href="#' + anchor + '">' + label + '</a>')
    parts.append('</div>')
    return "\n".join(parts)


def build_macro_section(text: str) -> str:
    """Build Section I: Macro Environment."""
    if not text or not text.strip():
        return ""

    regime, color = _extract_regime(text)
    one_liner = _extract_one_liner(text)

    parts = ['<div class="section" id="sec-macro">']
    parts.append('  <div class="section-label">Section I</div>')
    parts.append('  <div class="section-title">'
                 + '\u5b8f\u89c2\u73af\u5883 &mdash; <span>Macro Environment</span></div>')

    # Regime box with one-liner
    if one_liner:
        parts.append('  <div class="regime-box ' + color + '">')
        parts.append('    <h3>Regime: ' + html.escape(regime) + '</h3>')
        parts.append('    <div class="prose"><p>' + _inline(one_liner) + '</p></div>')
        parts.append('  </div>')

    # Render rest as prose
    body = _strip_first_heading(text)
    # Remove the one-liner line and regime line to avoid duplication
    body = re.sub(r"\*\*一句话\*\*:.*\n", "", body)
    body = re.sub(r"\*\*Regime\*\*:.*\n", "", body)
    parts.append('  <div class="prose">')
    parts.append(md_to_html(body))
    parts.append('  </div>')

    parts.append('</div>')
    return "\n".join(parts)


def build_lenses_section(research_dir: Path) -> str:
    """Build Section I: Five Lenses Analysis."""
    lens_keys = [
        "quality_compounder",
        "imaginative_growth",
        "fundamental_long_short",
        "deep_value",
        "event_driven",
    ]
    lens_numbers = {
        "quality_compounder": "1",
        "imaginative_growth": "2",
        "fundamental_long_short": "3",
        "deep_value": "4",
        "event_driven": "5",
    }

    cards = []
    for key in lens_keys:
        path = research_dir / ("lens_" + key + ".md")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            continue

        color = LENS_COLORS.get(key, "gold")
        label = LENS_LABELS.get(key, key)
        number = lens_numbers.get(key, "?")

        # Extract rating
        rating_info = _extract_rating_line(text)

        card = ['<div class="strat-card">']
        card.append('  <div class="layer-badge" style="background:var(--'
                    + color + '-dim); color:var(--' + color + ');">'
                    + 'LENS ' + number + '</div>')
        card.append('  <h3>' + html.escape(label) + '</h3>')

        if rating_info:
            card.append('  <div class="strat-meta">')
            card.append('    ' + _stars_html(rating_info["stars"]))
            card.append('    &nbsp; ' + _verdict_tag(rating_info["verdict"]))
            if rating_info.get("irr"):
                card.append('    &nbsp; <span style="color:var(--text-dim);font-size:11px;">'
                           + 'IRR: ' + html.escape(rating_info["irr"]) + '</span>')
            card.append('  </div>')

        # Render body
        body = _strip_first_heading(text)
        card.append('  <div class="prose">')
        card.append(md_to_html(body))
        card.append('  </div>')

        card.append('</div>')
        cards.append("\n".join(card))

    if not cards:
        return ""

    parts = ['<div class="section" id="sec-lenses">']
    parts.append('  <div class="section-label">Section I</div>')
    parts.append('  <div class="section-title">'
                 + '\u4e94\u7ef4\u900f\u955c\u5206\u6790 &mdash; '
                 + '<span>Five Lenses</span></div>')
    parts.extend(cards)
    parts.append('</div>')
    return "\n".join(parts)


def build_debate_section(text: str) -> str:
    """Build Section II: Core Debate with tension blocks.

    Handles three debate markdown formats:
    - GOOG style: H2 tensions (## 张力一：) + H3 sub-sections (### 多头论证)
    - TSLA style: H3 tensions (### 张力 1:) + inline bold (**多头（...）**:)
    - MSFT style: H2 tensions (## Tension 1:) + inline bold (**Bull (...)**:)
    """
    if not text or not text.strip():
        return ""

    parts = ['<div class="section" id="sec-debate">']
    parts.append('  <div class="section-label">Section II</div>')
    parts.append('  <div class="section-title">'
                 + '\u6838\u5fc3\u8fa9\u8bba &mdash; <span>Debate</span></div>')

    # Extract verdict for verdict box
    verdict = _extract_debate_verdict(text)
    if verdict:
        parts.append('  <div class="verdict-box">')
        parts.append('    <div class="verdict-text">' + _inline(verdict) + '</div>')
        parts.append('  </div>')

    # Parse tensions — support H2/H3, Chinese/English, Chinese/Arabic numerals
    tensions = list(_TENSION_HEADING_RE.finditer(text))

    # Find where verdict section starts (to bound last tension block)
    verdict_match = _VERDICT_HEADING_RE.search(text)
    verdict_start = verdict_match.start() if verdict_match else len(text)

    if tensions:
        for idx, match in enumerate(tensions):
            start = match.start()
            end = tensions[idx + 1].start() if idx + 1 < len(tensions) else verdict_start
            if end > verdict_start:
                end = verdict_start
            block = text[start:end]

            title = match.group(2).strip()

            bull, bear, resolution = _extract_sides(block)

            parts.append('  <div class="tension-block">')
            parts.append('    <div class="tension-header">'
                        + '\u5f20\u529b ' + str(idx + 1) + ': '
                        + _inline(title) + '</div>')
            if bull:
                parts.append('    <div class="tension-side bull">')
                parts.append('      <div class="side-label">BULL</div>')
                parts.append('      <div class="prose">' + md_to_html(bull) + '</div>')
                parts.append('    </div>')
            if bear:
                parts.append('    <div class="tension-side bear">')
                parts.append('      <div class="side-label">BEAR</div>')
                parts.append('      <div class="prose">' + md_to_html(bear) + '</div>')
                parts.append('    </div>')
            if resolution:
                parts.append('    <div class="tension-side resolution">')
                parts.append('      <div class="side-label">RESOLUTION</div>')
                parts.append('      <div class="prose">' + md_to_html(resolution) + '</div>')
                parts.append('    </div>')
            parts.append('  </div>')
    else:
        # Fallback: render debate body as prose if tension parsing fails
        body = text
        if verdict_match:
            body = text[:verdict_start]
        body = _strip_first_heading(body)
        if body.strip():
            parts.append('  <div class="prose">')
            parts.append(md_to_html(body))
            parts.append('  </div>')

    # Render verdict section (after verdict heading)
    if verdict_match:
        verdict_section = text[verdict_match.start():]
        # Remove heading line
        verdict_section = verdict_section.split("\n", 1)[-1] if "\n" in verdict_section else ""
        verdict_section = verdict_section.lstrip("\n")
        # Remove first bold verdict line if it matches what's in the verdict box
        if verdict and verdict_section.startswith("**"):
            first_line = verdict_section.split("\n", 1)[0]
            if verdict in first_line:
                verdict_section = verdict_section.split("\n", 1)[-1] if "\n" in verdict_section else ""
        verdict_section = verdict_section.strip()
        if verdict_section:
            parts.append('  <div class="prose">')
            parts.append(md_to_html(verdict_section))
            parts.append('  </div>')

    parts.append('</div>')
    return "\n".join(parts)


def build_memo_section(text: str) -> str:
    """Build Section III: Investment Memo."""
    if not text or not text.strip():
        return ""

    parts = ['<div class="section" id="sec-memo">']
    parts.append('  <div class="section-label">Section III</div>')
    parts.append('  <div class="section-title">'
                 + '\u6295\u8d44\u5907\u5fd8\u5f55 &mdash; <span>Memo</span></div>')

    body = _strip_first_heading(text)
    parts.append('  <div class="prose">')
    parts.append(md_to_html(body))
    parts.append('  </div>')

    parts.append('</div>')
    return "\n".join(parts)


def build_oprms_section(text: str) -> str:
    """Build Section IV: OPRMS Rating with snap cards."""
    if not text or not text.strip():
        return ""

    dna = _extract_oprms_dna(text)
    timing = _extract_oprms_timing(text)
    position_val, position_formula = _extract_oprms_position(text)
    verdict = _extract_oprms_verdict(text)

    grade_colors = {"S": "gold", "A": "green", "B": "blue", "C": "red"}

    parts = ['<div class="section" id="sec-oprms">']
    parts.append('  <div class="section-label">Section IV</div>')
    parts.append('  <div class="section-title">'
                 + 'OPRMS \u8bc4\u7ea7\u4e0e\u4ed3\u4f4d &mdash; '
                 + '<span>Rating</span></div>')

    # Snap card grid: DNA / Timing / Position / Verdict
    parts.append('  <div class="snapshot-grid">')

    # DNA card
    dna_color = grade_colors.get(dna["grade"], "amber")
    parts.append('    <div class="snap-card">')
    parts.append('      <div class="label">DNA \u8d44\u4ea7\u57fa\u56e0</div>')
    parts.append('      <div class="value" style="color:var(--' + dna_color + ');">'
                + html.escape(dna["grade"]) + '</div>')
    parts.append('      <div class="sub">' + html.escape(dna["name"]) + '</div>')
    parts.append('      <div class="sub">\u4ed3\u4f4d\u4e0a\u9650: '
                + html.escape(dna["cap"]) + '</div>')
    parts.append('      <div class="bar" style="width:100%; background:var(--'
                + dna_color + ');"></div>')
    parts.append('    </div>')

    # Timing card
    timing_color = grade_colors.get(timing["grade"], "amber")
    parts.append('    <div class="snap-card">')
    parts.append('      <div class="label">TIMING \u65f6\u673a\u7cfb\u6570</div>')
    parts.append('      <div class="value" style="color:var(--' + timing_color + ');">'
                + html.escape(timing["grade"]) + '</div>')
    parts.append('      <div class="sub">' + html.escape(timing["name"]) + '</div>')
    parts.append('      <div class="sub">\u7cfb\u6570: '
                + html.escape(timing["coeff"]) + '</div>')
    parts.append('      <div class="bar" style="width:100%; background:var(--'
                + timing_color + ');"></div>')
    parts.append('    </div>')

    # Position card
    parts.append('    <div class="snap-card">')
    parts.append('      <div class="label">POSITION \u6700\u7ec8\u4ed3\u4f4d</div>')
    parts.append('      <div class="value" style="color:var(--gold);">'
                + html.escape(position_val or "0%") + '</div>')
    if position_formula:
        parts.append('      <div class="sub">' + html.escape(position_formula) + '</div>')
    parts.append('      <div class="bar" style="width:100%; background:var(--gold);"></div>')
    parts.append('    </div>')

    # Verdict card
    parts.append('    <div class="snap-card">')
    parts.append('      <div class="label">VERDICT</div>')
    if verdict:
        parts.append('      <div class="value" style="font-size:18px;">'
                    + _verdict_tag(verdict) + '</div>')
    parts.append('    </div>')

    parts.append('  </div>')  # close snapshot-grid

    # Render full OPRMS body
    body = _strip_first_heading(text)
    parts.append('  <div class="prose">')
    parts.append(md_to_html(body))
    parts.append('  </div>')

    parts.append('</div>')
    return "\n".join(parts)


def build_alpha_section(
    red_team: str,
    gemini: str,
    cycle: str,
    bet: str,
    alpha_debate: str = "",
) -> str:
    """Build Section V: Alpha Layer (Second-Order Thinking)."""
    has_content = any(t.strip() for t in [red_team, gemini, cycle, bet, alpha_debate] if t)
    if not has_content:
        return ""

    parts = ['<div class="section" id="sec-alpha">']
    parts.append('  <div class="section-label">Section V</div>')
    parts.append('  <div class="section-title">'
                 + '\u6c42\u5bfc\u601d\u7ef4 &mdash; '
                 + '<span>Alpha Layer</span></div>')

    # Red Team — red callout
    if red_team and red_team.strip():
        parts.append('  <div class="risk-callout">')
        parts.append('    <h3>\u7ea2\u961f\u8bd5\u70bc &mdash; Red Team</h3>')
        body = _strip_first_heading(red_team)
        parts.append('    <div class="prose">')
        parts.append(md_to_html(body))
        parts.append('    </div>')
        parts.append('  </div>')

    # Gemini Contrarian — amber callout
    if gemini and gemini.strip():
        parts.append('  <div class="risk-callout amber">')
        parts.append('    <h3>Gemini \u5bf9\u7acb\u89c2\u70b9 &mdash; Contrarian View</h3>')
        body = _strip_first_heading(gemini)
        parts.append('    <div class="prose">')
        parts.append(md_to_html(body))
        parts.append('    </div>')
        parts.append('  </div>')

    # Cycle Pendulum — regime box
    if cycle and cycle.strip():
        parts.append('  <div class="regime-box cyan">')
        parts.append('    <h3>\u5468\u671f\u949f\u6446 &mdash; Cycle Pendulum</h3>')
        body = _strip_first_heading(cycle)
        parts.append('    <div class="prose">')
        parts.append(md_to_html(body))
        parts.append('    </div>')
        parts.append('  </div>')

    # Asymmetric Bet — gold regime box
    if bet and bet.strip():
        conviction = _extract_conviction_modifier(bet)
        parts.append('  <div class="regime-box gold">')
        parts.append('    <h3>\u975e\u5bf9\u79f0\u8d4c\u6ce8 &mdash; Asymmetric Bet')
        if conviction:
            parts.append('    <span style="float:right;font-size:12px;">'
                        + 'Conviction: ' + html.escape(conviction) + '</span>')
        parts.append('    </h3>')
        body = _strip_first_heading(bet)
        parts.append('    <div class="prose">')
        parts.append(md_to_html(body))
        parts.append('    </div>')
        parts.append('  </div>')

    # Alpha Debate — purple regime box (final verdict)
    if alpha_debate and alpha_debate.strip():
        # Extract final conviction modifier from debate
        m_cm = re.search(r"(?:final_)?conviction_modifier[：:]\s*([\d.]+)", alpha_debate)
        debate_cm = m_cm.group(1) if m_cm else None
        m_action = re.search(r"(?:final_action|最终行动)[：:]\s*(执行|搁置|放弃)", alpha_debate)
        debate_action = m_action.group(1) if m_action else None

        parts.append('  <div class="regime-box purple">')
        parts.append('    <h3>\u7ec8\u6781\u8fa9\u8bba &mdash; Alpha Debate')
        badge_parts = []
        if debate_cm:
            badge_parts.append('Conviction: ' + html.escape(debate_cm))
        if debate_action:
            badge_parts.append(html.escape(debate_action))
        if badge_parts:
            parts.append('    <span style="float:right;font-size:12px;">'
                        + ' | '.join(badge_parts) + '</span>')
        parts.append('    </h3>')
        body = _strip_first_heading(alpha_debate)
        parts.append('    <div class="prose">')
        parts.append(md_to_html(body))
        parts.append('    </div>')
        parts.append('  </div>')

    parts.append('</div>')
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compile_html_report(
    symbol: str,
    research_dir: Path,
    date: Optional[str] = None,
) -> Path:
    """Compile all research files into a self-contained HTML report.

    Reads intermediate analysis files from research_dir and assembles them
    into a structured HTML document with dated filename.

    Args:
        symbol: Stock ticker (e.g. "TSLA")
        research_dir: Path containing intermediate analysis markdown files
        date: Optional date string (YYYY-MM-DD). Defaults to today.

    Returns:
        Path to the generated HTML file
    """
    symbol = symbol.upper()
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    def _read(filename: str) -> str:
        path = research_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # Read all sections
    debate = _read("debate.md")
    memo = _read("memo.md")
    oprms = _read("oprms.md")
    red_team = _read("alpha_red_team.md")
    gemini = _read("gemini_contrarian.md")
    cycle = _read("alpha_cycle.md")
    bet = _read("alpha_bet.md")
    alpha_debate_text = _read("alpha_debate.md")

    # Build sections
    header_html = build_header(symbol, research_dir)
    toc_html = build_toc()
    lenses_html = build_lenses_section(research_dir)
    debate_html = build_debate_section(debate)
    memo_html = build_memo_section(memo)
    oprms_html = build_oprms_section(oprms)
    alpha_html = build_alpha_section(red_team, gemini, cycle, bet, alpha_debate_text)

    doc = '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
    doc += '<meta charset="UTF-8">\n'
    doc += '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    doc += '<title>' + html.escape(symbol) + ' \u6df1\u5ea6\u7814\u7a76\u62a5\u544a</title>\n'
    doc += ('<link href="https://fonts.googleapis.com/css2?'
            'family=JetBrains+Mono:wght@300;400;500;600;700&'
            'family=Crimson+Pro:ital,wght@0,300;0,400;0,600;1,300;1,400'
            '&display=swap" rel="stylesheet">\n')
    doc += '<style>\n' + CSS + '\n</style>\n'
    doc += '</head>\n<body>\n'
    doc += '<div class="layout">\n'
    doc += toc_html + '\n'
    doc += '<div class="container">\n'
    doc += header_html + '\n'
    doc += lenses_html + '\n'
    doc += debate_html + '\n'
    doc += memo_html + '\n'
    doc += oprms_html + '\n'
    doc += alpha_html + '\n'
    doc += '<div class="disclaimer">\n'
    doc += ('<strong>DISCLAIMER:</strong> '
            '\u672c\u62a5\u544a\u7531 AI \u751f\u6210\uff0c'
            '\u4ec5\u4f9b\u53c2\u8003\uff0c'
            '\u4e0d\u6784\u6210\u6295\u8d44\u5efa\u8bae\u3002'
            '\u6295\u8d44\u6709\u98ce\u9669\uff0c'
            '\u51b3\u7b56\u987b\u8c28\u614e\u3002<br>\n')
    doc += ('Generated by \u672a\u6765\u8d44\u672c AI Trading Desk &mdash; '
            + date + '\n')
    doc += '</div>\n'
    doc += '</div>\n'  # close container
    doc += '</div>\n'  # close layout
    doc += '</body>\n</html>\n'

    output_path = research_dir / ("full_report_" + date + ".html")
    output_path.write_text(doc, encoding="utf-8")
    logger.info("Compiled HTML report: %s (%d chars)", output_path, len(doc))

    return output_path
