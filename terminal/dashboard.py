"""
HTML Dashboard generator for the Company Database.

Generates a self-contained single-page HTML file showing all companies,
OPRMS ratings, and analysis status. Reuses css from html_report.py.
"""
import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from terminal.company_store import get_store

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_OUTPUT = _PROJECT_ROOT / "data" / "dashboard.html"

# Grade → color mapping
_GRADE_COLORS = {
    "S": ("gold", "#d4a012"),
    "A": ("green", "#198754"),
    "B": ("blue", "#2563eb"),
    "C": ("red", "#dc3545"),
}

_BUCKET_COLORS = {
    "Long-term Compounder": "green",
    "Catalyst-Driven Long": "blue",
    "Watch": "amber",
    "Pass": "red",
}


def _grade_badge(grade: Optional[str]) -> str:
    """Render a colored grade badge."""
    if not grade or grade == "?":
        return '<span style="color:#b0a898;">—</span>'
    _, hex_color = _GRADE_COLORS.get(grade, ("amber", "#e8960c"))
    return (
        f'<span style="display:inline-block;width:28px;height:28px;'
        f'line-height:28px;text-align:center;border-radius:4px;'
        f'font-weight:700;font-size:14px;color:#fff;'
        f'background:{hex_color};">{html.escape(grade)}</span>'
    )


def _bucket_tag(bucket: Optional[str]) -> str:
    """Render an investment bucket tag."""
    if not bucket:
        return ""
    color = _BUCKET_COLORS.get(bucket, "amber")
    return (
        f'<span style="display:inline-block;padding:2px 8px;font-size:10px;'
        f'letter-spacing:1px;border-radius:2px;font-weight:600;'
        f'background:var(--{color}-dim);color:var(--{color});">'
        f'{html.escape(bucket)}</span>'
    )


def _verdict_badge(verdict: Optional[str]) -> str:
    """Render a verdict badge."""
    if not verdict:
        return ""
    v = verdict.upper()
    if "BUY" in v:
        color, bg = "#198754", "#19875415"
    elif "HOLD" in v:
        color, bg = "#e8960c", "#e8960c15"
    elif "SELL" in v or "PASS" in v:
        color, bg = "#dc3545", "#dc354515"
    else:
        color, bg = "#d4a012", "#d4a01222"
    return (
        f'<span style="display:inline-block;padding:2px 8px;font-size:10px;'
        f'letter-spacing:1px;border-radius:2px;font-weight:600;'
        f'background:{bg};color:{color};">'
        f'{html.escape(verdict[:20])}</span>'
    )


def _relative_date(date_str: Optional[str]) -> str:
    """Format a date string as relative (e.g., '3d ago')."""
    if not date_str:
        return "—"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        delta = datetime.now() - dt.replace(tzinfo=None)
        days = delta.days
        if days == 0:
            return "today"
        elif days == 1:
            return "1d ago"
        elif days < 30:
            return f"{days}d ago"
        else:
            return f"{days // 30}mo ago"
    except (ValueError, TypeError):
        return date_str[:10] if date_str else "—"


def generate_dashboard(output_path: Optional[Path] = None) -> Path:
    """Generate the HTML dashboard file.

    Args:
        output_path: Where to write the HTML. Defaults to data/dashboard.html.

    Returns:
        Path to the generated file.
    """
    output_path = output_path or _DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)

    store = get_store()
    data = store.get_dashboard_data()
    stats = store.get_stats()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")

    doc = _build_html(data, stats, date)
    output_path.write_text(doc, encoding="utf-8")
    logger.info("Generated dashboard: %s (%d companies)", output_path, len(data))
    return output_path


