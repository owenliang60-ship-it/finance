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
from terminal.valuation.config import (CONFLICT, MILD_ZONE_SIGMAS, PE_LEVELS, REV_DELTA_MIN, SURPRISE_MIN_QUARTERS,
                                       SURPRISE_QUARTERS, VERIFIED)
from terminal.valuation.facts import FactPack
from terminal.valuation.ledger import Ledger

if TYPE_CHECKING:
    from terminal.valuation.gates import GateSummary

QUARTER_GAP = (60, 120)       # adjacent fiscal quarters (days), same rule as prosperity consensus
PRIOR_YEAR_DAYS = 365
DISCLAIMER = "本页只呈现事实反推与你输入的情景演算，不含模型结论。"
NO_REPLAY = "样本内无可回放的深度下行周期"
CROWDING_NOTE = "拥挤度读数，非区间边界"


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


def _guidance(facts: FactPack, ledger: Ledger, gates, rev_cons):
    markers, notes = [], []
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
        if facts.ntm.basis != "quarter_sum":
            flags.append("NTM 非逐季加总")
        row = next((r for r in facts.ntm_rows if r is not None and k.fin(r.get("rev_avg")) is not None
                    and abs(_days(r["fiscal_date"], g["fiscal_date"])) <= ESTIMATE_MATCH_DAYS), None)
        if row is None:
            flags.append("不在 NTM 季度内")
        src = ledger.sources[g["source"]]
        if flags or rev_cons is None:
            notes.append({"id": g["id"], "label": g["label"], "fiscal_date": g["fiscal_date"], "value": g["value"],
                          "flags": flags, "source": f"{src['id']} · {src['url']}"})
            continue
        markers.append({"slider": "rev", "value": k.fin(rev_cons - row["rev_avg"] + g["value"]), "label": g["label"],
                        "kind": "guidance", "source": f"{src['id']} · {src['url']}"})
    return markers, notes


def _segments(ledger: Ledger, gates) -> dict:
    cleared = any(r.id.startswith("G3d:") and r.status == CONFLICT for r in gates.results)
    usable = [q for q in sorted(ledger.segment_quarters, key=lambda q: q["fiscal_date"])
              if f"seg:{q['fiscal_date']}" in gates.usable]
    quarters = []
    for q in usable:
        prior, basis = q.get("prior_year_restated"), "restated"
        if prior is None:
            prev = next((p for p in usable if abs(_days(q["fiscal_date"], p["fiscal_date"]) - PRIOR_YEAR_DAYS)
                         <= ESTIMATE_MATCH_DAYS), None)
            prior, basis = (prev["values"], "ledger") if prev else (None, None)
        yoy = [k.pct_diff(v, b) for v, b in zip(q["values"], prior)] if prior else [None] * len(q["values"])
        quarters.append({"fiscal_date": q["fiscal_date"], "values": list(q["values"]), "yoy": yoy,
                         "yoy_basis": basis, "source": q["source"]})
    return {"unit": ledger.segment_unit, "names": list(ledger.segment_names), "quarters": quarters,
            "cleared": "分部重述待确认（G3d），分部区已清空" if cleared else None}


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


