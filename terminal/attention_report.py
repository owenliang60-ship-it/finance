"""
Attention Engine — HTML Report Generator.

Generates a self-contained HTML report with:
  - Stats cards (ticker count, Reddit total, news total, GT keywords)
  - Top 20 attention ranking table (sortable)
  - 12-week trend chart (inline SVG)
  - Theme keyword heat table
  - New discoveries section
"""
import html
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from terminal.attention_store import get_attention_store

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "attention"


def generate_attention_report(
    week_start: str,
    rankings: List[Dict[str, Any]],
    output_dir: Optional[Path] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> Path:
    """Generate the HTML attention report.

    Args:
        week_start: ISO date (Monday) for the report week.
        rankings: Output from compute_attention_ranking().
        output_dir: Where to write. Defaults to data/attention/.
        stats: Optional pre-computed stats dict.

    Returns:
        Path to generated HTML file.
    """
    out_dir = output_dir or _DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"report_{week_start}.html"

    store = get_attention_store()

    if stats is None:
        stats = _compute_report_stats(store, week_start)

    # Get trend history for top 5 tickers (12 weeks)
    top5_tickers = [r["ticker"] for r in rankings[:5]]
    trend_data = _get_trend_data(store, top5_tickers, week_start, weeks=12)

    # Get keyword heat data
    keyword_data = _get_keyword_heat(store, week_start)

    # Get new discoveries
    new_tickers = store.get_new_discoveries(week_start)

    # Previous week for comparison
    prev_week = _prev_monday(week_start)
    prev_rankings = store.get_weekly_ranking(prev_week, top_n=50)
    prev_map = {r["ticker"]: r for r in prev_rankings}

    doc = _build_html(
        week_start=week_start,
        rankings=rankings,
        stats=stats,
        trend_data=trend_data,
        top5_tickers=top5_tickers,
        keyword_data=keyword_data,
        new_tickers=new_tickers,
        prev_map=prev_map,
    )
    output_path.write_text(doc, encoding="utf-8")
    logger.info("Generated attention report: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _prev_monday(week_start: str) -> str:
    """Get the Monday 7 days before."""
    d = datetime.strptime(week_start, "%Y-%m-%d").date()
    prev = d - timedelta(days=7)
    return prev.isoformat()


def _compute_report_stats(store: Any, week_start: str) -> Dict[str, Any]:
    """Compute summary stats for the report header."""
    from datetime import date, timedelta

    ws = datetime.strptime(week_start, "%Y-%m-%d").date()
    ws_end = (ws + timedelta(days=6)).isoformat()
    prev_ws = (ws - timedelta(days=7)).isoformat()
    prev_end = (ws - timedelta(days=1)).isoformat()

    conn = store._get_conn()

    # Reddit totals
    reddit_cur = conn.execute(
        "SELECT COALESCE(SUM(mention_count), 0) FROM reddit_mentions WHERE scan_date >= ? AND scan_date <= ?",
        (week_start, ws_end),
    ).fetchone()[0]
    reddit_prev = conn.execute(
        "SELECT COALESCE(SUM(mention_count), 0) FROM reddit_mentions WHERE scan_date >= ? AND scan_date <= ?",
        (prev_ws, prev_end),
    ).fetchone()[0]

    # News totals
    news_cur = conn.execute(
        "SELECT COALESCE(SUM(article_count), 0) FROM news_mentions WHERE scan_date >= ? AND scan_date <= ?",
        (week_start, ws_end),
    ).fetchone()[0]
    news_prev = conn.execute(
        "SELECT COALESCE(SUM(article_count), 0) FROM news_mentions WHERE scan_date >= ? AND scan_date <= ?",
        (prev_ws, prev_end),
    ).fetchone()[0]

    # Unique tickers this week
    tickers_scanned = conn.execute(
        """
        SELECT COUNT(DISTINCT ticker) FROM (
            SELECT ticker FROM reddit_mentions WHERE scan_date >= ? AND scan_date <= ?
            UNION
            SELECT ticker FROM news_mentions WHERE scan_date >= ? AND scan_date <= ?
        )
        """,
        (week_start, ws_end, week_start, ws_end),
    ).fetchone()[0]

    # Active keywords
    active_kw = conn.execute(
        "SELECT COUNT(*) FROM theme_keywords WHERE is_active = 1"
    ).fetchone()[0]

    def _pct_change(cur: int, prev: int) -> Optional[float]:
        if prev == 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    return {
        "tickers_scanned": tickers_scanned,
        "reddit_total": reddit_cur,
        "reddit_prev": reddit_prev,
        "reddit_change_pct": _pct_change(reddit_cur, reddit_prev),
        "news_total": news_cur,
        "news_prev": news_prev,
        "news_change_pct": _pct_change(news_cur, news_prev),
        "active_keywords": active_kw,
    }


def _get_trend_data(
    store: Any, tickers: List[str], week_start: str, weeks: int = 12,
) -> Dict[str, List[Dict[str, Any]]]:
    """Get weekly composite scores for trend chart."""
    result = {}
    for ticker in tickers:
        history = store.get_ticker_history(ticker, weeks=weeks)
        # Reverse to chronological order
        result[ticker] = list(reversed(history))
    return result


def _get_keyword_heat(store: Any, week_start: str) -> List[Dict[str, Any]]:
    """Get keyword GT data for current vs previous week."""
    prev_ws = _prev_monday(week_start)
    keywords = store.get_keywords(active_only=True)

    conn = store._get_conn()
    result = []
    for kw_info in keywords:
        kw = kw_info["keyword"]
        # Current week
        cur = conn.execute(
            "SELECT interest_score, anchor_ratio FROM google_trends WHERE keyword = ? AND week_start = ?",
            (kw, week_start),
        ).fetchone()
        # Previous week
        prev = conn.execute(
            "SELECT interest_score, anchor_ratio FROM google_trends WHERE keyword = ? AND week_start = ?",
            (kw, prev_ws),
        ).fetchone()

        cur_score = cur["interest_score"] if cur else 0
        prev_score = prev["interest_score"] if prev else 0
        change_pct = round((cur_score - prev_score) / max(prev_score, 1) * 100, 1)

        result.append({
            "keyword": kw,
            "category": kw_info.get("category", ""),
            "tickers": kw_info.get("related_tickers", []),
            "current_score": cur_score,
            "prev_score": prev_score,
            "change_pct": change_pct,
        })

    return result


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(
    week_start: str,
    rankings: List[Dict[str, Any]],
    stats: Dict[str, Any],
    trend_data: Dict[str, List[Dict]],
    top5_tickers: List[str],
    keyword_data: List[Dict],
    new_tickers: List[str],
    prev_map: Dict[str, Dict],
) -> str:
    """Build the complete HTML document."""
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    p = []  # parts

    p.append('<!DOCTYPE html>')
    p.append('<html lang="zh-CN">')
    p.append('<head>')
    p.append('<meta charset="UTF-8">')
    p.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    p.append(f'<title>注意力雷达 — {html.escape(week_start)}</title>')
    p.append('<link href="https://fonts.googleapis.com/css2?'
             'family=JetBrains+Mono:wght@300;400;500;600;700&'
             'family=Crimson+Pro:ital,wght@0,300;0,400;0,600;1,300;1,400'
             '&display=swap" rel="stylesheet">')
    p.append(f'<style>{_CSS}</style>')
    p.append('</head>')
    p.append('<body>')

    # Header
    p.append('<div class="report-header">')
    p.append('<div class="header-badge">CONFIDENTIAL</div>')
    p.append(f'<h1>注意力雷达 <span>Attention Radar</span></h1>')
    p.append(f'<div class="header-meta">Week of {html.escape(week_start)} &middot; Generated {html.escape(date_str)}</div>')
    p.append('</div>')

    # Stats cards
    p.append('<div class="stats-grid">')
    p.append(_stat_card("Tickers", str(stats["tickers_scanned"]), "本周扫描"))
    reddit_sub = _change_badge(stats.get("reddit_change_pct"))
    p.append(_stat_card("Reddit", f'{stats["reddit_total"]:,}', f"提及量 {reddit_sub}", is_html=True))
    news_sub = _change_badge(stats.get("news_change_pct"))
    p.append(_stat_card("News", f'{stats["news_total"]:,}', f"文章数 {news_sub}", is_html=True))
    p.append(_stat_card("Keywords", str(stats["active_keywords"]), "GT 关键词"))
    p.append('</div>')

    # Top 20 ranking table
    p.append('<div class="section-label">TOP ATTENTION RANKING</div>')
    p.append(_build_ranking_table(rankings, prev_map))

    # Trend chart (SVG)
    if trend_data and any(trend_data.values()):
        p.append('<div class="section-label" style="margin-top:40px;">12-WEEK TREND — TOP 5</div>')
        p.append(_build_trend_chart(trend_data, top5_tickers))

    # Keyword heat table
    if keyword_data:
        p.append('<div class="section-label" style="margin-top:40px;">THEME KEYWORD HEAT</div>')
        p.append(_build_keyword_table(keyword_data))

    # New discoveries
    if new_tickers:
        p.append('<div class="section-label" style="margin-top:40px;">NEW DISCOVERIES</div>')
        p.append('<div class="new-discoveries">')
        for t in new_tickers:
            p.append(f'<span class="new-ticker">{html.escape(t)}</span>')
        p.append('</div>')

    # Footer
    p.append('<div class="footer">')
    p.append(f'Generated by 未来资本 Attention Engine<br>{html.escape(date_str)}')
    p.append('</div>')

    # JS
    p.append(f'<script>{_JS}</script>')

    p.append('</body>')
    p.append('</html>')

    return '\n'.join(p)


def _stat_card(label: str, value: str, sub: str, is_html: bool = False) -> str:
    val_html = html.escape(value) if not is_html else value
    sub_html = sub if is_html else html.escape(sub)
    return (
        '<div class="stat-card">'
        f'<div class="stat-label">{html.escape(label)}</div>'
        f'<div class="stat-value">{val_html}</div>'
        f'<div class="stat-sub">{sub_html}</div>'
        '</div>'
    )


def _change_badge(pct: Optional[float]) -> str:
    if pct is None:
        return ""
    if pct > 0:
        return f'<span class="change-up">+{pct}%</span>'
    elif pct < 0:
        return f'<span class="change-down">{pct}%</span>'
    return '<span class="change-flat">0%</span>'


def _rank_change_indicator(ticker: str, current_rank: int, prev_map: Dict) -> str:
    if ticker not in prev_map:
        return '<span class="rank-new">NEW</span>'
    prev_rank = prev_map[ticker].get("rank", 999)
    diff = prev_rank - current_rank
    if diff > 0:
        return f'<span class="rank-up">+{diff}</span>'
    elif diff < 0:
        return f'<span class="rank-down">{diff}</span>'
    return '<span class="rank-flat">=</span>'


def _build_ranking_table(rankings: List[Dict], prev_map: Dict) -> str:
    p = ['<table class="ranking-table" id="ranking-table">']
    p.append('<thead><tr>')
    p.append('<th class="sortable" onclick="sortTable(0)">#</th>')
    p.append('<th class="sortable" onclick="sortTable(1)">Ticker</th>')
    p.append('<th class="sortable" onclick="sortTable(2)">综合分</th>')
    p.append('<th class="sortable" onclick="sortTable(3)">Reddit Z</th>')
    p.append('<th class="sortable" onclick="sortTable(4)">News Z</th>')
    p.append('<th class="sortable" onclick="sortTable(5)">GT Z</th>')
    p.append('<th>变动</th>')
    p.append('</tr></thead>')
    p.append('<tbody>')

    for r in rankings:
        rank = r.get("rank", 0)
        ticker = r.get("ticker", "")
        composite = r.get("composite_score", 0)
        r_z = r.get("reddit_zscore", 0)
        n_z = r.get("news_zscore", 0)
        t_z = r.get("trends_zscore", 0)

        change_html = _rank_change_indicator(ticker, rank, prev_map)

        # Color intensity based on Z-score
        p.append(f'<tr data-ticker="{html.escape(ticker)}">')
        p.append(f'<td class="center mono">{rank}</td>')
        p.append(f'<td class="ticker-cell">{html.escape(ticker)}</td>')
        p.append(f'<td class="center mono score">{_score_badge(composite)}</td>')
        p.append(f'<td class="center mono">{_z_cell(r_z)}</td>')
        p.append(f'<td class="center mono">{_z_cell(n_z)}</td>')
        p.append(f'<td class="center mono">{_z_cell(t_z)}</td>')
        p.append(f'<td class="center">{change_html}</td>')
        p.append('</tr>')

    p.append('</tbody></table>')
    return '\n'.join(p)


def _score_badge(score: float) -> str:
    if score >= 2.0:
        color = "#dc3545"  # hot red
    elif score >= 1.0:
        color = "#e8960c"  # amber
    elif score >= 0.5:
        color = "#d4a012"  # gold
    else:
        color = "#7a7265"  # dim
    return (
        f'<span style="color:{color};font-weight:700;">'
        f'{score:.2f}</span>'
    )


def _z_cell(z: float) -> str:
    if abs(z) < 0.01:
        return '<span style="color:#b0a898;">—</span>'
    if z >= 2.0:
        color = "#dc3545"
    elif z >= 1.0:
        color = "#e8960c"
    elif z >= 0:
        color = "#7a7265"
    else:
        color = "#2563eb"
    return f'<span style="color:{color};">{z:.2f}</span>'


def _build_trend_chart(
    trend_data: Dict[str, List[Dict]], tickers: List[str],
) -> str:
    """Build an inline SVG line chart for top 5 tickers over 12 weeks."""
    # Chart dimensions
    w, h = 800, 280
    pad_l, pad_r, pad_t, pad_b = 60, 120, 20, 40

    chart_w = w - pad_l - pad_r
    chart_h = h - pad_t - pad_b

    # Collect all data points
    all_weeks: set = set()
    all_scores: List[float] = []
    for ticker in tickers:
        for pt in trend_data.get(ticker, []):
            all_weeks.add(pt["week_start"])
            all_scores.append(pt.get("composite_score", 0))

    if not all_scores or not all_weeks:
        return '<div class="dim" style="padding:20px;">No trend data available</div>'

    weeks_sorted = sorted(all_weeks)
    y_min = min(all_scores) - 0.5
    y_max = max(all_scores) + 0.5
    if y_max <= y_min:
        y_max = y_min + 1

    def x_pos(week_str: str) -> float:
        idx = weeks_sorted.index(week_str) if week_str in weeks_sorted else 0
        return pad_l + (idx / max(len(weeks_sorted) - 1, 1)) * chart_w

    def y_pos(val: float) -> float:
        return pad_t + chart_h - ((val - y_min) / (y_max - y_min)) * chart_h

    colors = ["#dc3545", "#e8960c", "#2563eb", "#198754", "#7c3aed"]

    svg = [f'<svg viewBox="0 0 {w} {h}" style="width:100%;max-width:{w}px;'
           f'background:var(--surface);border:1px solid var(--border);margin:8px 0;">']

    # Grid lines
    for i in range(5):
        y_val = y_min + (y_max - y_min) * i / 4
        y = y_pos(y_val)
        svg.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" '
                   f'stroke="#e8e0d0" stroke-width="0.5"/>')
        svg.append(f'<text x="{pad_l - 8}" y="{y:.1f}" text-anchor="end" '
                   f'fill="#7a7265" font-size="10" dy="3">{y_val:.1f}</text>')

    # X axis labels (every 2 weeks)
    for i, ws in enumerate(weeks_sorted):
        if i % 2 == 0 or i == len(weeks_sorted) - 1:
            x = x_pos(ws)
            label = ws[5:]  # MM-DD
            svg.append(f'<text x="{x:.1f}" y="{h - 8}" text-anchor="middle" '
                       f'fill="#7a7265" font-size="9">{html.escape(label)}</text>')

    # Lines + legends
    for idx, ticker in enumerate(tickers):
        data = trend_data.get(ticker, [])
        if not data:
            continue
        color = colors[idx % len(colors)]
        points = []
        for pt in data:
            ws = pt["week_start"]
            score = pt.get("composite_score", 0)
            if ws in weeks_sorted:
                points.append((x_pos(ws), y_pos(score)))

        if len(points) >= 2:
            path_d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
            svg.append(f'<path d="{path_d}" fill="none" stroke="{color}" '
                       f'stroke-width="2" stroke-linejoin="round"/>')
            # Dots on last point
            lx, ly = points[-1]
            svg.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{color}"/>')

        # Legend
        legend_y = pad_t + 16 + idx * 18
        legend_x = w - pad_r + 12
        svg.append(f'<line x1="{legend_x}" y1="{legend_y}" '
                   f'x2="{legend_x + 16}" y2="{legend_y}" stroke="{color}" stroke-width="2"/>')
        svg.append(f'<text x="{legend_x + 22}" y="{legend_y}" '
                   f'fill="{color}" font-size="11" font-weight="600" dy="4">'
                   f'{html.escape(ticker)}</text>')

    svg.append('</svg>')
    return '\n'.join(svg)


def _build_keyword_table(keyword_data: List[Dict]) -> str:
    p = ['<table class="keyword-table">']
    p.append('<thead><tr>')
    p.append('<th>关键词</th>')
    p.append('<th>分类</th>')
    p.append('<th>本周 GT</th>')
    p.append('<th>上周 GT</th>')
    p.append('<th>变化%</th>')
    p.append('<th>关联 Ticker</th>')
    p.append('</tr></thead>')
    p.append('<tbody>')

    for kw in keyword_data:
        change = kw.get("change_pct", 0)
        if change > 20:
            change_cls = "change-up"
        elif change < -20:
            change_cls = "change-down"
        else:
            change_cls = "change-flat"

        tickers_str = ", ".join(kw.get("tickers", []))
        p.append('<tr>')
        p.append(f'<td class="keyword-name">{html.escape(kw["keyword"])}</td>')
        p.append(f'<td class="dim">{html.escape(kw.get("category", ""))}</td>')
        p.append(f'<td class="center mono">{kw["current_score"]}</td>')
        p.append(f'<td class="center mono dim">{kw["prev_score"]}</td>')
        p.append(f'<td class="center {change_cls}">{change:+.1f}%</td>')
        p.append(f'<td class="dim">{html.escape(tickers_str)}</td>')
        p.append('</tr>')

    p.append('</tbody></table>')
    return '\n'.join(p)


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
  --green: #198754;
  --amber: #e8960c;
  --blue: #2563eb;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  line-height: 1.7;
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}