def _build_html(
    companies: List[Dict[str, Any]],
    stats: Dict[str, Any],
    date: str,
) -> str:
    """Build the complete HTML document."""
    # Separate rated vs unrated
    rated = [c for c in companies if c.get("dna")]
    unrated = [c for c in companies if not c.get("dna")]

    parts = []
    parts.append('<!DOCTYPE html>')
    parts.append('<html lang="zh-CN">')
    parts.append('<head>')
    parts.append('<meta charset="UTF-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    parts.append('<title>未来资本 — Company Dashboard</title>')
    parts.append(
        '<link href="https://fonts.googleapis.com/css2?'
        'family=JetBrains+Mono:wght@300;400;500;600;700&'
        'family=Crimson+Pro:ital,wght@0,300;0,400;0,600;1,300;1,400'
        '&display=swap" rel="stylesheet">'
    )
    parts.append('<style>')
    parts.append(_CSS)
    parts.append('</style>')
    parts.append('</head>')
    parts.append('<body>')

    # Header
    parts.append('<div class="dashboard-header">')
    parts.append('<div class="header-inner">')
    parts.append('<div class="header-badge">CONFIDENTIAL</div>')
    parts.append('<h1>未来资本 <span>Company Dashboard</span></h1>')
    parts.append(f'<div class="header-meta">Generated: {html.escape(date)}</div>')
    parts.append('</div>')
    parts.append('</div>')

    # Stats cards
    parts.append('<div class="stats-grid">')
    parts.append(_stat_card("Total", str(stats["total_companies"]), "Companies in DB"))
    parts.append(_stat_card("Pool", str(stats["in_pool"]), "Active stock pool"))
    parts.append(_stat_card("Rated", str(stats["rated"]), "With OPRMS rating"))
    parts.append(_stat_card("Analyzed", str(stats["analyzed"]), "With deep analysis"))

    # DNA distribution mini
    dna = stats.get("dna_distribution", {})
    dna_parts = []
    for grade in ["S", "A", "B", "C"]:
        count = dna.get(grade, 0)
        if count > 0:
            _, hex_c = _GRADE_COLORS.get(grade, ("amber", "#e8960c"))
            dna_parts.append(
                f'<span style="color:{hex_c};font-weight:700;">'
                f'{grade}:{count}</span>'
            )
    dna_str = " ".join(dna_parts) if dna_parts else "—"
    parts.append(_stat_card("DNA", dna_str, "Rating distribution", is_html=True))
    parts.append('</div>')

    # Filter controls
    parts.append('<div class="controls">')
    parts.append('<input type="text" id="search" placeholder="Filter by symbol or name..." '
                 'oninput="filterTable()">')
    parts.append('<select id="dna-filter" onchange="filterTable()">')
    parts.append('<option value="">All DNA</option>')
    for g in ["S", "A", "B", "C"]:
        parts.append(f'<option value="{g}">{g}</option>')
    parts.append('</select>')
    parts.append('<select id="pool-filter" onchange="filterTable()">')
    parts.append('<option value="">All</option>')
    parts.append('<option value="1">In Pool</option>')
    parts.append('<option value="0">Out of Pool</option>')
    parts.append('</select>')
    parts.append('</div>')

    # Rated companies table
    if rated:
        parts.append('<div class="section-label">RATED COMPANIES</div>')
        parts.append(_build_table(rated, show_analysis=True))

    # Unrated companies table (collapsed)
    if unrated:
        parts.append(f'<div class="section-label" style="margin-top:48px;">UNRATED ({len(unrated)})</div>')
        parts.append('<details>')
        parts.append('<summary style="cursor:pointer;font-size:12px;color:#7a7265;margin-bottom:12px;">'
                     'Show unrated companies</summary>')
        parts.append(_build_table(unrated, show_analysis=False))
        parts.append('</details>')

    # Footer
    parts.append('<div class="footer">')
    parts.append('Generated by 未来资本 AI Trading Desk<br>')
    parts.append(f'{html.escape(date)}')
    parts.append('</div>')

    # Filter JS
    parts.append('<script>')
    parts.append(_FILTER_JS)
    parts.append('</script>')

    parts.append('</body>')
    parts.append('</html>')

    return '\n'.join(parts)


def _stat_card(label: str, value: str, sub: str, is_html: bool = False) -> str:
    """Build a stats summary card."""
    val_html = value if is_html else html.escape(value)
    return (
        '<div class="stat-card">'
        f'<div class="stat-label">{html.escape(label)}</div>'
        f'<div class="stat-value">{val_html}</div>'
        f'<div class="stat-sub">{html.escape(sub)}</div>'
        '</div>'
    )


def _build_table(companies: List[Dict[str, Any]], show_analysis: bool) -> str:
    """Build the company data table."""
    parts = ['<table class="company-table" id="company-table">']
    parts.append('<thead><tr>')
    parts.append('<th class="sortable" onclick="sortTable(0)">Symbol</th>')
    parts.append('<th>Name</th>')
    parts.append('<th>Sector</th>')
    if show_analysis:
        parts.append('<th class="sortable" onclick="sortTable(3)">DNA</th>')
        parts.append('<th class="sortable" onclick="sortTable(4)">Timing</th>')
        parts.append('<th class="sortable" onclick="sortTable(5)">Coeff</th>')
        parts.append('<th class="sortable" onclick="sortTable(6)">Position%</th>')
        parts.append('<th>Verdict</th>')
        parts.append('<th>Bucket</th>')
        parts.append('<th>Last Analysis</th>')
        parts.append('<th>Report</th>')
    else:
        parts.append('<th>Exchange</th>')
        parts.append('<th>In Pool</th>')
    parts.append('</tr></thead>')
    parts.append('<tbody>')

    for c in companies:
        pool_val = c.get("in_pool", 0)
        dna_val = c.get("dna") or ""
        parts.append(
            f'<tr data-symbol="{html.escape(c.get("symbol", ""))}" '
            f'data-name="{html.escape(c.get("company_name", ""))}" '
            f'data-dna="{html.escape(dna_val)}" '
            f'data-pool="{pool_val}">'
        )
        parts.append(f'<td class="symbol">{html.escape(c.get("symbol", ""))}</td>')
        parts.append(f'<td>{html.escape(c.get("company_name", "")[:30])}</td>')
        parts.append(f'<td class="dim">{html.escape(c.get("sector", "")[:20])}</td>')

        if show_analysis:
            parts.append(f'<td class="center">{_grade_badge(c.get("dna"))}</td>')
            parts.append(f'<td class="center">{_grade_badge(c.get("timing"))}</td>')
            coeff = c.get("timing_coeff")
            coeff_str = f"{coeff:.1f}" if coeff is not None else "—"
            parts.append(f'<td class="center mono">{coeff_str}</td>')
            pos = c.get("position_pct")
            pos_str = f"{pos:.1f}%" if pos is not None else "—"
            parts.append(f'<td class="center mono">{pos_str}</td>')
            parts.append(f'<td>{_verdict_badge(c.get("oprms_verdict") or c.get("debate_verdict"))}</td>')
            parts.append(f'<td>{_bucket_tag(c.get("investment_bucket"))}</td>')
            parts.append(f'<td class="dim">{_relative_date(c.get("analysis_date") or c.get("oprms_date"))}</td>')
            # Report link
            report_path = c.get("html_report_path") or c.get("report_path") or ""
            if report_path:
                parts.append(f'<td><a href="file://{html.escape(report_path)}" '
                             f'style="color:#2563eb;text-decoration:none;" '
                             f'title="{html.escape(report_path)}">View</a></td>')
            else:
                parts.append('<td class="dim">—</td>')
        else:
            parts.append(f'<td class="dim">{html.escape(c.get("exchange", ""))}</td>')
            pool_icon = "In Pool" if pool_val else "—"
            parts.append(f'<td class="dim">{pool_icon}</td>')

        parts.append('</tr>')

    parts.append('</tbody>')
    parts.append('</table>')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
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
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  line-height: 1.7;
  max-width: 1400px;
  margin: 0 auto;
  padding: 24px;
}

