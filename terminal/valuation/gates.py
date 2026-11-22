"""Fail-closed tri-state gates (plan 2026-11-22 「门控与熔断表」) and the single overall-state summary.

Missing evidence is UNVERIFIED, contradicting evidence is CONFLICT; neither is ever shown as a pass.
A page-scope result with blocks_page stops the whole page (exit 20). Acked G3 results are VERIFIED
with acked=True and stay visible in the gate report.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional, Tuple
from urllib.parse import urlparse

from terminal.prosperity.config import ESTIMATE_MATCH_DAYS
from terminal.valuation import engine
from terminal.valuation.config import (CONFLICT, EXIT_BLOCKED, EXIT_DEGRADED, EXIT_OK, FX_DRIFT_TOL,
                                       FX_XVENDOR_TOL, G1_TOL, G3_LABELS, MIN_ABS_EPS_FOR_FX, PE_MIN_DAYS,
                                       QUOTE_MIN_CHARS, SHARE_JUMP_TOL, T1_GLOBAL_DOMAINS, UNVERIFIED, VERIFIED)
from terminal.valuation.facts import FactPack
from terminal.valuation.kernel import fin
from terminal.valuation.ledger import Ledger

PRIOR_YEAR_DAYS = 365
RANK = {VERIFIED: 0, UNVERIFIED: 1, CONFLICT: 2}


@dataclass(frozen=True)
class GateResult:
    id: str                    # 如 "C2"、"G1a:2026-09-30"、"G2:g-2026q4-rev"
    scope: str                 # "page" | "item" | "bridge"
    status: str
    blocks_page: bool
    detail: str
    acked: bool = False


@dataclass(frozen=True)
class GateSummary:
    results: Tuple[GateResult, ...]
    overall: str               # 最差状态
    page_blocked: bool
    fx0: Optional[float]       # USD 为 1.0
    usable: frozenset          # 通过 G1/G2 的条目 id
    g4_reextract: Tuple[str, ...]
    exit_code: int


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days


def _near(rows, key: str, day: str, max_days: int = ESTIMATE_MATCH_DAYS):
    hits = [r for r in rows if r.get(key) and abs(_days(r[key], day)) <= max_days]
    return min(hits, key=lambda r: abs(_days(r[key], day))) if hits else None


def _within(a, b, tol) -> bool:
    return abs(a - b) <= tol * abs(b)


def _ratio(num, den) -> Optional[float]:
    num, den = fin(num), fin(den)
    return fin(num / den) if num is not None and den is not None and num > 0 and den > 0 else None


def _eps_ratio(local, usd) -> Optional[float]:
    """Loss-quarter guard: both |EPS| ≥ MIN_ABS_EPS_FOR_FX and the same sign."""
    local, usd = fin(local), fin(usd)
    if local is None or usd is None or min(abs(local), abs(usd)) < MIN_ABS_EPS_FOR_FX or local * usd <= 0:
        return None
    return fin(local / usd)


def _latest_actual(facts: FactPack):
    rows = [r for r in facts.earnings if fin(r.get("revenue_actual")) is not None and r.get("fiscal_date")
            and r["announce_date"][:10] <= facts.as_of]
    return max(rows, key=lambda r: r["fiscal_date"]) if rows else None


def implied_fx(facts: FactPack) -> Mapping[str, Optional[float]]:
    nq, up, yf = facts.next_q or {}, facts.upcoming or {}, facts.yf_0q or {}
    last = _latest_actual(facts)
    income = _near(facts.income, "date", last["fiscal_date"]) if last else None
    return {
        "fwd_rev": _ratio(nq.get("rev_avg"), up.get("revenue_estimated")),
        "fwd_eps_fmp": _eps_ratio(nq.get("eps_avg"), up.get("eps_estimated")),
        "fwd_eps_yf": _eps_ratio(nq.get("eps_avg"), yf.get("eps_avg")),
        "act_rev": _ratio(income.get("revenue"), last.get("revenue_actual")) if income else None,
    }


def _r(gid, scope, status, detail, blocks=None, acks=frozenset()):
    """blocks: which statuses stop the page (page scope only); acked G3 conflicts pass."""
    if status == CONFLICT and gid in acks:
        return GateResult(gid, scope, VERIFIED, False, detail + "（Boss 已确认）", acked=True)
    return GateResult(gid, scope, status, status in (blocks or ()), detail)


def _page(facts: FactPack, ledger: Ledger, fx0: Optional[float]):
    out = []
    usd = facts.reporting_currency == "USD"
    if fin(facts.price) is not None:
        out.append(_r("P1", "page", VERIFIED, f"最后收盘 {facts.price_date}"))
    else:
        age = f"，距 as_of {_days(facts.as_of, facts.price_date)} 天" if facts.price_date else ""
        out.append(_r("P1", "page", UNVERIFIED, f"无 7 天内收盘价（最后收盘 {facts.price_date or '无'}{age}）",
                      (UNVERIFIED,)))
    if fin(facts.ntm.value) is not None and facts.ntm_weights:
        out.append(_r("P2", "page", VERIFIED, f"NTM {facts.ntm.basis}，快照 {facts.ntm.snapshot_date}"))
    else:
        out.append(_r("P2", "page", UNVERIFIED,
                      f"NTM 不可得：{facts.ntm.missing_reason or '权重不可解'}", (UNVERIFIED,)))
    both = (CONFLICT, UNVERIFIED)
    if facts.reporting_currency is None:
        out.append(_r("C1", "page", UNVERIFIED, "库内无报告币种", both))
    elif ledger.reporting_currency != facts.reporting_currency:
        out.append(_r("C1", "page", CONFLICT,
                      f"台账币种 {ledger.reporting_currency} ≠ 库内 {facts.reporting_currency}", both))
    elif ledger.eps_basis == "non_gaap" and not usd:
        out.append(_r("C1", "page", CONFLICT, "non_gaap 口径要求报告币种 USD", both))
    else:
        out.append(_r("C1", "page", VERIFIED, f"报告币种 {facts.reporting_currency}"))
    fx = implied_fx(facts)
    if usd:
        out.append(_r("C2a", "page", VERIFIED, "USD 报告者，fx0 = 1"))
    elif fx0 is None:
        out.append(_r("C2a", "page", UNVERIFIED, "缺下一季营收共识或未公告季营收预期，fx0 不可得", (UNVERIFIED,)))
    else:
        out.append(_r("C2a", "page", VERIFIED, f"fx0 = {fx0:.4f}（下一季营收共识 ÷ 美元营收预期）"))
    if fx0 is None:
        out.append(_r("C2", "page", UNVERIFIED, "fx0 不可得，跨供应商校验不可做"))
    elif fx["fwd_eps_yf"] is None:
        out.append(_r("C2", "page", UNVERIFIED,
                      "缺 yfinance 下一季 EPS、或 EPS 绝对值过小/异号，跨供应商校验不可做"))
    else:
        dev = fx["fwd_eps_yf"] / fx0 - 1
        status = VERIFIED if abs(dev) <= FX_XVENDOR_TOL else CONFLICT
        out.append(_r("C2", "page", status, f"跨供应商隐含比 {fx['fwd_eps_yf']:.4f}，对 fx0 偏差 {dev:+.1%}",
                      (CONFLICT,)))
    tol, name = (G1_TOL, "G1b 双源营收") if usd else (FX_DRIFT_TOL, "已公告季隐含汇率漂移")
    if fx0 is None or fx["act_rev"] is None:
        out.append(_r("C3", "page", UNVERIFIED, f"{name}：最新已公告季营收缺一源"))
    else:
        dev = fx["act_rev"] / fx0 - 1
        status = VERIFIED if abs(dev) <= tol else CONFLICT
        out.append(_r("C3", "page", status, f"{name}：比值 {fx['act_rev']:.4f}，对 fx0 偏差 {dev:+.1%}",
                      (CONFLICT,)))
    return out


def _engine_inputs(facts: FactPack, ledger: Ledger):
    """P3 / B1 / B2 read the engine's own P/E sample, street margin and mild zone (one source of numbers)."""
    pe = engine.pe_history(facts.closes, facts.eps)
    sample = f"有效 {pe['n_valid']} 天（{pe['start']}–{pe['end']}，TTM≤0 {pe['n_nonpositive']} 天，无 TTM {pe['n_no_ttm']} 天）"
    out = [_r("P3", "page", VERIFIED if pe["n_valid"] >= PE_MIN_DAYS else UNVERIFIED,
              f"P/E 样本{sample}，下限 {PE_MIN_DAYS} 天", (UNVERIFIED,))]
    street = engine.incremental_margins(facts, ledger)["street"]
    shares = fin(facts.shares)
    if street["value"] is None:
        out.append(_r("B1", "bridge", UNVERIFIED, f"街口隐含增量利润率不可得：{street['detail']}，桥禁用，EPS 固定为共识"))
    elif fin(facts.ntm_sums.get("rev_avg")) is None or shares is None or shares <= 0:
        out.append(_r("B1", "bridge", UNVERIFIED, "NTM 营收共识或摊薄股本缺失，桥禁用，EPS 固定为共识"))
    else:
        out.append(_r("B1", "bridge", VERIFIED, f"街口隐含增量利润率 {street['value']:.4f}（{street['detail']}）"))
    zone = engine.mild_zone(facts)
    if zone["half_width"] is None:
        out.append(_r("B2", "bridge", UNVERIFIED,
                      f"营收意外样本 {zone['n']} 季（需 ≥8 且离散度 >0），桥禁用，EPS 固定为共识"))
    else:
        out.append(_r("B2", "bridge", VERIFIED, f"营收意外样本 {zone['n']} 季，温和区 ±{zone['half_width']:.1%}"))
    return out