.report-header {
  border-bottom: 1px solid var(--border);
  padding-bottom: 24px;
  margin-bottom: 32px;
}

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
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
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
.stat-value { font-size: 24px; font-weight: 700; }
.stat-sub { font-size: 10px; color: var(--text-dim); margin-top: 2px; }

.section-label {
  font-size: 10px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 12px;
}

/* Tables */
.ranking-table, .keyword-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.ranking-table th, .keyword-table th {
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

.ranking-table th.sortable { cursor: pointer; }
.ranking-table th.sortable:hover { color: var(--gold); }

.ranking-table td, .keyword-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}

.ranking-table tr:hover td, .keyword-table tr:hover td {
  background: var(--surface2);
}

.center { text-align: center; }
.mono { font-family: 'JetBrains Mono', monospace; }
.dim { color: var(--text-dim); }

.ticker-cell {
  font-weight: 700;
  letter-spacing: 0.5px;
}

.keyword-name { font-weight: 600; }

/* Change indicators */
.change-up { color: var(--red); font-weight: 600; }
.change-down { color: var(--green); font-weight: 600; }
.change-flat { color: var(--text-muted); }

.rank-new {
  display: inline-block;
  padding: 1px 6px;
  font-size: 9px;
  letter-spacing: 1px;
  background: var(--gold);
  color: #fff;
  font-weight: 700;
}

