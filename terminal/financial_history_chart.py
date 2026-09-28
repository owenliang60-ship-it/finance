"""Three-panel PNG for the frozen financial history (price / revenue QoQ / net income QoQ).

Layout follows the AMD five-year prototype in
docs/handoffs/2026-09-28-company-financial-charts/legacy-code, with module
globals replaced by the frozen history dict so one company never leaks
settings into the next. Only draws what financial_history.json contains.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from terminal.financial_history import NA_REASONS, format_money  # noqa: E402
from terminal.report_fonts import FONT_CANDIDATES  # noqa: E402

INCOME_QOQ_CLIP = 200.0
REVENUE_QOQ_CLIP = 200.0

BG = "#F4F6F9"
PANEL = "#FFFFFF"
TEXT = "#182334"
MUTED = "#5C677A"
BLUE = "#2463A8"
GREEN = "#138C6A"
GREEN_EST = "#8DD3BE"
PURPLE = "#6254C7"
PURPLE_EST = "#B7AFE8"
RED = "#D45555"
RED_EST = "#E9A4A4"
GRID = "#D9E1EA"
GRAY = "#8A94A3"

def pick_font() -> fm.FontProperties:
    for candidate in FONT_CANDIDATES["bold"]:
        if Path(candidate).exists():
            return fm.FontProperties(fname=candidate)
    raise RuntimeError("找不到 CJK 字体，中文会缺字；请安装 Noto Sans CJK / PingFang")


def _money(value: Optional[float], currency: Optional[str]) -> str:
    return format_money(value, currency)


def _na(reason: Optional[str]) -> str:
    return NA_REASONS.get(reason or "", reason or "")


class _Axis:
    """Shared calendar-quarter x axis."""

    def __init__(self, first: pd.Period, last: pd.Period):
        self.first = first
        self.periods = pd.period_range(first, last, freq="Q")

    def qx(self, quarter: str) -> float:
        return float(pd.Period(quarter, "Q").ordinal - self.first.ordinal) + 0.5

    def dx(self, day: pd.Timestamp) -> float:
        q = day.to_period("Q")
        start, end = q.start_time, q.end_time.normalize()
        return float(q.ordinal - self.first.ordinal) + (day - start).days / max((end - start).days, 1)

    def style(self, ax, font, *, labels: bool, zero_line: bool = False) -> None:
        ax.set_facecolor(PANEL)
        ax.grid(axis="y", color=GRID, linewidth=0.75, alpha=0.9)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(colors=MUTED, labelsize=10)
        if zero_line:
            ax.axhline(0, color="#566273", linewidth=1.0)
        for p in self.periods[1:]:
            if p.quarter == 1:
                ax.axvline(p.ordinal - self.first.ordinal, color="#DDE4EC", linewidth=0.9)
        ax.set_xlim(0, len(self.periods))
        ax.set_xticks(np.arange(len(self.periods)) + 0.5)
        if labels:
            ax.set_xticklabels([f"{p.year} Q{p.quarter}" for p in self.periods], rotation=40,
                               ha="right", fontproperties=font, fontsize=10.5, color=MUTED)
        else:
            ax.set_xticklabels([])
        for label in ax.get_yticklabels():
            label.set_fontproperties(font)
            label.set_fontsize(10)


def _estimate_boundary(ax, axis: _Axis, rows: List[Dict[str, Any]]) -> None:
    xs = [axis.qx(r["display_quarter"]) for r in rows if r["kind"] == "estimate"]
    if xs:
        boundary = min(xs) - 0.5
        ax.axvspan(boundary, len(axis.periods), color="#EAF3F0", alpha=0.45, zorder=0)
        ax.axvline(boundary, color=GREEN, linestyle="--", linewidth=1.1, alpha=0.8)


def _plot_price(ax, axis: _Axis, history: Dict[str, Any], font) -> None:
    daily = history["price"]["daily"]
    days = pd.to_datetime([d for d, _ in daily])
    closes = np.array([c for _, c in daily], dtype=float)
    xs = [axis.dx(d) for d in days]
    ax.plot(xs, closes, color=BLUE, linewidth=2.0)
    ax.fill_between(xs, closes, 0, color=BLUE, alpha=0.08)
    span = closes.max() - 0 if len(closes) else 1
    for q in history["price"]["quarters"]:
        x = axis.dx(pd.Timestamp(q["end_date"]))
        ret = q["return_pct"]
        ax.scatter([x], [q["end_close"]], s=26, color="white", edgecolor=BLUE, linewidth=1.3, zorder=4)
        if ret is None:
            continue
        mark = "".join({"partial": "*", "QTD": " QTD"}.get(f, "") for f in q["flags"])
        ax.annotate(f"{ret:+.0f}%{mark}", (x, q["end_close"]),
                    xytext=(0, 9 if ret >= 0 else -11), textcoords="offset points",
                    ha="center", va="bottom" if ret >= 0 else "top",
                    fontproperties=font, fontsize=10, color=GREEN if ret >= 0 else RED,
                    weight="semibold")
    if len(closes):
        ax.set_ylim(0, closes.max() + span * 0.16)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))


def _bar_panel(ax, axis: _Axis, rows: List[Dict[str, Any]], font, *, metric: str, currency) -> None:
    """Revenue or net income QoQ bars with amount labels; n/a stays visibly empty."""
    is_rev = metric == "revenue"
    qoq_key = "revenue_qoq" if is_rev else "net_income_qoq"
    clip = REVENUE_QOQ_CLIP if is_rev else INCOME_QOQ_CLIP
    pos_a, pos_e = (GREEN, GREEN_EST) if is_rev else (PURPLE, PURPLE_EST)
    raw = [r[qoq_key] for r in rows]
    plotted = [None if v is None else float(np.clip(v, -clip, clip)) for v in raw]
    finite = [v for v in plotted if v is not None] or [0.0]
    top, bottom = max(max(finite), 0.0), min(min(finite), 0.0)
    span = max(top - bottom, 40.0)
    for r, v, pv in zip(rows, raw, plotted):
        x = axis.qx(r["display_quarter"])
        est = r["kind"] == "estimate"
        amount = _money(r[metric], r.get("currency") or currency)
        if pv is not None:
            color = (pos_e if est else pos_a) if v >= 0 else (RED_EST if est else RED)
            edge = pos_a if v >= 0 else RED
            ax.bar(x, pv, width=0.64, color=color, edgecolor=edge if est else color,
                   hatch="///" if est else None, linewidth=0.8, zorder=3)
            clipped = abs(v) > clip
            arrow = ("↑" if v > 0 else "↓") if clipped else ""
            head = f"{v:+.0f}%{arrow}" if abs(v) >= 100 else f"{v:+.1f}%{arrow}"
            y = pv + (span * 0.03 if v >= 0 else -span * 0.03)
            va = "bottom" if v >= 0 else "top"
            color_txt = pos_a if v >= 0 else RED
        else:
            if is_rev:
                head = "n/a\n" + _na(r["revenue_na_reason"])
                color_txt = GRAY
            else:
                kind = r["income_change_type"]
                pct = r.get("income_change_pct")
                head = {"loss_narrowed": f"亏损收窄\n{abs(pct or 0):.0f}%" if pct is not None else "",
                        "loss_widened": f"亏损扩大\n{abs(pct or 0):.0f}%" if pct is not None else "",
                        "turned_profit": "扭亏", "turned_loss": "转亏", "breakeven": "亏损归零"}.get(
                    kind, "n/a\n" + _na(kind))
                color_txt = {"loss_narrowed": PURPLE, "turned_profit": PURPLE, "breakeven": PURPLE,
                             "loss_widened": RED, "turned_loss": RED}.get(kind, GRAY)
                ax.scatter([x], [0], marker="D", s=24, color=color_txt, zorder=4)
            y, va = span * 0.03, "bottom"
        ax.text(x, y, f"{head}{' E' if est else ''}\n{amount}", ha="center", va=va,
                fontproperties=font, fontsize=9.6, color=color_txt, zorder=5, linespacing=1.15)
    ax.set_ylim(bottom - span * 0.30, top + span * 0.34)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))


def render_financial_history_png(history: Dict[str, Any], path: Path) -> Path:
    font = pick_font()
    plt.rcParams["axes.unicode_minus"] = False
    rows = [r for r in history["periods"] if r.get("role") == "window"]
    if not rows or not history["price"]["daily"]:
        raise ValueError("冻结数据缺财季或股价，无法绘图")
    currency = history.get("currency")
    last_q = max(max(pd.Period(r["display_quarter"], "Q") for r in rows),
                 pd.Timestamp(history["as_of"]).to_period("Q"))
    axis = _Axis(pd.Period(history["window_start_quarter"], "Q"), last_q)

    fig = plt.figure(figsize=(20, 13.5), dpi=170, facecolor=BG)
    gs = fig.add_gridspec(3, 1, height_ratios=[1.0, 1.0, 1.1], left=0.055, right=0.985,
                          bottom=0.13, top=0.80, hspace=0.24)
    ax_price, ax_rev, ax_inc = (fig.add_subplot(gs[i]) for i in range(3))

    sym = history["symbol"]
    snap = history.get("estimate_snapshot") or {}
    latest = history.get("latest_reported_period") or {}
    price = history["price"]
    fig.text(0.055, 0.962, f"{sym}：{history['years']}年股价、季度营收/净利润 QoQ 与未来{history['forecast_quarters']}季共识",
             fontproperties=font, fontsize=26, color=TEXT, weight="semibold")
    status = {"complete": "", "partial": " · 注意 partial：有数据缺口（见脚注）"}.get(history["status"], "")
    fig.text(0.055, 0.932,
             f"股价截至 {price['price_as_of']} · 最新财报 FY{latest.get('fiscal_year')} {latest.get('fiscal_quarter')}"
             f"（期末 {latest.get('period_end')}，公布 {latest.get('announced_at') or '—'}）"
             f" · 预测快照 {snap.get('snapshot_date', '无')}（{'周频库' if snap.get('snapshot_kind') == 'weekly' else '实时' if snap else '—'}）"
             f" · 实际实色 / 预测斜纹{status}",
             fontproperties=font, fontsize=12.5, color=RED if history["status"] != "complete" else MUTED)

    estimates = [r for r in rows if r["kind"] == "estimate"]
    actuals = [r for r in rows if r["kind"] == "actual"]
    cards = [("最新股价", f"${price['daily'][-1][1]:,.2f}", BLUE)]
    if price.get("change"):
        cards.append((price["change"]["label"], f"{price['change']['return_pct']:+.1f}%", BLUE))
    if actuals:
        la = actuals[-1]
        q = f"{la['revenue_qoq']:+.1f}% | " if la["revenue_qoq"] is not None else ""
        cards.append((f"最新季营收 FY{la['fiscal_year']} {la['fiscal_quarter']}", q + _money(la["revenue"], currency), GREEN))
    full = len(estimates) == history["forecast_quarters"]
    if full and all(r["revenue"] is not None for r in estimates):
        cards.append((f"未来{len(estimates)}季营收共识合计", _money(sum(r["revenue"] for r in estimates), currency), GREEN))
    else:
        cards.append(("未来预测", f"仅 {len(estimates)}/{history['forecast_quarters']} 季，不求和", RED))
    if full and all(r["net_income"] is not None for r in estimates):
        total = sum(r["net_income"] for r in estimates)
        cards.append((f"未来{len(estimates)}季净利润共识（口径未核实）", _money(total, currency), PURPLE if total >= 0 else RED))
    for i, (label, value, color) in enumerate(cards):
        x = 0.055 + i * 0.19
        fig.text(x, 0.883, label, fontproperties=font, fontsize=11.5, color=color, weight="semibold")
        fig.text(x, 0.852, value, fontproperties=font, fontsize=15, color=color, weight="semibold")

    _plot_price(ax_price, axis, history, font)
    axis.style(ax_price, font, labels=False)
    _bar_panel(ax_rev, axis, rows, font, metric="revenue", currency=currency)
    axis.style(ax_rev, font, labels=False, zero_line=True)
    _bar_panel(ax_inc, axis, rows, font, metric="net_income", currency=currency)
    axis.style(ax_inc, font, labels=True, zero_line=True)
    for ax in (ax_rev, ax_inc):
        _estimate_boundary(ax, axis, rows)

    rev_basis = "街口径净营收" if history.get("revenue_mode") == "street_net" else "报告营收"
    titles = [
        (ax_price, "股价（日线）——季度末标注当季价格收益（上季末→本季末，不含股息）", "股价（美元）"),
        (ax_rev, f"营收 QoQ（{rev_basis}）——柱顶同时标注当季金额", "营收 QoQ"),
        (ax_inc, "净利润 QoQ——历史 GAAP 实际；预测为分析师净利润共识（口径未核实，跨口径首季 n/a）", "净利润 QoQ"),
    ]
    for ax, title, ylabel in titles:
        ax.set_title(title, loc="left", fontproperties=font, fontsize=14, color=TEXT, pad=9, weight="semibold")
        ax.set_ylabel(ylabel, fontproperties=font, fontsize=11, color=MUTED)

    legend = [
        Patch(facecolor=GREEN, label="营收实际"),
        Patch(facecolor=GREEN_EST, edgecolor=GREEN, hatch="///", label="营收预测"),
        Patch(facecolor=PURPLE, label="净利润实际"),
        Patch(facecolor=PURPLE_EST, edgecolor=PURPLE, hatch="///", label="净利润预测"),
        Patch(facecolor=RED, label="负增长"),
    ]
    fig.legend(handles=legend, loc="upper right", bbox_to_anchor=(0.985, 0.975), ncol=5,
               frameon=False, prop=font, fontsize=11)

    shifted = any(r.get("display_shifted") for r in rows)
    notes = [
        "* 价格收益：上季度末收盘→本季度末收盘，仅价格不含股息；*=partial（无上季收盘：上市/窗口起点/停牌），QTD=当季未结束。"
        "财季按期末所在自然季度归位（非重编自然季度财报）" + ("，52/53 周期末跨界已回拨" if shifted else "") + "。",
        f"* 净利润：两季均盈利才画 QoQ 柱；均亏损标亏损收窄/扩大（按绝对亏损额，菱形标记）；跨盈亏标扭亏/转亏；"
        f"柱高截断在 ±{INCOME_QOQ_CLIP:.0f}%，箭头旁为真实值；n/a 标原因。财报期轴只用于趋势比较，不是事件日收益归因。",
        f"* 数据：历史营收 {', '.join(sorted({r.get('revenue_source') or '' for r in actuals} - {''}))}；"
        f"历史净利润 {', '.join(sorted({r.get('net_income_source') or '' for r in actuals} - {''}))}；"
        f"预测 {snap.get('source', '无')} {snap.get('snapshot_date', '')}；股价 {price['source']}。币种 {currency or '—'}。",
    ]
    problems = history["gaps"][:2] + history["warnings"][:1]
    if problems:
        notes.append("* 警示：" + "；".join(p[:90] for p in problems) + ("……详见 financial_history.md" if len(history["gaps"]) + len(history["warnings"]) > 3 else ""))
    for i, note in enumerate(notes):
        fig.text(0.055, 0.075 - i * 0.021, note, fontproperties=font, fontsize=10.2,
                 color=RED if note.startswith("* 警示") else MUTED)

    path = Path(path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fig.savefig(path, dpi=170, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    missing = sorted({str(w.message) for w in caught if "missing from font" in str(w.message)})
    if missing:
        raise RuntimeError("图中有字体缺字：" + "; ".join(missing)[:300])
    return path
