#!/usr/bin/env python3
"""Deterministic, zero-CDN light-theme HTML report renderer for Technical Analysis (Daily & Single).

Usage:
  python3 render_ta_html.py --input payload.json --output "/Users/owen/CC workspace/Finance/notes/ta/YYYY-MM-DD_....html"
  cat payload.json | python3 render_ta_html.py --output "/Users/owen/CC workspace/Finance/notes/ta/YYYY-MM-DD_....html"
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path
from typing import Any


def esc(val: Any, default: str = "—") -> str:
    if val is None or val == "":
        return default
    return html.escape(str(val), quote=True)


def fmt_num(val: Any, digits: int = 2, prefix: str = "", suffix: str = "", signed: bool = False) -> str:
    if val is None or val == "":
        return "—"
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return "—"
        sign = "+" if (signed and v > 0) else ""
        if abs(v) >= 1000 and digits <= 2:
            return f"{prefix}{sign}{v:,.{digits}f}{suffix}"
        return f"{prefix}{sign}{v:.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return esc(val)


def fmt_vol(val: Any, unit: str = "") -> str:
    if val is None:
        return "—"
    try:
        v = float(val)
        u = f" {unit}" if unit else ""
        if abs(v) >= 1e8:
            return f"{v / 1e8:.2f}亿{u}"
        if abs(v) >= 1e4:
            return f"{v / 1e4:,.1f}万{u}"
        return f"{v:,.2f}{u}"
    except (TypeError, ValueError):
        return esc(val)


def pct_color(pct: float | None, invert: bool = False) -> str:
    if pct is None:
        return "#64748b"
    v = (100.0 - pct) if invert else pct
    if v >= 85:
        return "#c23028"
    if v >= 65:
        return "#b86e00"
    if v <= 15:
        return "#167c47"
    return "#0f5ca8"


def regime_meta(code: Any) -> tuple[str, str, str]:
    mapping = {
        -3: ("-3 恐慌急杀 · 慎接飞刀", "#c23028", "#fdf1f0"),
        -2: ("-2 减速超跌 · 观察企稳", "#c25700", "#fff4e8"),
        -1: ("-1 超跌警戒 · 弱势下探", "#a16207", "#fef8ec"),
        0: ("0 常态区间", "#475569", "#f1f5f9"),
        1: ("+1 急杀回踩 · 假极端", "#0f5ca8", "#eef4fc"),
        2: ("+2 慢牛钝化 · 禁逆势", "#6d28d9", "#f5f3ff"),
        3: ("+3 喷发过热 · 止盈警戒", "#c23028", "#fdf1f0"),
    }
    try:
        c = int(code)
        return mapping.get(c, (f"{code}", "#475569", "#f1f5f9"))
    except (TypeError, ValueError):
        return (esc(code), "#475569", "#f1f5f9")


def resonance_badge(code: Any, prefix: str = "") -> str:
    p = f"{prefix} " if prefix else ""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return f'<span class="pill pill-muted">{esc(p)}{esc(code)}</span>'
    if c == 1:
        return f'<span class="pill pill-bull">{esc(p)}+1 双多共振</span>'
    if c == -1:
        return f'<span class="pill pill-bear">{esc(p)}-1 双空共振</span>'
    return f'<span class="pill pill-warn">{esc(p)}0 信号分歧</span>'


def svg_thermometer(pct: float | None, invert: bool = False) -> str:
    if pct is None:
        return '<div class="muted" style="font-size:12px;">2Y 分位不可用</div>'
    p = max(0.0, min(100.0, float(pct)))
    col = pct_color(p, invert=invert)
    x = 6 + p * 2.28
    gid = f"tg-{'inv' if invert else 'norm'}"
    return f"""
    <svg viewBox="0 0 240 26" class="thermo-svg" role="img" aria-label="2Y percentile {p:.1f}%">
      <defs>
        <linearGradient id="{gid}" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="{'#c23028' if invert else '#167c47'}"/>
          <stop offset="50%" stop-color="#d97706"/>
          <stop offset="100%" stop-color="{'#167c47' if invert else '#c23028'}"/>
        </linearGradient>
      </defs>
      <rect x="6" y="10" width="228" height="7" rx="3.5" fill="url(#{gid})" opacity="0.22"/>
      <rect x="6" y="10" width="{max(4.0, p * 2.28):.1f}" height="7" rx="3.5" fill="{col}" opacity="0.88"/>
      <line x1="63" y1="8" x2="63" y2="19" stroke="#cbd5e1" stroke-width="1"/>
      <line x1="120" y1="8" x2="120" y2="19" stroke="#cbd5e1" stroke-width="1"/>
      <line x1="177" y1="8" x2="177" y2="19" stroke="#cbd5e1" stroke-width="1"/>
      <circle cx="{x:.1f}" cy="13.5" r="5" fill="#ffffff" stroke="{col}" stroke-width="2.5"/>
    </svg>"""


def svg_price_ruler(sym: dict[str, Any]) -> str:
    trend = sym.get("trend") or {}
    close = sym.get("close")
    st_stop = trend.get("st_stop")
    cow_low = trend.get("cow_lower")
    cow_mid = trend.get("cow_mid")
    cow_up = trend.get("cow_upper")
    ema20 = (sym.get("position_mgmt") or {}).get("ema20")

    pts: list[tuple[str, float, str]] = []
    if close is not None:
        pts.append(("现价", float(close), "#151b26"))
    if st_stop is not None:
        pts.append(("ST止损", float(st_stop), "#c23028" if trend.get("st_direction") == "short" else "#167c47"))
    if cow_low is not None:
        pts.append(("通道下沿", float(cow_low), "#0f5ca8"))
    if cow_mid is not None:
        pts.append(("EMA30", float(cow_mid), "#5a6578"))
    if cow_up is not None:
        pts.append(("通道上沿", float(cow_up), "#0f5ca8"))
    if ema20 is not None:
        pts.append(("EMA20", float(ema20), "#b86e00"))

    if len(pts) < 2:
        return '<div class="chart-empty">价格标尺数据不足</div>'

    vals = [p[1] for p in pts]
    lo, hi = min(vals), max(vals)
    span = max(hi - lo, abs(hi) * 0.01, 1e-6)
    lo -= span * 0.12
    hi += span * 0.12
    span = hi - lo

    def px(v: float) -> float:
        return 18.0 + (v - lo) / span * 264.0

    band = ""
    if cow_low is not None and cow_up is not None:
        x1, x2 = px(float(cow_low)), px(float(cow_up))
        band = f'<rect x="{min(x1,x2):.1f}" y="38" width="{abs(x2-x1):.1f}" height="12" rx="4" fill="#0f5ca8" opacity="0.14"/>'

    markers = []
    sorted_pts = sorted(pts, key=lambda item: item[1])
    last_top_x = -999.0
    last_bot_x = -999.0
    for idx, (label, val, color) in enumerate(sorted_pts):
        x = px(val)
        is_close = label == "现价"
        place_top = is_close or (idx % 2 == 0)
        if place_top and abs(x - last_top_x) < 48:
            place_top = False
        elif not place_top and abs(x - last_bot_x) < 48:
            place_top = True
        if place_top:
            last_top_x = x
            ly, vy = 22, 11
            line_y1, line_y2 = 26, 44
        else:
            last_bot_x = x
            ly, vy = 70, 81
            line_y1, line_y2 = 44, 60

        anchor = "middle"
        if x < 42:
            anchor = "start"
        elif x > 258:
            anchor = "end"

        val_str = f"{val:,.0f}" if abs(val) >= 1000 else f"{val:.2f}"
        if is_close:
            markers.append(
                f'<line x1="{x:.1f}" y1="{line_y1}" x2="{x:.1f}" y2="{line_y2}" stroke="#151b26" stroke-width="1.6"/>'
                f'<circle cx="{x:.1f}" cy="44" r="5.5" fill="#151b26" stroke="#ffffff" stroke-width="2"/>'
                f'<text x="{x:.1f}" y="{ly}" text-anchor="{anchor}" fill="#151b26" font-size="9.5" font-weight="700">{label}</text>'
                f'<text x="{x:.1f}" y="{vy}" text-anchor="{anchor}" fill="#334155" font-size="9" font-weight="600">{val_str}</text>'
            )
        else:
            markers.append(
                f'<line x1="{x:.1f}" y1="{line_y1}" x2="{x:.1f}" y2="{line_y2}" stroke="{color}" stroke-width="1.3" stroke-dasharray="2,2"/>'
                f'<circle cx="{x:.1f}" cy="44" r="3.5" fill="{color}"/>'
                f'<text x="{x:.1f}" y="{ly}" text-anchor="{anchor}" fill="{color}" font-size="9" font-weight="600">{label}</text>'
                f'<text x="{x:.1f}" y="{vy}" text-anchor="{anchor}" fill="#5a6578" font-size="8.5">{val_str}</text>'
            )

    return f"""
    <div class="mini-chart">
      <div class="mini-chart-title">价格 vs 趋势通道 / ST 止损标尺</div>
      <svg viewBox="0 0 300 90" width="100%">
        <rect x="18" y="42" width="264" height="4" rx="2" fill="#e2e8f0"/>
        {band}
        {''.join(markers)}
      </svg>
    </div>"""


def svg_extremity_gauge(sym: dict[str, Any]) -> str:
    ext = sym.get("extremity") or {}
    z_sig = ext.get("z_sigma")
    s_ext = ext.get("s_pct_extreme")
    if z_sig is None:
        return '<div class="chart-empty">极端度数据不可用</div>'

    z = float(z_sig)
    z_clamped = max(-3.0, min(3.0, z))
    zx = 18.0 + (z_clamped + 3.0) / 6.0 * 264.0
    z_col = "#c23028" if abs(z) >= 2.0 else ("#b86e00" if abs(z) >= 1.5 else "#0f5ca8")

    s_svg = ""
    if s_ext is not None:
        s = float(s_ext)
        s_clamped = max(-150.0, min(150.0, s))
        mid_x = 150.0
        sx = mid_x + (s_clamped / 150.0) * 132.0
        s_col = "#167c47" if s >= 0 else "#c23028"
        bx = min(mid_x, sx)
        bw = max(2.0, abs(sx - mid_x))
        s_svg = f"""
        <text x="18" y="64" fill="#5a6578" font-size="9">5日速度 S 占极值</text>
        <text x="282" y="64" text-anchor="end" fill="{s_col}" font-size="9.5" font-weight="700">{s:+.1f}%</text>
        <rect x="18" y="70" width="264" height="7" rx="3.5" fill="#e8eef6"/>
        <line x1="150" y1="68" x2="150" y2="79" stroke="#94a3b8" stroke-width="1"/>
        <rect x="{bx:.1f}" y="70" width="{bw:.1f}" height="7" rx="3" fill="{s_col}"/>"""

    def sx_pos(sigma: float) -> float:
        return 18.0 + (sigma + 3.0) / 6.0 * 264.0

    x_m2, x_m15, x_0, x_p15, x_p2 = sx_pos(-2), sx_pos(-1.5), sx_pos(0), sx_pos(1.5), sx_pos(2)
    return f"""
    <div class="mini-chart">
      <div class="mini-chart-title">双轴极端度位置 Z (σ) &amp; 5日速度 S</div>
      <svg viewBox="0 0 300 90" width="100%">
        <rect x="18" y="24" width="{x_m2-18:.1f}" height="10" rx="2" fill="#c23028" opacity="0.22"/>
        <rect x="{x_m2:.1f}" y="24" width="{x_m15-x_m2:.1f}" height="10" fill="#d97706" opacity="0.20"/>
        <rect x="{x_m15:.1f}" y="24" width="{x_p15-x_m15:.1f}" height="10" fill="#cbd5e1" opacity="0.55"/>
        <rect x="{x_p15:.1f}" y="24" width="{x_p2-x_p15:.1f}" height="10" fill="#d97706" opacity="0.20"/>
        <rect x="{x_p2:.1f}" y="24" width="{282-x_p2:.1f}" height="10" rx="2" fill="#c23028" opacity="0.22"/>
        <line x1="{x_0:.1f}" y1="21" x2="{x_0:.1f}" y2="37" stroke="#94a3b8" stroke-width="1"/>
        <text x="{x_m2:.1f}" y="47" text-anchor="middle" fill="#c23028" font-size="8.5">-2σ</text>
        <text x="{x_m15:.1f}" y="47" text-anchor="middle" fill="#b86e00" font-size="8.5">-1.5</text>
        <text x="{x_0:.1f}" y="47" text-anchor="middle" fill="#64748b" font-size="8.5">0</text>
        <text x="{x_p15:.1f}" y="47" text-anchor="middle" fill="#b86e00" font-size="8.5">+1.5</text>
        <text x="{x_p2:.1f}" y="47" text-anchor="middle" fill="#c23028" font-size="8.5">+2σ</text>
        <circle cx="{zx:.1f}" cy="29" r="5.5" fill="{z_col}" stroke="#ffffff" stroke-width="2"/>
        <text x="{zx:.1f}" y="15" text-anchor="middle" fill="{z_col}" font-size="10" font-weight="700">{z:+.2f}σ</text>
        {s_svg}
      </svg>
    </div>"""


def svg_volume_bars(sym: dict[str, Any]) -> str:
    vol = sym.get("volume") or {}
    bars = vol.get("bars_10") or []
    ma20 = vol.get("vol_ma20")
    thresh = vol.get("threshold_2sigma")
    if not bars:
        return '<div class="chart-empty">近 10 日量能序列未提供</div>'

    max_v = max([float(b.get("v") or 0) for b in bars] + [float(ma20 or 0), float(thresh or 0), 1.0]) * 1.12
    n = len(bars)
    left, right, top, bot = 12.0, 288.0, 12.0, 72.0
    h = bot - top
    slot = (right - left) / max(n, 1)
    bw = max(6.0, slot * 0.62)

    rects = []
    for i, b in enumerate(bars):
        v = float(b.get("v") or 0)
        bh = (v / max_v) * h
        x = left + i * slot + (slot - bw) / 2.0
        y = bot - bh
        surge = b.get("surge")
        if surge == "up":
            col = "#167c47"
        elif surge == "down":
            col = "#c23028"
        elif i == n - 1:
            col = "#0f5ca8"
        else:
            col = "#cbd5e1"
        dt = str(b.get("date") or "")[-5:]
        rects.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(1.5, bh):.1f}" rx="2" fill="{col}"/>')
        if i in (0, n // 2, n - 1):
            rects.append(f'<text x="{x + bw/2:.1f}" y="84" text-anchor="middle" fill="#64748b" font-size="8">{esc(dt)}</text>')

    lines = []
    if ma20 is not None:
        ym = bot - (float(ma20) / max_v) * h
        lines.append(f'<line x1="{left}" y1="{ym:.1f}" x2="{right}" y2="{ym:.1f}" stroke="#64748b" stroke-width="1" stroke-dasharray="3,2"/>')
    if thresh is not None:
        yt = bot - (float(thresh) / max_v) * h
        lines.append(
            f'<line x1="{left}" y1="{yt:.1f}" x2="{right}" y2="{yt:.1f}" stroke="#b86e00" stroke-width="1.2" stroke-dasharray="4,2"/>'
            f'<text x="{right}" y="{max(10.0, yt - 3):.1f}" text-anchor="end" fill="#b86e00" font-size="8" font-weight="600">+2σ 阈值</text>'
        )

    return f"""
    <div class="mini-chart">
      <div class="mini-chart-title">近 10 日成交量 vs Vol MA20 / +2σ 放量线</div>
      <svg viewBox="0 0 300 90" width="100%">
        {''.join(rects)}
        {''.join(lines)}
      </svg>
    </div>"""


def render_vol_env_section(vol_env: dict[str, Any], vol_summary: str) -> str:
    if not vol_env:
        return ""
    btc = vol_env.get("btc_vol") or {}
    fg = vol_env.get("us_fear_greed") or {}
    eq = vol_env.get("us_equity_vol") or {}
    vix = eq.get("vix") or {}
    vxn = eq.get("vxn") or {}
    qhv = eq.get("qqq_hv20") or {}
    semi = vol_env.get("semi_vol") or {}

    def card_unavail(title: str, reason: str) -> str:
        return f'<div class="vol-card unavail"><div class="vol-label">{esc(title)}</div><div class="unavail-msg">⚠️ 不可用（{esc(reason or "数据源缺失")}）</div></div>'

    if btc.get("available", True) and btc.get("dvol") is not None:
        vrp = btc.get("vrp_dvol_minus_hv20")
        vrp_cls = "pill-bear" if (vrp is not None and vrp > 3) else ("pill-bull" if (vrp is not None and vrp < -3) else "pill-muted")
        vrp_txt = f"DVOL-HV20 {fmt_num(vrp, 2, signed=True)}"
        live_str = f" · 盘中 {fmt_num(btc.get('dvol_live_intraday'))}" if btc.get("dvol_live_intraday") is not None else ""
        c1 = f"""
        <div class="vol-card">
          <div class="vol-head"><span class="vol-label">BTC 隐波 (Deribit DVOL)</span><span class="pill {vrp_cls}">{vrp_txt}</span></div>
          <div class="vol-main">{fmt_num(btc.get('dvol'))} <span class="vol-delta">{fmt_num(btc.get('dvol_1d_chg'), signed=True)}</span></div>
          <div class="vol-sub">2Y 分位 <b>{fmt_num(btc.get('dvol_2y_pct'), 1, suffix='%')}</b>{live_str}</div>
          {svg_thermometer(btc.get('dvol_2y_pct'))}
          <div class="vol-foot">BTC HV20: {fmt_num(btc.get('hv20'))}（2Y {fmt_num(btc.get('hv20_2y_pct'), 1, suffix='%')}）· {esc(btc.get('as_of_date'))}</div>
        </div>"""
    else:
        c1 = card_unavail("BTC 隐波 (Deribit DVOL)", btc.get("reason", ""))

    if fg.get("available", True) and fg.get("score") is not None:
        c2 = f"""
        <div class="vol-card">
          <div class="vol-head"><span class="vol-label">美股恐慌 (CNN F&amp;G)</span><span class="pill pill-warn">{esc(fg.get('rating_cn') or fg.get('rating'))}</span></div>
          <div class="vol-main">{fmt_num(fg.get('score'), 1)} <span class="vol-delta">{fmt_num(fg.get('chg_1d'), 1, signed=True)}</span></div>
          <div class="vol-sub">2Y 分位 <b>{fmt_num(fg.get('pct_2y'), 1, suffix='%')}</b></div>
          {svg_thermometer(fg.get('pct_2y'), invert=True)}
          <div class="vol-foot">1周前 {fmt_num(fg.get('prev_1w'), 1)} · 1月前 {fmt_num(fg.get('prev_1m'), 1)} · {esc(fg.get('as_of_date'))}</div>
        </div>"""
    else:
        c2 = card_unavail("美股恐慌 (CNN F&G)", fg.get("reason", ""))

    if eq.get("available", True) and vix.get("value") is not None:
        vrp_eq = eq.get("vrp_vxn_minus_qqq_hv20")
        c3 = f"""
        <div class="vol-card">
          <div class="vol-head"><span class="vol-label">美股波动率 (VIX / VXN)</span><span class="pill pill-muted">VXN-HV20 {fmt_num(vrp_eq, 2, signed=True)}</span></div>
          <div class="vol-dual">
            <div><span class="muted">VIX</span> <b>{fmt_num(vix.get('value'))}</b> <span class="vol-delta">{fmt_num(vix.get('chg_1d'), signed=True)}</span> · <b>{fmt_num(vix.get('pct_2y'), 1, suffix='%')}</b></div>
            <div><span class="muted">VXN</span> <b>{fmt_num(vxn.get('value'))}</b> <span class="vol-delta">{fmt_num(vxn.get('chg_1d'), signed=True)}</span> · <b>{fmt_num(vxn.get('pct_2y'), 1, suffix='%')}</b></div>
          </div>
          {svg_thermometer(vxn.get('pct_2y'))}
          <div class="vol-foot">QQQ HV20: {fmt_num(qhv.get('value'))}（2Y {fmt_num(qhv.get('pct_2y'), 1, suffix='%')}）· VXN {esc(vxn.get('as_of_date'))}</div>
        </div>"""
    else:
        c3 = card_unavail("美股波动率 (VIX / VXN)", eq.get("reason", ""))

    if semi.get("available", True) and semi.get("hv20") is not None:
        atm = semi.get("atm_iv_30d")
        vrp_s = semi.get("vrp_atm_iv_minus_hv20")
        c4 = f"""
        <div class="vol-card">
          <div class="vol-head"><span class="vol-label">半导体波动率 (SOXX)</span><span class="pill pill-muted">IV-HV20 {fmt_num(vrp_s, 2, signed=True)}</span></div>
          <div class="vol-main">{fmt_num(atm, suffix='%')} <span class="vol-sub-inline">30D ATM IV</span></div>
          <div class="vol-sub">HV20 <b>{fmt_num(semi.get('hv20'))}</b>（{fmt_num(semi.get('hv20_1d_chg'), signed=True)}）· HV20 2Y <b>{fmt_num(semi.get('hv20_pct_2y'), 1, suffix='%')}</b></div>
          {svg_thermometer(semi.get('hv20_pct_2y'))}
          <div class="vol-foot">注：2Y 分位基于 HV20 · {esc(semi.get('as_of_date'))}</div>
        </div>"""
    else:
        c4 = card_unavail("半导体波动率 (SOXX)", semi.get("reason", ""))

    summary_html = f'<div class="callout">{esc(vol_summary)}</div>' if vol_summary else ""
    return f"""
    <section class="section">
      <h2>二、跨市场波动率与恐慌温度计（2Y 分位）</h2>
      <div class="vol-grid">{c1}{c2}{c3}{c4}</div>
      {summary_html}
    </section>"""


def render_traders_section(traders: dict[str, Any]) -> str:
    if not traders:
        return ""
    cow = traders.get("bitcow") or {}
    yuren = traders.get("yuren") or {}

    def render_setup_list(items: list[dict[str, Any]]) -> str:
        out = []
        for it in items:
            tag = esc(it.get("setup") or "观望")
            tag_cls = "pill-bull" if any(k in tag for k in ("N1", "N2A", "N2B", "Y2")) else ("pill-bear" if any(k in tag for k in ("N3", "Y1", "Y3")) else "pill-muted")
            out.append(f"""
            <div class="setup-item">
              <div class="setup-top"><b>{esc(it.get('symbol'))}</b><span class="pill {tag_cls}">{tag}</span></div>
              <div class="setup-body">{esc(it.get('detail'))}</div>
              {f'<div class="setup-next">🎯 下一枪：{esc(it.get("next_trigger"))}</div>' if it.get("next_trigger") else ''}
            </div>""")
        return "".join(out)

    checks = "".join(f"<li>{esc(c)}</li>" for c in (cow.get("holding_checks") or []))
    levels = "".join(
        f'<span class="level-chip"><span class="muted">{esc(lv.get("label"))}</span> <b>{esc(lv.get("value"))}</b></span>'
        for lv in (cow.get("btc_key_levels") or [])
    )

    return f"""
    <section class="section">
      <h2>三、奶牛 &amp; 余人实战推演 + 持仓排查</h2>
      <div class="trader-grid">
        <div class="trader-card">
          <div class="trader-title">🐮 奶牛 (@Yanakz3) · 期权买方 + 持仓防回吐</div>
          <div class="trader-stance">{esc(cow.get('stance'))}</div>
          <div class="sub-label">如果你现在空仓</div>
          {render_setup_list(cow.get('flat_setups') or [])}
          {f'<div class="sub-label">如果你已有持仓（需人工核对持仓）</div><ul class="check-list">{checks}</ul>' if checks else ''}
          {f'<div class="sub-label">BTC 关键观察价位</div><div class="level-row">{levels}</div>' if levels else ''}
        </div>
        <div class="trader-card">
          <div class="trader-title">🗡️ 余人 (@yuren477) · 永续 / 正股重锤视角</div>
          <div class="trader-stance">{esc(yuren.get('stance'))}</div>
          <div class="sub-label">三标的逐一推演</div>
          {render_setup_list(yuren.get('setups') or [])}
        </div>
      </div>
    </section>"""


def render_symbol_card(sym: dict[str, Any], idx: int) -> str:
    t = sym.get("trend") or {}
    e = sym.get("extremity") or {}
    d = sym.get("original_d") or {}
    v = sym.get("volume") or {}
    c = sym.get("conclusions") or {}
    alerts = sym.get("new_alerts") or []

    reg_label, reg_col, reg_bg = regime_meta(e.get("regime_code"))
    chg = sym.get("change_pct")
    chg_cls = "pos" if (chg is not None and float(chg) >= 0) else "neg"

    alert_html = ""
    if alerts:
        items = "".join(f"<div>🚨 {esc(a)}</div>" for a in alerts)
        alert_html = f'<div class="sym-alert">{items}</div>'

    meta_note = f" · {esc(sym.get('bar_note'))}" if sym.get("bar_note") else ""
    base_rate = f"<li><b>条件历史基准率</b>：{esc(c.get('base_rate'))}</li>" if c.get("base_rate") else ""

    d_pct_str = fmt_num(d.get("d_2y_pct"), 2, suffix="%")
    d_tag = ""
    if d.get("d_2y_pct") is not None:
        if float(d["d_2y_pct"]) >= 90:
            d_tag = ' <span class="pill pill-bear">过热</span>'
        elif float(d["d_2y_pct"]) <= 10:
            d_tag = ' <span class="pill pill-warn">超跌</span>'

    unit = v.get("unit") or ""

    return f"""
    <article class="sym-card" id="sym-{esc(sym.get('ticker', idx))}">
      <div class="sym-header">
        <div>
          <span class="sym-name">{esc(sym.get('ticker'))}</span>
          <span class="sym-exch">{esc(sym.get('exchange_symbol'))} · {esc(sym.get('timeframe', '1D'))}</span>
          <div class="sym-date">最新收盘：{esc(sym.get('bar_date'))}{meta_note} · 数据源：TradingView MCP</div>
        </div>
        <div class="sym-price-box">
          <div class="sym-price">{fmt_num(sym.get('close'))} <span class="sym-chg {chg_cls}">{fmt_num(chg, 2, signed=True, suffix='%')}</span></div>
          <div class="sym-pills">
            {resonance_badge(t.get('resonance'), '1D')}
            {resonance_badge(sym.get('trend_1w_resonance'), '1W') if sym.get('trend_1w_resonance') is not None else ''}
            <span class="pill" style="color:{reg_col};background:{reg_bg};">{esc(reg_label)}</span>
          </div>
        </div>
      </div>

      {alert_html}

      <div class="summary-box">
        <ul>
          <li><b>趋势定性</b>：{esc(c.get('trend'))}</li>
          <li><b>极端定性</b>：{esc(c.get('extremity'))}</li>
          <li><b>量能印证</b>：{esc(c.get('volume'))}</li>
          <li><b>关键观察位与边界</b>：{esc(c.get('levels'))}</li>
          <li><b>1W 大级别趋势背景</b>：{esc(c.get('trend_1w') or '1W 背景：未拉取')}</li>
          {base_rate}
        </ul>
      </div>

      <div class="charts-grid">
        {svg_price_ruler(sym)}
        {svg_extremity_gauge(sym)}
        {svg_volume_bars(sym)}
      </div>

      <div class="panels-grid">
        <div class="panel-box">
          <div class="panel-title">板块一：趋势（ST + 奶牛通道面板）</div>
          <table>
            <tr><td>共振状态</td><td>{resonance_badge(t.get('resonance'))}</td></tr>
            <tr><td>Supertrend (14, 2)</td><td>{esc(t.get('st_status'))}</td></tr>
            <tr><td>ST 收盘逆转价</td><td><b>{esc(t.get('st_flip_text'))}</b></td></tr>
            <tr><td>奶牛通道 (EMA30±1ATR)</td><td>{esc(t.get('cow_direction_text'))}</td></tr>
            <tr><td>奶牛临界 (上/下沿)</td><td>上沿 {fmt_num(t.get('cow_upper'))} / 下沿 {fmt_num(t.get('cow_lower'))}（中枢 {fmt_num(t.get('cow_mid'))}）</td></tr>
            <tr><td>近 10 根翻向事件</td><td>{esc(t.get('recent_flips') or '无新翻向')}</td></tr>
          </table>
        </div>

        <div class="panel-box">
          <div class="panel-title">板块二：极端（Extremity Z+S + 原版偏离 D）</div>
          <table>
            <tr><td>盘面定性</td><td><span style="color:{reg_col};font-weight:700;">{esc(reg_label)}</span></td></tr>
            <tr><td>位置 Z</td><td><b>{fmt_num(e.get('z_val'))} ({fmt_num(e.get('z_sigma'), signed=True, suffix='σ')})</b><div class="sub-td">中枢 {fmt_num(e.get('mid'), signed=True)} · ±1.5σ ({fmt_num(e.get('band_p15'), signed=True)} / {fmt_num(e.get('band_m15'), signed=True)}) · ±2σ ({fmt_num(e.get('band_p20'), signed=True)} / {fmt_num(e.get('band_m20'), signed=True)})</div></td></tr>
            <tr><td>Z 2年分位</td><td><b>{fmt_num(e.get('z_2y_pct'), 2, suffix='%')}</b></td></tr>
            <tr><td>5日速度 S</td><td>{fmt_num(e.get('s_val'), signed=True)}（{fmt_num(e.get('s_pct_extreme'), 2, signed=True, suffix='%极值')}）</td></tr>
            <tr><td>S 2年分位</td><td>{fmt_num(e.get('s_2y_pct'), 2, suffix='%')}</td></tr>
            <tr><td>近 10 根事件</td><td>{esc(e.get('recent_events') or '无')}</td></tr>
            <tr><td>原版偏离 D (EMA84)</td><td><b>{fmt_num(d.get('d_val'), signed=True)}</b></td></tr>
            <tr><td>D 历史分位 (2Y)</td><td><b>{d_pct_str}</b>{d_tag}</td></tr>
            <tr><td>2Y 极值 D (90%/10%)</td><td>{fmt_num(d.get('d_overheat'), signed=True)} / {fmt_num(d.get('d_oversold'), signed=True)}</td></tr>
          </table>
        </div>

        <div class="panel-box">
          <div class="panel-title">板块三：交易量（Vol + RVOL，120 / 2σ）</div>
          <table>
            <tr><td>当日成交量</td><td><b>{fmt_vol(v.get('vol'), unit)}</b></td></tr>
            <tr><td>Vol MA (20)</td><td>{fmt_vol(v.get('vol_ma20'), unit)}</td></tr>
            <tr><td>放量阈值线 (+2σ)</td><td>{fmt_vol(v.get('threshold_2sigma'), unit)}</td></tr>
            <tr><td>RVOL 倍数</td><td><b>{fmt_num(v.get('rvol'), 2, suffix='x')}</b></td></tr>
            <tr><td>量能 Z-Score (σ)</td><td><b>{fmt_num(v.get('z_score'), 2, signed=True, suffix='σ')}</b></td></tr>
            <tr><td>近 10 根放量异动</td><td>{esc(v.get('recent_events') or '无异动')}</td></tr>
          </table>
        </div>
      </div>
    </article>"""


def render_html(data: dict[str, Any]) -> str:
    mode = data.get("mode", "daily")
    title = data.get("title") or ("未来资本 · 每日三标的四维技术与期权晨报" if mode == "daily" else "未来资本 · 技术分析报告")
    report_date = data.get("report_date") or ""
    generated_at = data.get("generated_at") or ""
    holiday_note = data.get("holiday_note") or ""
    overview_bullets = data.get("overview_bullets") or []
    symbols = data.get("symbols") or []

    all_alerts = []
    for s in symbols:
        for a in (s.get("new_alerts") or []):
            all_alerts.append(f"{s.get('ticker')}: {a}")

    alert_banner = ""
    if all_alerts:
        items = "".join(f'<div class="banner-item">🚨 {esc(a)}</div>' for a in all_alerts)
        alert_banner = f'<div class="alert-banner"><div class="banner-title">当日新触发预警线</div>{items}</div>'

    scorecards = ""
    if mode == "daily" and symbols:
        cards = []
        for s in symbols:
            e = s.get("extremity") or {}
            d = s.get("original_d") or {}
            v = s.get("volume") or {}
            t = s.get("trend") or {}
            reg_l, reg_c, reg_bg = regime_meta(e.get("regime_code"))
            chg = s.get("change_pct")
            chg_cls = "pos" if (chg is not None and float(chg) >= 0) else "neg"
            has_alert = bool(s.get("new_alerts"))
            cards.append(f"""
            <a class="score-card {'has-alert' if has_alert else ''}" href="#sym-{esc(s.get('ticker'))}">
              <div class="score-top">
                <span class="score-sym">{esc(s.get('ticker'))} {'🚨' if has_alert else ''}</span>
                <span class="sym-chg {chg_cls}">{fmt_num(chg, 2, signed=True, suffix='%')}</span>
              </div>
              <div class="score-price">{fmt_num(s.get('close'))}</div>
              <div class="score-badges">
                {resonance_badge(t.get('resonance'))}
                <span class="pill" style="color:{reg_c};background:{reg_bg};">{esc(reg_l.split(' · ')[0])}</span>
              </div>
              <div class="score-metrics">
                <span>Z: <b>{fmt_num(e.get('z_sigma'), signed=True, suffix='σ')}</b></span>
                <span>D分位: <b>{fmt_num(d.get('d_2y_pct'), 1, suffix='%')}</b></span>
                <span>RVOL: <b>{fmt_num(v.get('rvol'), 2, suffix='x')}</b></span>
              </div>
            </a>""")
        scorecards = f'<div class="score-grid">{"".join(cards)}</div>'

    overview_sec = ""
    if overview_bullets or scorecards:
        bul = "".join(f"<li>{esc(b)}</li>" for b in overview_bullets)
        overview_sec = f"""
        <section class="section">
          <h2>一、三标的横向总览与跨资产联动结论</h2>
          {scorecards}
          {f'<ul class="overview-list">{bul}</ul>' if bul else ''}
        </section>"""

    vol_sec = render_vol_env_section(data.get("vol_env") or {}, data.get("vol_summary") or "")
    traders_sec = render_traders_section(data.get("traders") or {})
    sym_cards = "".join(render_symbol_card(s, i) for i, s in enumerate(symbols))
    sec4_title = "四、分标的完整三板块面板" if mode == "daily" else "三板块技术分析面板"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(title)} ({esc(report_date)})</title>
<style>
  :root {{
    --bg: #f7f8fa; --surface: #ffffff; --card: #ffffff; --card-alt: #f3f6fa; --border: #dce2ec;
    --text: #151b26; --muted: #5a6578; --bull: #167c47; --bear: #c23028; --warn: #b86e00; --accent: #0f5ca8;
    --bull-bg: #eef9f2; --bear-bg: #fdf1f0; --warn-bg: #fef8ec; --blue-bg: #eef4fc;
    --serif: "Songti SC", "Noto Serif SC", Georgia, serif;
    --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px 16px 56px; background: var(--bg); color: var(--text);
    font-family: var(--sans); font-size: 14.5px; line-height: 1.6; -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 1120px; margin: 0 auto; }}
  header {{
    background: var(--surface); border: 1px solid var(--border); border-top: 3px solid var(--text);
    border-radius: 12px; padding: 20px 24px; margin-bottom: 18px;
  }}
  h1 {{ margin: 0 0 6px; font-family: var(--serif); font-size: 24px; font-weight: 700; color: var(--text); }}
  .meta-line {{ color: var(--muted); font-size: 12.5px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
  .holiday-note {{
    margin-top: 10px; padding: 8px 12px; border-radius: 8px;
    background: var(--warn-bg); border: 1px solid #f0d599; color: #8a5300; font-size: 12.5px;
  }}
  .alert-banner {{
    margin-bottom: 18px; padding: 12px 16px; border-radius: 10px;
    background: var(--bear-bg); border: 1px solid #f1b7b4;
  }}
  .banner-title {{ font-size: 11.5px; font-weight: 700; color: var(--bear); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 4px; }}
  .banner-item {{ color: #8c1d18; font-weight: 600; font-size: 13.5px; }}
  .section {{ margin-bottom: 26px; }}
  h2 {{ font-size: 17px; font-weight: 700; color: var(--text); margin: 0 0 12px; padding-left: 10px; border-left: 3.5px solid var(--accent); }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 600; white-space: nowrap; }}
  .pill-bull {{ color: var(--bull); background: var(--bull-bg); }}
  .pill-bear {{ color: var(--bear); background: var(--bear-bg); }}
  .pill-warn {{ color: var(--warn); background: var(--warn-bg); }}
  .pill-muted {{ color: #475569; background: #eef2f7; }}
  .pos {{ color: var(--bull); }} .neg {{ color: var(--bear); }} .muted {{ color: var(--muted); }}
  .score-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 14px; }}
  .score-card {{
    display: block; text-decoration: none; color: inherit; background: var(--card);
    border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; transition: border-color .15s, box-shadow .15s;
  }}
  .score-card:hover {{ border-color: var(--accent); box-shadow: 0 2px 8px rgba(15,92,168,.08); }}
  .score-card.has-alert {{ border-color: #e59894; background: #fffcfc; }}
  .score-top {{ display: flex; justify-content: space-between; align-items: center; }}
  .score-sym {{ font-size: 16px; font-weight: 700; color: var(--text); }}
  .score-price {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; margin: 4px 0 8px; color: var(--text); }}
  .score-badges {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
  .score-metrics {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); padding-top: 8px; }}
  .score-metrics b {{ color: var(--text); }}
  .overview-list {{ margin: 0; padding: 14px 18px 14px 32px; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }}
  .overview-list li {{ margin-bottom: 6px; }}
  .vol-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
  .vol-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }}
  .vol-card.unavail {{ border-color: #f0d599; background: var(--warn-bg); }}
  .unavail-msg {{ color: var(--warn); margin-top: 8px; font-size: 13px; }}
  .vol-head {{ display: flex; justify-content: space-between; align-items: center; gap: 6px; margin-bottom: 6px; }}
  .vol-label {{ font-size: 12.5px; color: var(--muted); font-weight: 600; }}
  .vol-main {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text); }}
  .vol-delta {{ font-size: 12.5px; font-weight: 600; color: var(--muted); }}
  .vol-sub {{ font-size: 12.5px; color: #334155; margin: 2px 0 4px; }}
  .vol-sub-inline {{ font-size: 12px; font-weight: 500; color: var(--muted); }}
  .vol-dual {{ font-size: 13.5px; display: flex; flex-direction: column; gap: 3px; margin: 4px 0; font-variant-numeric: tabular-nums; }}
  .vol-foot {{ font-size: 11.5px; color: var(--muted); margin-top: 4px; }}
  .thermo-svg {{ width: 100%; height: 26px; display: block; }}
  .callout {{
    margin-top: 12px; padding: 11px 14px; border-radius: 10px;
    background: var(--blue-bg); border: 1px solid #c5daf4; color: #123c69; font-size: 13px;
  }}
  .trader-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }}
  .trader-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; }}
  .trader-title {{ font-size: 15px; font-weight: 700; color: var(--text); margin-bottom: 8px; }}
  .trader-stance {{ padding: 9px 12px; border-radius: 8px; background: var(--card-alt); border: 1px solid var(--border); font-size: 13px; margin-bottom: 12px; color: #243047; }}
  .sub-label {{ font-size: 12px; font-weight: 700; color: var(--accent); margin: 12px 0 6px; letter-spacing: .02em; }}
  .setup-item {{ padding: 10px 12px; border-radius: 9px; background: var(--card-alt); border: 1px solid var(--border); margin-bottom: 8px; }}
  .setup-top {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 4px; }}
  .setup-body {{ font-size: 13px; color: #334155; }}
  .setup-next {{ margin-top: 5px; font-size: 12px; color: #9a5700; font-weight: 600; }}
  .check-list {{ margin: 0; padding-left: 18px; font-size: 12.5px; color: #334155; }}
  .check-list li {{ margin-bottom: 4px; }}
  .level-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .level-chip {{ padding: 4px 10px; border-radius: 8px; background: var(--card-alt); border: 1px solid var(--border); font-size: 12px; }}
  .sym-card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
    padding: 18px 20px; margin-bottom: 18px;
  }}
  .sym-header {{ display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 12px; }}
  .sym-name {{ font-size: 20px; font-weight: 800; color: var(--text); margin-right: 8px; }}
  .sym-exch {{ font-size: 12.5px; color: var(--muted); }}
  .sym-date {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}
  .sym-price-box {{ text-align: right; }}
  .sym-price {{ font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--text); }}
  .sym-chg {{ font-size: 14px; font-weight: 700; margin-left: 6px; }}
  .sym-pills {{ display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; margin-top: 6px; }}
  .sym-alert {{
    margin-bottom: 12px; padding: 9px 12px; border-radius: 9px;
    background: var(--bear-bg); border: 1px solid #f1b7b4; color: #8c1d18; font-weight: 600; font-size: 13px;
  }}
  .summary-box {{ background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; margin-bottom: 14px; }}
  .summary-box ul {{ margin: 0; padding-left: 18px; }}
  .summary-box li {{ margin-bottom: 4px; font-size: 13px; }}
  .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-bottom: 14px; }}
  .mini-chart {{ background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }}
  .mini-chart-title {{ font-size: 11.5px; color: var(--muted); font-weight: 600; margin-bottom: 4px; }}
  .chart-empty {{ background: var(--card-alt); border: 1px dashed var(--border); border-radius: 10px; padding: 24px 12px; text-align: center; color: var(--muted); font-size: 12px; }}
  .panels-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
  .panel-box {{ background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }}
  .panel-title {{ font-size: 12.5px; font-weight: 700; color: var(--accent); margin-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  td {{ padding: 5px 4px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  td:first-child {{ color: var(--muted); width: 42%; padding-right: 8px; }}
  td:last-child {{ color: var(--text); font-variant-numeric: tabular-nums; }}
  .sub-td {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  footer {{ margin-top: 28px; text-align: center; color: var(--muted); font-size: 12px; border-top: 1px solid var(--border); padding-top: 14px; }}
  @media (max-width: 640px) {{
    body {{ padding: 12px 10px 36px; }}
    .sym-price-box {{ text-align: left; }}
    .sym-pills {{ justify-content: flex-start; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{esc(title)}</h1>
    <div class="meta-line">
      <span>📅 报告日期：{esc(report_date)}</span>
      {f'<span>🕒 生成时间：{esc(generated_at)}</span>' if generated_at else ''}
      <span>📡 数据源：TradingView MCP{'' if mode == 'single' else ' + 跨市场波动率采集'}</span>
    </div>
    {f'<div class="holiday-note">ℹ️ {esc(holiday_note)}</div>' if holiday_note else ''}
  </header>

  {alert_banner}
  {overview_sec}
  {vol_sec}
  {traders_sec}

  <section class="section">
    <h2>{esc(sec4_title)}</h2>
    {sym_cards}
  </section>

  <footer>
    以上为量化信号与盘口结构分析，不构成投资建议 · 未来资本技术分析系统
  </footer>
</div>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render deterministic zero-CDN light-theme Technical Analysis HTML report")
    parser.add_argument("--input", "-i", default="-", help="Input JSON path (default: stdin)")
    parser.add_argument("--output", "-o", required=True, help="Output HTML file path")
    args = parser.parse_args()

    if args.input == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.input).read_text(encoding="utf-8")

    data = json.loads(raw)
    out_html = render_html(data)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_html, encoding="utf-8")
    print(f"OK: {out_path} ({len(out_html)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