.dashboard-header {
  border-bottom: 1px solid var(--border);
  padding-bottom: 24px;
  margin-bottom: 32px;
}

.header-inner { position: relative; }

.header-badge {
  display: inline-block;
  font-size: 9px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--gold);
  border: 1px solid var(--gold);
  padding: 3px 10px;
  margin-bottom: 12px;
}

h1 {
  font-family: 'Crimson Pro', serif;
  font-size: 36px;
  font-weight: 300;
  color: var(--text);
}

h1 span { color: var(--gold); font-weight: 600; }

.header-meta {
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 8px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 32px;
}

.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

.stat-label {
  font-size: 9px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 4px;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--text);
}

.stat-sub {
  font-size: 10px;
  color: var(--text-dim);
  margin-top: 2px;
}

.controls {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.controls input, .controls select {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  outline: none;
}

.controls input { flex: 1; min-width: 200px; }
.controls input:focus { border-color: var(--gold); }

.section-label {
  font-size: 10px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 12px;
}

.company-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.company-table th {
  font-size: 9px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-muted);
  text-align: left;
  padding: 10px 12px;
  border-bottom: 2px solid var(--border);
  white-space: nowrap;
  user-select: none;
}

.company-table th.sortable { cursor: pointer; }
.company-table th.sortable:hover { color: var(--gold); }