def _g3(facts: FactPack, ledger: Ledger):
    out, acks = [], ledger.acks
    rows = [r for r in facts.income[-8:]]
    for a, b in zip(rows, rows[1:]):
        sa, sb = fin(a.get("weighted_average_shs_out_dil")), fin(b.get("weighted_average_shs_out_dil"))
        if sa is not None and sb is not None and sa > 0 and abs(sb / sa - 1) > SHARE_JUMP_TOL:
            out.append(_r(f"G3a:{b['date'][:10]}", "page", CONFLICT,
                          f"摊薄股本 {a['date'][:10]}→{b['date'][:10]} 变动 {sb / sa - 1:+.1%}", (CONFLICT,), acks))
    if not out:
        out.append(_r("G3a", "page", VERIFIED, f"近 {len(rows)} 季摊薄股本环比均 ≤{SHARE_JUMP_TOL:.0%}"))
    by_fiscal = {q.fiscal_date[:10]: q for q in facts.eps}
    flagged = []
    for fiscal in facts.ttm_quarters:
        labels = [x for x in by_fiscal[fiscal].labels if x in G3_LABELS] if fiscal in by_fiscal else []
        if labels:
            flagged.append(_r(f"G3b:{fiscal}", "page", CONFLICT, f"TTM 季度带标签 {', '.join(labels)}",
                              (CONFLICT,), acks))
    out.extend(flagged or [_r("G3b", "page", VERIFIED, "TTM 四季无拆股/冲突标签")])
    if facts.price_jump_notes:
        out.append(_r("G3c", "page", CONFLICT, "；".join(facts.price_jump_notes), (CONFLICT,), acks))
    else:
        out.append(_r("G3c", "page", VERIFIED, "复权后无疑似拆股跳变"))
    names = ledger.segment_names
    for q in ledger.segment_quarters:
        restated = q.get("prior_year_restated")
        prior = _near([p for p in ledger.segment_quarters if p is not q], "fiscal_date",
                      _shift(q["fiscal_date"], -PRIOR_YEAR_DAYS)) if restated else None
        if prior is None:
            continue
        for name, new, old in zip(names, restated, prior["values"]):
            status = VERIFIED if _within(new, old, G1_TOL) else CONFLICT
            out.append(_r(f"G3d:{q['fiscal_date']}:{name}", "item", status,
                          f"上年同季 {prior['fiscal_date']} 原值 {old:g}，重述值 {new:g}", (), acks))
    return out