def _body(facts: FactPack, ledger: Ledger, gates) -> dict:
    status = {r.id: r.status for r in gates.results}
    bridge_ok = status.get("B1") == VERIFIED and status.get("B2") == VERIFIED
    fx0, pe = gates.fx0, pe_history(facts.closes, facts.eps)
    matrix = _matrix(facts, ledger, pe, fx0)
    levels = matrix["pe"]
    margins = incremental_margins(facts, ledger)
    zone = mild_zone(facts)
    presets = cycle_replays(facts, ledger)
    enabled = [c for c in presets if c["enabled"]]
    rev_cons, eps_cons = facts.ntm_sums.get("rev_avg"), facts.ntm.value
    rc = k.fin(rev_cons)
    ratio = 0.5
    for c in enabled:
        ratio = min(ratio, c["rev_ratio"] - 0.05)
    street = margins["street"]["value"]
    ntm_usd = k.to_usd(eps_cons, fx0)
    pe_init = k.implied_pe(facts.price, ntm_usd)
    pe_top = max([v for v in (levels[-1], pe_init) if v is not None], default=None)
    sliders = {
        "rev": _slider(rev_cons, rc * ratio if rc is not None else None, rc * 1.5 if rc is not None else None,
                       not bridge_ok),
        "margin": _slider(street, -0.5, 1.5, not bridge_ok),
        "pe": _slider(pe_init, 1.0, 1.5 * pe_top if pe_top is not None else None, pe_init is None),
        "fx": (_slider(fx0, 0.85 * fx0, 1.15 * fx0, False) if facts.reporting_currency != "USD" and fx0 is not None
               else None),
        "drivers": [{"id": d["id"], "label": d["label"], "init": d["base"], "base": d["base"], "min": d["min"],
                     "max": d["max"], "basis_note": d.get("basis_note")} for d in ledger.drivers],
    }
    mode = "bridge" if bridge_ok else "direct"
    init = {"rev": rev_cons, "rev_cons": rev_cons, "eps_cons": eps_cons, "margin": street, "shares": facts.shares,
            "half_width": zone["half_width"], "fx": fx0, "price": facts.price, "pe": pe_init, "driver_delta": 0.0,
            "eps_mode": mode, "direct_eps": None if bridge_ok else eps_cons}
    states = [("init", init)]
    for key in ("rev", "margin", "pe", "fx"):
        s = sliders[key]
        if s is not None and not s["disabled"]:
            states += [(f"{key}:min", {**init, key: s["min"]}), (f"{key}:max", {**init, key: s["max"]})]
    for c in enabled:
        states.append((f"preset:{c['id']}", {**init, "rev": c["rev"], "driver_delta": 0.0, "eps_mode": "direct",
                                             "direct_eps": c["direct_eps"]}))
    snap = facts.ntm.snapshot_date
    markers = []

    def mark(slider, value, label, kind, source):
        if k.fin(value) is not None:
            markers.append({"slider": slider, "value": value, "label": label, "kind": kind, "source": source})

    mark("rev", facts.ntm_sums.get("rev_low"), f"卖方低值之和（{CROWDING_NOTE}）", "street", f"fmp_estimates 周快照 {snap}")
    mark("rev", facts.ntm_sums.get("rev_high"), f"卖方高值之和（{CROWDING_NOTE}）", "street", f"fmp_estimates 周快照 {snap}")
    for c in enabled:
        mark("rev", c["rev"], f"{c['label']} 回放", "history",
             f"台账周期 {c['start']}–{c['end']}：峰 {c['peak']['fiscal_date']} → 谷 {c['trough']['fiscal_date']}")
    guide_marks, notes = _guidance(facts, ledger, gates, rc)
    markers += guide_marks
    mark("margin", margins["hist"]["value"], "历史实现增量利润率", "history", margins["hist"]["detail"])
    mark("margin", street, "街口隐含增量利润率", "street", margins["street"]["detail"])
    mark("margin", margins["boss"]["value"], "Boss 假设", "boss", margins["boss"]["detail"])
    for q, v in zip(PE_LEVELS, levels):
        mark("pe", v, f"P{round(q * 100)}", "history", f"TTM 街口 P/E {pe['start']}–{pe['end']}")
    if sliders["fx"] is not None:
        mark("fx", ledger.fx_local_per_usd, "台账现汇", "ledger", "台账 [fx].local_per_usd")
    sums = facts.ntm_sums
    consensus = {
        "currency": facts.reporting_currency, "eps_cons": eps_cons, "ntm_eps_usd": ntm_usd, "rev_cons": rev_cons,
        "ttm_eps_usd": facts.ttm_eps_usd, "ttm_quarters": list(facts.ttm_quarters), "ntm_basis": facts.ntm.basis,
        "ntm_quarters": [list(q) for q in facts.ntm.quarters], "ntm_weights": list(facts.ntm_weights),
        "snapshot_date": snap, "ntm_sums": dict(sums), "shares": facts.shares, "price": facts.price,
        "price_date": facts.price_date, "fx0": fx0, "half_width": zone["half_width"], "sigma_q": zone["sigma_q"],
        "n_surprise": zone["n"], "bridge_enabled": bridge_ok,
        "bridge_reason": None if bridge_ok else "；".join(r.detail for r in gates.results
                                                         if r.id in ("B1", "B2") and r.status != VERIFIED),
        "eps_mode_init": mode, "direct_eps_init": init["direct_eps"],
        "crowding": {"rev_low_vs_avg": k.pct_diff(sums.get("rev_low"), rev_cons),
                     "rev_high_vs_avg": k.pct_diff(sums.get("rev_high"), rev_cons),
                     "eps_low_vs_avg": k.pct_diff(sums.get("eps_low"), eps_cons),
                     "eps_high_vs_avg": k.pct_diff(sums.get("eps_high"), eps_cons)},
        "crowding_note": CROWDING_NOTE,
    }
    return {"matrix": matrix, "consensus": consensus, "margins": margins, "sliders": sliders, "markers": markers,
            "presets": presets, "guidance_notes": notes, "segments": _segments(ledger, gates),
            "pe_sorted": pe["sorted"], "replay_note": None if enabled else NO_REPLAY,
            "selfcheck": [{"name": n, "in": s, "out": k.scenario_state(s)} for n, s in states]}
