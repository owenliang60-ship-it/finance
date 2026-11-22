"""Valuation engine: the single merge point of facts, ledger and gate state into the page payload.

Every number goes through kernel.py (mirrored in assets/kernel.js); missing values stay None.
Raw fact values are echoed unchanged, so a NaN in the facts fails json.dumps(allow_nan=False)
instead of being silently treated as missing. Sums use explicit loops (see kernel.py).
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict
from datetime import date
from typing import TYPE_CHECKING, Optional, Sequence

from terminal.prosperity.config import ESTIMATE_MATCH_DAYS
from terminal.prosperity.consensus import ttm_eps
from terminal.valuation import kernel as k
from terminal.valuation.config import (CONFLICT, MILD_ZONE_SIGMAS, PE_LEVELS, REV_DELTA_MIN, SEG_ADJ_RANGE,
                                       SURPRISE_MIN_QUARTERS, SURPRISE_QUARTERS, VERIFIED)
from terminal.valuation.facts import FactPack
from terminal.valuation.ledger import Ledger

if TYPE_CHECKING:
    from terminal.valuation.gates import GateSummary

QUARTER_GAP = (60, 120)       # adjacent fiscal quarters (days), same rule as prosperity consensus
PRIOR_YEAR_DAYS = 365
DISCLAIMER = "本页呈现事实反推、台账登记的参考情景预设与你输入的情景演算；参考情景与权重是可覆盖的假设，不是模型结论。"
NO_REPLAY = "样本内无可回放的深度下行周期"
CROWDING_NOTE = "拥挤度读数，非区间边界"
PALETTE = ("#4c78a8", "#f58518", "#54a24b", "#b279a2", "#9d755d", "#72b7b2")   # 计划 v2 §8 分部缺省调色板
FORMULA_NOTE = ("按最新已核实季报占比切分卖方 NTM 总营收；库内无分部级卖方共识，此为等比切分，不是分部预测，"
                "可在控制台独立调整。")
TRAJECTORY_NOTE = "未来 4 季 = 卖方季度形状 × 情景 NTM 总额比例，不含季度节奏假设"
CONSENSUS_BAND_NOTE = "卖方 EPS 低/高值 × 现价隐含 P/E，拥挤度读数"
CONSENSUS_THESIS = "初始态：卖方 NTM 共识营收、共识隐含净利率、现价隐含 P/E"
LEVEL_FALLBACK = (0.5, 0.75, 1.0, 1.25, 1.5)


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days


def _contiguous_break(dates: Sequence[str]) -> Optional[str]:
    lo, hi = QUARTER_GAP
    for a, b in zip(dates, dates[1:]):
        if not lo <= _days(b, a) <= hi:
            return f"{a}→{b}"
    return None


def _total(values) -> float:
    total = 0.0
    for v in values:
        total += v
    return total


def quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    n = len(sorted_values)
    if n == 0:
        return None
    pos = (n - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def pe_history(closes, eps) -> dict:
    by_announce = sorted(eps, key=lambda q: q.announce_date)
    values, n_nonpositive, n_no_ttm = [], 0, 0
    i, seen, ttm = 0, -1, None
    for day, close in closes:
        while i < len(by_announce) and by_announce[i].announce_date <= day[:10]:
            i += 1
        if i != seen:
            ttm, seen = ttm_eps(by_announce[:i])[0], i
        if ttm is None:
            n_no_ttm += 1
        elif ttm <= 0:
            n_nonpositive += 1
        else:
            pe = k.implied_pe(close, ttm)
            if pe is not None:
                values.append(pe)
    return {"sorted": sorted(values), "start": closes[0][0] if closes else None,
            "end": closes[-1][0] if closes else None, "n_valid": len(values),
            "n_nonpositive": n_nonpositive, "n_no_ttm": n_no_ttm}


def _hist_margin(facts: FactPack, ledger: Ledger) -> dict:
    last8 = [q for q in facts.eps if q.announce_date <= facts.as_of][-8:]
    if len(last8) < 8:
        return {"value": None, "detail": "已公告季不足 8 个"}
    if any(k.fin(q.eps_actual) is None or not q.statement_fiscal for q in last8):
        return {"value": None, "detail": "近 8 季有街口 EPS 为空或未配对报表季"}
    gap = _contiguous_break([q.period_end for q in last8])
    if gap:
        return {"value": None, "detail": f"近 8 季不连续：{gap}"}
    income = {r["date"][:10]: r for r in facts.income}
    ni, rev = [], []
    for q in last8:
        row = income.get(q.statement_fiscal[:10], {})
        r = k.fin(row.get("revenue"))
        if ledger.eps_basis == "non_gaap":
            shares = k.fin(row.get("weighted_average_shs_out_dil"))
            n = k.fin(q.eps_actual * shares) if shares is not None else None
        else:
            n = k.fin(row.get("net_income"))
        if r is None or n is None:
            return {"value": None, "detail": f"{q.statement_fiscal} 缺营收或净利"}
        ni.append(n)
        rev.append(r)
    rev_a, d_rev = _total(rev[:4]), _total(rev[4:]) - _total(rev[:4])
    if rev_a <= 0 or abs(d_rev) < REV_DELTA_MIN * rev_a:
        return {"value": None, "detail": "近 4 季对前 4 季营收变化不足 1%"}
    basis = "街口 EPS × 摊薄股本" if ledger.eps_basis == "non_gaap" else "报表净利"
    return {"value": k.fin((_total(ni[4:]) - _total(ni[:4])) / d_rev),
            "detail": f"{last8[0].statement_fiscal}–{last8[-1].statement_fiscal}，{basis}，后 4 季对前 4 季"}


def _street_margin(facts: FactPack) -> dict:
    if facts.fy_pair is None:
        return {"value": None, "detail": "缺 FY1/FY2 共识"}
    fy1, fy2 = facts.fy_pair
    ni1, ni2 = k.fin(fy1.get("net_income_avg")), k.fin(fy2.get("net_income_avg"))
    rev1, rev2 = k.fin(fy1.get("rev_avg")), k.fin(fy2.get("rev_avg"))
    if None in (ni1, ni2, rev1, rev2):
        return {"value": None, "detail": "FY1/FY2 共识缺净利或营收"}
    if rev1 <= 0 or abs(rev2 - rev1) < REV_DELTA_MIN * rev1:
        return {"value": None, "detail": "FY2 对 FY1 营收变化不足 1%"}
    return {"value": k.fin((ni2 - ni1) / (rev2 - rev1)),
            "detail": f"FY {fy1['fiscal_date'][:10]} → {fy2['fiscal_date'][:10]} 共识 ΔNI/ΔRev"}


def incremental_margins(facts: FactPack, ledger: Ledger) -> dict:
    boss = ledger.boss_margin
    return {"hist": _hist_margin(facts, ledger), "street": _street_margin(facts),
            "boss": {"value": boss, "detail": "台账 [assumptions].incremental_margin" if boss is not None
                     else "台账未设"}}


def mild_zone(facts: FactPack) -> dict:
    rows = [r for r in facts.earnings if r.get("match_method") != "none" and r["announce_date"][:10] <= facts.as_of
            and (k.fin(r.get("revenue_actual")) or 0) > 0 and (k.fin(r.get("revenue_estimated")) or 0) > 0]
    rows = sorted(rows, key=lambda r: r["announce_date"])[-SURPRISE_QUARTERS:]
    surprises = [r["revenue_actual"] / r["revenue_estimated"] - 1 for r in rows]
    if len(surprises) < SURPRISE_MIN_QUARTERS:
        return {"sigma_q": None, "n": len(surprises), "half_width": None}
    sigma = statistics.stdev(surprises)
    return {"sigma_q": sigma, "n": len(surprises), "half_width": MILD_ZONE_SIGMAS * sigma if sigma > 0 else None}


def cycle_replays(facts: FactPack, ledger: Ledger) -> list:
    eps_at = {q.statement_fiscal[:10]: q for q in facts.eps if q.statement_fiscal}
    rev_cons, eps_cons = k.fin(facts.ntm_sums.get("rev_avg")), k.fin(facts.ntm.value)
    out = []
    for c in ledger.cycles:
        item = {"id": c["id"], "label": c["label"], "start": c["start"], "end": c["end"], "enabled": False,
                "reason": None, "peak": None, "trough": None, "eps_ratio": None, "rev_ratio": None,
                "rev": None, "direct_eps": None}
        out.append(item)
        window = [r for r in facts.income if c["start"] <= r["date"][:10] <= c["end"]]
        dates = [r["date"][:10] for r in window]
        gap = _contiguous_break(dates)
        missing = next((d for d in dates if d not in eps_at or k.fin(eps_at[d].eps_actual) is None), None)
        no_rev = next((r["date"][:10] for r in window if (k.fin(r.get("revenue")) or 0) <= 0), None)
        if len(window) < 2:
            item["reason"] = "窗口内报表季度不足 2 个"
        elif gap:
            item["reason"] = f"窗口内季度不连续：{gap}"
        elif missing:
            item["reason"] = f"{missing} 无街口 EPS"
        elif no_rev:
            item["reason"] = f"{no_rev} 营收缺失"
        if item["reason"]:
            continue
        eps = [eps_at[d].eps_actual for d in dates]
        peak = 0
        for i, v in enumerate(eps):
            if v > eps[peak]:
                peak = i
        if eps[peak] <= 0:
            item["reason"] = f"峰 EPS ≤ 0（{dates[peak]}）"
            continue
        if peak == len(eps) - 1:
            item["reason"] = f"峰 {dates[peak]} 之后无季度"
            continue
        trough = peak + 1
        for i in range(peak + 1, len(eps)):
            if eps[i] < eps[trough]:
                trough = i
        if rev_cons is None or eps_cons is None:
            item["reason"] = "NTM 共识缺失，无法换算预设"
            continue
        rev_ratio = k.fin(window[trough]["revenue"] / window[peak]["revenue"])
        eps_ratio = k.fin(eps[trough] / eps[peak])
        item.update(enabled=True, eps_ratio=eps_ratio, rev_ratio=rev_ratio,
                    peak={"fiscal_date": dates[peak], "eps": eps[peak], "revenue": window[peak]["revenue"]},
                    trough={"fiscal_date": dates[trough], "eps": eps[trough], "revenue": window[trough]["revenue"]},
                    rev=k.fin(rev_cons * rev_ratio), direct_eps=k.fin(eps_cons * eps_ratio))
    return out


def _cyclical(facts: FactPack, levels) -> dict:
    ttms = []
    for i in range(len(facts.eps)):
        value = ttm_eps(facts.eps[:i + 1])[0]
        if value is not None:
            ttms.append((value, facts.eps[i].fiscal_date))
    if not ttms:
        return {"peak_ttm": None, "peak_date": None, "mid_ttm": None, "peak_x_low": None, "mid_x_normal": None}
    peak = ttms[0]
    for t in ttms:
        if t[0] > peak[0]:
            peak = t
    mid = statistics.median(t[0] for t in ttms)
    return {"peak_ttm": peak[0], "peak_date": peak[1], "mid_ttm": mid,
            "peak_x_low": k.scenario_value(levels[0], peak[0]), "mid_x_normal": k.scenario_value(levels[2], mid)}


def _matrix(facts: FactPack, ledger: Ledger, pe: dict, fx0) -> dict:
    levels = [quantile(pe["sorted"], q) for q in PE_LEVELS]
    sums = facts.ntm_sums
    columns = []
    for key, label, eps_usd in (("ttm", "TTM 街口 EPS", facts.ttm_eps_usd),
                                ("ntm", "NTM 共识 EPS", k.to_usd(facts.ntm.value, fx0)),
                                ("ntm_low", "NTM 卖方低值", k.to_usd(sums.get("eps_low"), fx0)),
                                ("ntm_high", "NTM 卖方高值", k.to_usd(sums.get("eps_high"), fx0))):
        cells = [k.scenario_value(p, eps_usd) for p in levels]
        ipe = k.implied_pe(facts.price, eps_usd)
        bracket = None
        for i in range(len(cells) - 1):
            a, b = cells[i], cells[i + 1]
            if a is not None and b is not None and k.fin(facts.price) is not None and a <= facts.price <= b:
                bracket = [i, i + 1]
                break
        columns.append({"key": key, "label": label, "eps_usd": eps_usd, "implied_pe": ipe,
                        "pe_rank": k.pct_rank(pe["sorted"], ipe), "cells": cells, "bracket": bracket})
    sample = {key: pe[key] for key in ("start", "end", "n_valid", "n_nonpositive", "n_no_ttm")}
    sample["note"] = "历史分位为 TTM 街口 P/E"
    return {"levels": list(PE_LEVELS), "pe": levels, "columns": columns, "pe_sample": sample,
            "cyclical": _cyclical(facts, levels) if ledger.cyclical else None}


def _status(gates) -> dict:
    results = [asdict(r) for r in gates.results]
    if gates.page_blocked:
        banner = [f"整页熔断 · {r.id}：{r.detail}" for r in gates.results if r.blocks_page]
    else:
        banner = [f"已剔除 · {r.id}（{r.status}）：{r.detail}" for r in gates.results if r.status != VERIFIED]
    return {"overall": gates.overall, "page_blocked": gates.page_blocked, "exit_code": gates.exit_code,
            "fx0": gates.fx0, "g4_reextract": list(gates.g4_reextract), "banner": banner, "results": results}


def _meta(facts: FactPack, ledger: Ledger) -> dict:
    return {"symbol": facts.symbol, "as_of": facts.as_of, "reporting_currency": facts.reporting_currency,
            "ledger_currency": ledger.reporting_currency, "eps_basis": ledger.eps_basis, "cyclical": ledger.cyclical,
            "is_adr": facts.is_adr, "price": facts.price, "price_date": facts.price_date, "ledger": str(ledger.path),
            "sources": [dict(s) for s in ledger.sources.values()], "disclaimer": DISCLAIMER}


def _slider(init, lo, hi, disabled) -> dict:
    ok = k.fin(init) is not None and k.fin(lo) is not None and k.fin(hi) is not None
    return {"init": init, "min": lo, "max": hi, "disabled": bool(disabled or not ok)}


def build_payload(facts: FactPack, ledger: Ledger, gates: "GateSummary") -> dict:
    payload = {"meta": _meta(facts, ledger), "status": _status(gates)}
    if not gates.page_blocked:
        payload.update(_body(facts, ledger, gates))
    try:
        json.dumps(payload, allow_nan=False)
    except ValueError as exc:
        raise ValueError(f"payload contains a non-finite number: {exc}") from None
    return payload


def _guidance_notes(facts: FactPack, ledger: Ledger, gates) -> list:
    """Every gate-passing guidance item becomes a text note; none enters arithmetic (plan v2 §1)."""
    notes = []
    for g in ledger.guidance:
        if f"guidance:{g['id']}" not in gates.usable:
            continue          # 未通过门控的数字不引用，只在门控报告里出现
        flags = []
        if g["period_type"] == "run_rate":
            flags.append("年化口径")
        elif g["period_type"] == "fiscal_year":
            flags.append("财年口径")
        if g["is_lower_bound"]:
            flags.append("下限")
        if g["denominator"] != "total_revenue":
            flags.append("非总营收口径")
        src = ledger.sources[g["source"]]
        notes.append({"id": g["id"], "label": g["label"], "fiscal_date": g["fiscal_date"], "value": g["value"],
                      "flags": flags, "source": f"{src['id']} · {src['url']}"})
    return notes


def _basis_text(ledger: Ledger) -> str:
    return "街口 EPS × 摊薄股本 ÷ 营收" if ledger.eps_basis == "non_gaap" else "报表净利 ÷ 营收"


def _quarter_margins(facts: FactPack, ledger: Ledger):
    """Per-quarter net margin over every income row ≤ as_of; rows without the inputs count as missing."""
    eps_at = {q.statement_fiscal[:10]: q for q in facts.eps if q.statement_fiscal and q.announce_date <= facts.as_of}
    out, missing = [], 0
    for row in facts.income:
        day = row["date"][:10]
        rev = k.fin(row.get("revenue"))
        if ledger.eps_basis == "non_gaap":
            q = eps_at.get(day)
            eps = k.fin(q.eps_actual) if q is not None else None
            shares = k.fin(row.get("weighted_average_shs_out_dil"))
            ni = k.fin(eps * shares) if eps is not None and shares is not None else None
        else:
            ni = k.fin(row.get("net_income"))
        margin = k.fin(ni / rev) if ni is not None and rev is not None and rev > 0 else None
        if margin is None:
            missing += 1
        else:
            out.append((day, margin))
    return out, missing


def _lowest(points):
    best = None
    for p in points:
        if best is None or p[1] < best[1]:
            best = p
    return best


def margin_history(facts: FactPack, ledger: Ledger) -> dict:
    points, missing = _quarter_margins(facts, ledger)
    if not points:
        return {"n": 0, "n_missing": missing, "median": None, "min": None, "max": None, "latest": None,
                "start": None, "end": None, "basis": _basis_text(ledger)}
    low, high = _lowest(points), points[0]
    for p in points:
        if p[1] > high[1]:
            high = p
    return {"n": len(points), "n_missing": missing, "median": statistics.median(v for _, v in points),
            "min": {"fiscal_date": low[0], "value": low[1]}, "max": {"fiscal_date": high[0], "value": high[1]},
            "latest": {"fiscal_date": points[-1][0], "value": points[-1][1]},
            "start": points[0][0], "end": points[-1][0], "basis": _basis_text(ledger)}


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _margin_range(candidates):
    """floor(min, 10pp) … ceil(max + 5pp, 5pp); round before floor/ceil so float residue cannot add a step."""
    values = [v for v in candidates if k.fin(v) is not None]
    if not values:
        return None, None
    lo, hi = min(values), max(values)
    return math.floor(round(lo * 10, 6)) / 10, math.ceil(round((hi + 0.05) * 20, 6)) / 20


def _levels(anchors) -> Optional[list]:
    values = [v for v in anchors if k.fin(v) is not None and v > 0]
    if not values:
        return None
    lo, hi = min(values), max(values)
    if not hi > lo:
        return None
    return [(lo * (4 - i) + hi * i) / 4 for i in range(5)]


def _increasing(values) -> bool:
    return (len(values) == 5 and all(k.fin(v) is not None and v > 0 for v in values)
            and all(b > a for a, b in zip(values, values[1:])))


def _near_income(facts: FactPack, fiscal: str):
    rows = [r for r in facts.income if abs(_days(r["date"], fiscal)) <= ESTIMATE_MATCH_DAYS]
    return min(rows, key=lambda r: abs(_days(r["date"], fiscal))) if rows else None


def _model_segments(facts: FactPack, ledger: Ledger, gates, rev_cons):
    status = {r.id: r for r in gates.results}
    usable = [q for q in sorted(ledger.segment_quarters, key=lambda q: q["fiscal_date"])
              if f"seg:{q['fiscal_date']}" in gates.usable]
    s1 = status.get("S1")
    if s1 is not None and s1.status == VERIFIED and usable:
        q = usable[-1]
        total = _total(q["values"])
        segments = []
        for i, (name, v) in enumerate(zip(ledger.segment_names, q["values"])):
            share = k.fin(v / total)
            segments.append({
                "name": name, "role": ledger.segment_roles[i] if ledger.segment_roles else None,
                "color": ledger.segment_colors[i].lower() if ledger.segment_colors else PALETTE[i % len(PALETTE)],
                "latest_value": k.fin(v * ledger.segment_unit), "share": share,
                "base": k.fin(rev_cons * share) if rev_cons is not None and share is not None else None})
        row = _near_income(facts, q["fiscal_date"])
        g1 = status.get(f"G1a:{q['fiscal_date']}")
        logic = {"axis_rationale": ledger.segment_axis_rationale, "secondary_note": ledger.segment_secondary_note,
                 "g1_detail": g1.detail if g1 else None, "pct_derived": bool(q.get("pct_derived")),
                 "g1_dev": k.pct_diff(total * ledger.segment_unit, row.get("revenue")) if row else None,
                 "formula_note": FORMULA_NOTE}
        return "segments", segments, logic, q["fiscal_date"], None
    if not ledger.segment_quarters:
        reason = "台账未登记分部，使用单一总营收滑块"
    elif any(r.id.startswith("G3d:") and r.status == CONFLICT for r in gates.results):
        reason = "分部重述待确认（G3d），分部切分停用，使用单一总营收滑块"
    else:
        reason = f"S1 未过：{s1.detail if s1 else '无分部门控结果'}"
    total = [{"name": "总营收", "role": None, "color": PALETTE[0], "latest_value": None, "share": 1.0, "base": rev_cons}]
    return "total", total, None, None, reason


def _ntm_window(facts: FactPack) -> dict:
    quarters = [q[0] for q in facts.ntm.quarters]
    if facts.ntm.basis == "quarter_sum" and quarters:
        label = f"{quarters[0][:7]} – {quarters[-1][:7]}（逐季加总）"
    elif facts.ntm.basis == "ntm_fy_blend" and len(quarters) == len(facts.ntm_weights) == 2:
        w1, w2 = facts.ntm_weights
        label = f"FY{quarters[0][2:4]}×{w1:g} + FY{quarters[1][2:4]}×{w2:g}（财年混合）"
    else:
        label = "NTM 口径不可解"
    return {"basis": facts.ntm.basis, "quarters": quarters, "label": label}


def _trajectory(facts: FactPack, rev_cons) -> dict:
    hist = [{"fiscal_date": r["date"][:10], "revenue": k.fin(r.get("revenue"))} for r in facts.income[-4:]]
    rows = facts.next_q4
    if len(hist) < 4 or any(h["revenue"] is None for h in hist):
        return {"reason": "库内已公告季不足 4 个或缺营收", "hist": hist}
    if len(rows) < 4 or any(k.fin(r.get("rev_avg")) is None for r in rows):
        return {"reason": f"当前快照未公告季的季度营收共识不足 4 行（{len(rows)} 行），只画历史", "hist": hist}
    shape = [k.fin(r["rev_avg"]) for r in rows]
    total = _total(shape)
    scale = k.fin(rev_cons / total) if rev_cons is not None and total > 0 else None
    if scale is None:
        return {"reason": "NTM 营收共识缺失，只画历史", "hist": hist}
    analysts = [r.get("num_analysts_eps") for r in rows]
    note = TRAJECTORY_NOTE
    if facts.ntm.basis == "ntm_fy_blend":
        counts = " / ".join(str(a) if isinstance(a, int) else "缺" for a in analysts)
        note += f"；NTM 为财年混合口径，季度形状按缩放系数 {scale:.4f} 对齐 NTM 总额；各季 EPS 分析师数 {counts}"
    return {"hist": hist, "future_dates": [r["fiscal_date"][:10] for r in rows], "shape": shape,
            "analysts": analysts, "scale": scale, "note": note}


def _heatmap(facts: FactPack, cons_eps, sums, fx0, pe_init, scenarios, pe_levels_hist) -> dict:
    if k.fin(cons_eps) is None or cons_eps <= 0:
        return {"reason": "卖方共识 EPS ≤0 或缺失，EPS × P/E 热力矩阵不适用"}
    live = [s for s in scenarios[1:] if s["enabled"]]
    eps_levels = _levels([cons_eps, k.to_usd(sums.get("eps_low"), fx0), k.to_usd(sums.get("eps_high"), fx0)]
                         + [s["out"]["eps_usd"] for s in live]) or [cons_eps * f for f in LEVEL_FALLBACK]
    pe_anchors = [pe_init]
    for s in live:
        pe_anchors += [s["pe"] - s["pe_half"], s["pe"] + s["pe_half"]]
    pe_levels = _levels(pe_anchors)
    if pe_levels is None:
        pe_levels = list(pe_levels_hist) if _increasing(pe_levels_hist) else (
            [pe_init * f for f in LEVEL_FALLBACK] if k.fin(pe_init) is not None else None)
    if pe_levels is None:
        return {"reason": "现价隐含 P/E 不可得，热力矩阵不适用"}
    cells = [[k.scenario_value(pe, eps) for pe in pe_levels] for eps in eps_levels]
    pct = [[k.pct_diff(v, facts.price) for v in row] for row in cells]
    price_cell, best = None, None
    for i, row in enumerate(pct):
        for j, v in enumerate(row):
            if v is not None and (best is None or abs(v) < best):
                price_cell, best = [i, j], abs(v)
    marks = []
    for s in live:
        if s["tone"] == "base" and k.fin(s["out"]["eps_usd"]) is not None and s["out"]["eps_usd"] > 0:
            marks.append({"id": s["id"], "r_pos": k.grid_pos(eps_levels, s["out"]["eps_usd"]),
                          "c_pos": k.grid_pos(pe_levels, s["pe"])})
    return {"eps_levels": eps_levels, "pe_levels": pe_levels, "cells": cells, "pct": pct, "price_cell": price_cell,
            "marks": marks}


def _body(facts: FactPack, ledger: Ledger, gates) -> dict:
    status = {r.id: r.status for r in gates.results}
    eps_mode = "margin" if status.get("B1") == VERIFIED else "direct"
    fx0, pe = gates.fx0, pe_history(facts.closes, facts.eps)
    matrix = _matrix(facts, ledger, pe, fx0)
    levels = matrix["pe"]
    zone = mild_zone(facts)
    sums = facts.ntm_sums
    rev_cons, eps_cons, shares, price = k.fin(sums.get("rev_avg")), k.fin(facts.ntm.value), facts.shares, facts.price
    direct_eps = eps_cons if eps_mode == "direct" else None
    mode, segments, logic, split_quarter, total_reason = _model_segments(facts, ledger, gates, rev_cons)
    bases = [s["base"] for s in segments]
    zeros = [0.0] * len(bases)
    lo, hi = SEG_ADJ_RANGE

    def live_in(adjs, margin, pe_value, pe_half, fx):
        return {"bases": bases, "adjs": list(adjs), "driver_delta": 0.0, "margin": margin, "shares": shares,
                "fx": fx, "pe": pe_value, "pe_half": pe_half, "price": price, "rev_cons": rev_cons,
                "half_width": zone["half_width"], "eps_mode": eps_mode, "direct_eps": direct_eps}

    rev0 = k.seg_revenue(bases, zeros, 0.0)
    margin_cons = k.implied_margin(eps_cons, shares, rev0)
    pe_init = k.implied_pe(price, k.live_state(live_in(zeros, margin_cons, None, 0.0, fx0))["eps_usd"])
    init_in = live_in(zeros, margin_cons, pe_init, 0.0, fx0)
    init_out = k.live_state(init_in)
    history = margin_history(facts, ledger)
    points, _ = _quarter_margins(facts, ledger)

    def seg_rev(adjs):
        return [k.fin(b * (1 + a)) if b is not None else None for b, a in zip(bases, adjs)]

    trajectory = _trajectory(facts, rev_cons)

    def path(rev):
        return k.quarter_path(trajectory["shape"], rev) if "shape" in trajectory else None

    consensus_weight = ledger.consensus_weight if ledger.consensus_weight is not None else 0.0
    scenarios = [{"id": "consensus", "label": "卖方共识", "tone": "consensus", "thesis": CONSENSUS_THESIS,
                  "weight": consensus_weight, "adjs": zeros, "margin": margin_cons, "pe": pe_init, "pe_half": 0.0,
                  "enabled": True, "reason": None, "out": init_out,
                  "band": {"lo": k.scenario_value(pe_init, k.to_usd(sums.get("eps_low"), fx0)),
                           "hi": k.scenario_value(pe_init, k.to_usd(sums.get("eps_high"), fx0)),
                           "note": CONSENSUS_BAND_NOTE},
                  "seg_rev": seg_rev(zeros), "quarters": path(init_out["rev"])}]
    states = [("init", init_in)]
    for s in ledger.scenarios:
        reason = None
        if eps_mode != "margin":
            reason = "B1 未过：净利率法禁用，参考情景不可用"
        elif len(s["seg_adjs"]) != len(bases):
            reason = f"分部切分停用（{total_reason}），情景的分部调整无法套用"
        item = {"id": s["id"], "label": s["label"], "tone": s["tone"], "thesis": s["thesis"], "weight": s["weight"],
                "adjs": list(s["seg_adjs"]), "margin": s["net_margin"], "pe": s["pe_mid"], "pe_half": s["pe_half"],
                "enabled": reason is None, "reason": reason, "out": None, "band": None, "seg_rev": None,
                "quarters": None}
        if reason is None:
            vector = live_in(s["seg_adjs"], s["net_margin"], s["pe_mid"], s["pe_half"], fx0)
            out = k.live_state(vector)
            item.update(out=out, band={"lo": out["value_lo"], "hi": out["value_hi"], "note": None},
                        seg_rev=seg_rev(s["seg_adjs"]), quarters=path(out["rev"]))
            states.append((f"scenario:{s['id']}", vector))
        scenarios.append(item)
    ev_init = k.weighted_value([s["out"]["value"] if s["enabled"] and s["out"] else None for s in scenarios],
                               [s["weight"] for s in scenarios])

    cycles = []
    for c, raw in zip(ledger.cycles, cycle_replays(facts, ledger)):
        trough = _lowest([p for p in points if c["start"] <= p[0] <= c["end"]])
        reason = raw["reason"]
        if reason is None and eps_mode != "margin":
            reason = "B1 未过：净利率法禁用，周期回放不可用"
        if reason is None and trough is None:
            reason = "窗口内无季度净利率"
        adj = _clamp(raw["rev_ratio"] - 1, lo, hi) if raw["rev_ratio"] is not None else None
        item = {"id": c["id"], "label": c["label"], "start": c["start"], "end": c["end"], "enabled": reason is None,
                "reason": reason, "adj": adj, "margin": trough[1] if trough else None,
                "trough_quarter": trough[0] if trough else None, "rev_ratio": raw["rev_ratio"], "peak": raw["peak"],
                "trough": raw["trough"]}
        cycles.append(item)
        if item["enabled"]:
            states.append((f"cycle:{c['id']}", live_in([adj] * len(bases), item["margin"], pe_init, 0.0, fx0)))

    candidates = [v for _, v in points] + [margin_cons] + [s["net_margin"] for s in ledger.scenarios]
    m_lo, m_hi = _margin_range(candidates)
    pe_mids = [s["pe_mid"] for s in ledger.scenarios]
    pe_top = max([v for v in [levels[-1], pe_init] + pe_mids if v is not None], default=None)
    sliders = {
        "seg": {"min": lo, "max": hi, "disabled": eps_mode != "margin"},
        "margin": _slider(margin_cons, m_lo, m_hi, eps_mode != "margin"),
        "pe": _slider(pe_init, min([1.0] + pe_mids), 1.5 * pe_top if pe_top is not None else None, pe_init is None),
        "fx": (_slider(fx0, 0.85 * fx0, 1.15 * fx0, False) if facts.reporting_currency != "USD" and fx0 is not None
               else None),
        "drivers": [{"id": d["id"], "label": d["label"], "init": d["base"], "base": d["base"], "min": d["min"],
                     "max": d["max"], "basis_note": d.get("basis_note")} for d in ledger.drivers],
    }
    if not sliders["seg"]["disabled"]:
        for i in range(len(bases)):
            for end, v in (("min", lo), ("max", hi)):
                adjs = list(zeros)
                adjs[i] = v
                states.append((f"seg:{i}:{end}", live_in(adjs, margin_cons, pe_init, 0.0, fx0)))
    for key in ("margin", "pe", "fx"):
        sl = sliders[key]
        if sl is not None and not sl["disabled"]:
            for end in ("min", "max"):
                vec = dict(init_in)
                vec[key] = sl[end]
                states.append((f"{key}:{end}", vec))

    snap = facts.ntm.snapshot_date
    markers = []

    def mark(slider, value, label, kind, source):
        if k.fin(value) is not None:
            markers.append({"slider": slider, "value": value, "label": label, "kind": kind, "source": source})

    basis = _basis_text(ledger)
    if history["n"]:
        mark("margin", history["median"], "10Y 中位", "history",
             f"库内 {history['start']} 至 {history['end']} 共 {history['n']} 季，{basis}")
    for c in ledger.cycles:
        trough = _lowest([p for p in points if c["start"] <= p[0] <= c["end"]])
        if trough:
            mark("margin", trough[1], f"{c['label']} 周期谷底（{trough[0]}）", "history",
                 f"台账周期窗口 {c['start']} 至 {c['end']} 内最低季度净利率，{basis}")
    if history["latest"]:
        mark("margin", history["latest"]["value"], "最新季实绩", "actual",
             f"{history['latest']['fiscal_date']} 季，{basis}")
    mark("margin", margin_cons, "卖方共识隐含", "street",
         f"fmp_estimates 周快照 {snap}：NTM EPS × 摊薄股本 ÷ NTM 营收")
    for q, v in zip(PE_LEVELS, levels):
        mark("pe", v, f"P{round(q * 100)}", "history", f"TTM 街口 P/E {pe['start']}–{pe['end']}")
    if sliders["fx"] is not None:
        mark("fx", ledger.fx_local_per_usd, "台账现汇", "ledger", "台账 [fx].local_per_usd")

    ntm_usd = k.to_usd(eps_cons, fx0)
    consensus = {
        "currency": facts.reporting_currency, "eps_cons": eps_cons, "ntm_eps_usd": ntm_usd, "rev_cons": rev_cons,
        "ttm_eps_usd": facts.ttm_eps_usd, "ttm_quarters": list(facts.ttm_quarters), "ntm_basis": facts.ntm.basis,
        "ntm_quarters": [list(q) for q in facts.ntm.quarters], "ntm_weights": list(facts.ntm_weights),
        "snapshot_date": snap, "ntm_sums": dict(sums), "shares": shares, "price": price,
        "price_date": facts.price_date, "fx0": fx0, "half_width": zone["half_width"], "sigma_q": zone["sigma_q"],
        "n_surprise": zone["n"], "margin_cons": margin_cons, "pe_init": pe_init,
        "mkt_cap": k.fin(price * shares) if k.fin(price) is not None and k.fin(shares) is not None else None,
        "ntm_window": _ntm_window(facts),
        "eps_mode_reason": None if eps_mode == "margin" else "；".join(
            r.detail for r in gates.results if r.id == "B1" and r.status != VERIFIED),
        "crowding": {"rev_low_vs_avg": k.pct_diff(sums.get("rev_low"), rev_cons),
                     "rev_high_vs_avg": k.pct_diff(sums.get("rev_high"), rev_cons),
                     "eps_low_vs_avg": k.pct_diff(sums.get("eps_low"), eps_cons),
                     "eps_high_vs_avg": k.pct_diff(sums.get("eps_high"), eps_cons)},
        "crowding_note": CROWDING_NOTE,
    }
    model = {"mode": mode, "eps_mode": eps_mode, "direct_eps": direct_eps, "currency": facts.reporting_currency,
             "split_quarter": split_quarter, "segments": segments, "logic": logic, "total_reason": total_reason,
             "init": {"adjs": zeros, "margin": margin_cons, "pe": pe_init, "pe_half": 0.0, "fx": fx0},
             "live_in": init_in}
    return {"matrix": matrix, "consensus": consensus, "margins": incremental_margins(facts, ledger), "model": model,
            "sliders": sliders, "markers": markers, "margin_history": history, "scenarios": scenarios,
            "cycles": cycles, "trajectory": trajectory,
            "heatmap": _heatmap(facts, init_out["eps_usd"], sums, fx0, pe_init, scenarios, levels),
            "ev_init": ev_init, "pe_sorted": pe["sorted"], "guidance_notes": _guidance_notes(facts, ledger, gates),
            "replay_note": None if any(c["enabled"] for c in cycles) else NO_REPLAY,
            "selfcheck": [{"name": n, "in": s, "out": k.live_state(s)} for n, s in states]}