def _shift(day: str, days: int) -> str:
    return date.fromordinal(date.fromisoformat(day).toordinal() + days).isoformat()


def _g2(gid: str, item: Mapping, ledger: Ledger) -> GateResult:
    src = ledger.sources[item["source"]]
    path = ledger.path.parent / src["raw"]
    try:
        raw = path.read_bytes()
    except OSError:
        return _r(gid, "item", UNVERIFIED, f"原文缺失：{src['raw']}")
    if hashlib.sha256(raw).hexdigest() != src["sha256"].lower():
        return _r(gid, "item", UNVERIFIED, f"原文 sha256 与台账不符：{src['raw']}")
    try:
        text = " ".join(raw.decode("utf-8").split())
    except UnicodeDecodeError:
        return _r(gid, "item", UNVERIFIED, f"原文不是 UTF-8 文本：{src['raw']}")
    quote = " ".join(item["quote"].split())
    if len(quote) < QUOTE_MIN_CHARS:
        return _r(gid, "item", UNVERIFIED, f"引文过短（{len(quote)} 字符 < {QUOTE_MIN_CHARS}）")
    if quote not in text:
        return _r(gid, "item", CONFLICT, f"引文不是原文 {src['id']} 的子串，需异构重抽")
    host = (urlparse(src["url"]).hostname or "").lower()
    domains = [d.lower() for d in T1_GLOBAL_DOMAINS + ledger.t1_domains]
    if not any(host == d or host.endswith("." + d) for d in domains):
        return _r(gid, "item", UNVERIFIED, f"出处域名 {host or '无'} 不在 T1 白名单")
    return _r(gid, "item", VERIFIED, f"引文命中原文 {src['id']}")