.rank-up { color: var(--red); font-weight: 600; }
.rank-down { color: var(--green); font-weight: 600; }
.rank-flat { color: var(--text-muted); }

/* New discoveries */
.new-discoveries {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 0;
}

.new-ticker {
  display: inline-block;
  padding: 6px 14px;
  background: var(--gold-dim);
  border: 1px solid var(--gold);
  color: var(--gold);
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 1px;
}

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
}

@media print {
  .ranking-table th.sortable { cursor: default; }
}
"""

# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

_JS = """
function sortTable(colIdx) {
  var table = document.getElementById('ranking-table');
  if (!table) return;
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));

  var currentDir = table.getAttribute('data-sort-dir-' + colIdx);
  var asc = currentDir !== 'asc';
  table.setAttribute('data-sort-dir-' + colIdx, asc ? 'asc' : 'desc');

  rows.sort(function(a, b) {
    var aText = a.cells[colIdx].textContent.trim();
    var bText = b.cells[colIdx].textContent.trim();

    var aNum = parseFloat(aText.replace(/[^0-9.\\-]/g, ''));
    var bNum = parseFloat(bText.replace(/[^0-9.\\-]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return asc ? aNum - bNum : bNum - aNum;
    }
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  });

  rows.forEach(function(row) { tbody.appendChild(row); });
}
"""