.company-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}

.company-table tr:hover td { background: var(--surface2); }

.company-table .symbol {
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.5px;
}

.company-table .center { text-align: center; }
.company-table .mono { font-family: 'JetBrains Mono', monospace; }
.company-table .dim { color: var(--text-dim); }

.footer {
  margin-top: 48px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  font-size: 10px;
  color: var(--text-muted);
  text-align: center;
}

@media (max-width: 768px) {
  body { padding: 12px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  .company-table { font-size: 11px; }
}

@media print {
  .controls { display: none; }
  .company-table { font-size: 10px; }
}
"""

# ---------------------------------------------------------------------------
# JavaScript for filtering and sorting
# ---------------------------------------------------------------------------

_FILTER_JS = """
function filterTable() {
  var search = document.getElementById('search').value.toLowerCase();
  var dnaFilter = document.getElementById('dna-filter').value;
  var poolFilter = document.getElementById('pool-filter').value;
  var rows = document.querySelectorAll('.company-table tbody tr');

  rows.forEach(function(row) {
    var symbol = (row.getAttribute('data-symbol') || '').toLowerCase();
    var name = (row.getAttribute('data-name') || '').toLowerCase();
    var dna = row.getAttribute('data-dna') || '';
    var pool = row.getAttribute('data-pool') || '';

    var matchSearch = !search || symbol.indexOf(search) >= 0 || name.indexOf(search) >= 0;
    var matchDna = !dnaFilter || dna === dnaFilter;
    var matchPool = !poolFilter || pool === poolFilter;

    row.style.display = (matchSearch && matchDna && matchPool) ? '' : 'none';
  });
}

function sortTable(colIdx) {
  var table = document.querySelector('.company-table');
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));

  // Toggle sort direction
  var currentDir = table.getAttribute('data-sort-dir-' + colIdx);
  var asc = currentDir !== 'asc';
  table.setAttribute('data-sort-dir-' + colIdx, asc ? 'asc' : 'desc');

  rows.sort(function(a, b) {
    var aText = a.cells[colIdx].textContent.trim();
    var bText = b.cells[colIdx].textContent.trim();

    // Try numeric sort
    var aNum = parseFloat(aText.replace('%', ''));
    var bNum = parseFloat(bText.replace('%', ''));
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return asc ? aNum - bNum : bNum - aNum;
    }

    // Alphabetic sort
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  });

  rows.forEach(function(row) { tbody.appendChild(row); });
}
"""