def _items(facts: FactPack, ledger: Ledger):
    out = []
    unit = ledger.segment_unit
    for q in ledger.segment_quarters:
        fiscal = q["fiscal_date"]
        row = _near(facts.income, "date", fiscal)
        rev = fin(row.get("revenue")) if row else None
        total = 0.0
        for v in q["values"]:
            total += v * unit
        if rev is None or rev <= 0:
            out.append(_r(f"G1a:{fiscal}", "item", UNVERIFIED, "库内无该季营收"))
        else:
            status = VERIFIED if _within(total, rev, G1_TOL) else CONFLICT
            out.append(_r(f"G1a:{fiscal}", "item", status, f"分部合计 {total:,.0f} vs 营收 {rev:,.0f}"))
        out.append(_g2(f"G2:seg:{fiscal}", q, ledger))
    for f in ledger.eps_facts:
        fiscal = f["fiscal_date"]
        quarters = [{"date": q.period_end, "eps": q.eps_actual} for q in facts.eps if fin(q.eps_actual) is not None]
        street = _near(quarters, "date", fiscal)
        if facts.reporting_currency != "USD":
            out.append(_r(f"G1c:{fiscal}", "item", UNVERIFIED, "非 USD 报告者：街口 EPS 为美元，无同币种对照"))
        elif street is None:
            out.append(_r(f"G1c:{fiscal}", "item", UNVERIFIED, "库内无该季街口 EPS"))
        else:
            status = VERIFIED if _within(f["value"], street["eps"], G1_TOL) else CONFLICT
            out.append(_r(f"G1c:{fiscal}", "item", status, f"台账 {f['value']:g} vs 街口 {street['eps']:g}"))
        out.append(_g2(f"G2:eps:{fiscal}", f, ledger))
    for g in ledger.guidance:
        out.append(_g2(f"G2:{g['id']}", g, ledger))
    return out


def _fx0(facts: FactPack) -> Optional[float]:
    return 1.0 if facts.reporting_currency == "USD" else implied_fx(facts)["fwd_rev"]


def run_gates(facts: FactPack, ledger: Ledger) -> Tuple[GateResult, ...]:
    return tuple(_page(facts, ledger, _fx0(facts)) + _engine_inputs(facts, ledger) + _g3(facts, ledger)
                 + _items(facts, ledger))


def summarize(results, facts: FactPack, ledger: Ledger) -> GateSummary:
    results = tuple(results)
    status = {r.id: r.status for r in results}
    ok = lambda gid: status.get(gid) == VERIFIED
    restated = any(r.id.startswith("G3d:") and r.status == CONFLICT for r in results)
    usable = set()
    for q in ledger.segment_quarters:
        fiscal = q["fiscal_date"]
        if not restated and ok(f"G1a:{fiscal}") and ok(f"G2:seg:{fiscal}"):
            usable.add(f"seg:{fiscal}")
    for f in ledger.eps_facts:
        if ok(f"G1c:{f['fiscal_date']}") and ok(f"G2:eps:{f['fiscal_date']}"):
            usable.add(f"eps:{f['fiscal_date']}")
    for g in ledger.guidance:
        if ok(f"G2:{g['id']}"):
            usable.add(f"guidance:{g['id']}")
    page_blocked = any(r.blocks_page for r in results)
    overall = max((r.status for r in results), key=RANK.__getitem__, default=VERIFIED)
    exit_code = EXIT_BLOCKED if page_blocked else EXIT_DEGRADED if overall != VERIFIED else EXIT_OK
    return GateSummary(results=results, overall=overall, page_blocked=page_blocked, fx0=_fx0(facts),
                       usable=frozenset(usable),
                       g4_reextract=tuple(r.id for r in results if r.id.startswith("G2:") and r.status == CONFLICT),
                       exit_code=exit_code)
