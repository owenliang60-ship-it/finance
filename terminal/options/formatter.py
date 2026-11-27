"""
Options Formatter — Format chain data, Greeks, P/L, and comparison tables.

Used by commands.py and the /options skill to present data to the user.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_options_context(ctx: Dict[str, Any]) -> str:
    """Format the full options context as markdown overview.

    Args:
        ctx: Output from prepare_options_context()

    Returns:
        Formatted markdown string
    """
    symbol = ctx.get("symbol", "???")
    price = ctx.get("underlying_price")
    lines = []

    lines.append("## {} Options Overview".format(symbol))
    lines.append("")

    # Price + OPRMS. position_pct is the rating-time suggested cap, never a holding (issue 045).
    oprms = ctx.get("oprms") or {}
    price_str = "${:.2f}".format(price) if price else "N/A"
    dna = oprms.get("dna", "—")
    timing = oprms.get("timing", "—")
    pos_pct = oprms.get("position_pct")
    pos_str = "{:.1f}%".format(pos_pct) if pos_pct else "—"
    as_of = str(oprms.get("created_at") or "")[:10] or "—"
    cap_str = "OPRMS Suggested Cap (非实际持仓): {} (as of {})".format(pos_str, as_of)
    if oprms.get("is_stale"):
        cap_str += " ⚠️ STALE >60d"

    lines.append("**Price**: {} | **OPRMS**: DNA={} Timing={} | {}".format(
        price_str, dna, timing, cap_str
    ))
    lines.append("")

    # Gates summary
    gates = ctx.get("gates")
    if gates:
        lines.append("**Gates**: Snapshot={} | Liquidity={} | Earnings={} | IV={}".format(
            gates.get("snapshot") or "MISSING",
            gates.get("liquidity") or "—",
            gates.get("earnings") or "UNKNOWN",
            gates.get("iv") or "MISSING",
        ))
        lines.append("")

    # Snapshot freshness (absent = MISSING, fail-closed)
    lines.append(_format_snapshot_freshness(ctx.get("snapshot_freshness") or {}))
    lines.append("")

    # Actual holdings
    lines.extend(_format_holdings(ctx.get("portfolio")))
    lines.append("")

    # IV Summary
    iv = ctx.get("iv_summary") or {}
    if iv:
        current = iv.get("current_iv")
        rank = iv.get("iv_rank")
        pctl = iv.get("iv_percentile")
        hv = iv.get("hv_30d")
        rv_iv = iv.get("rv_iv_ratio")

        iv_line = "**IV**: {:.1f}%".format(current * 100) if current else "**IV**: N/A"
        if rank is not None:
            iv_line += " | **IV Rank**: {:.0f}%".format(rank)
        if pctl is not None:
            iv_line += " | **IV Pctl**: {:.0f}%".format(pctl)
        if hv:
            iv_line += " | **HV30**: {:.1f}%".format(hv * 100)
        if rv_iv:
            iv_line += " | **RV/IV**: {:.2f}".format(rv_iv)
        lines.append(iv_line)

        # IV environment assessment
        if rank is not None:
            if rank > 70:
                lines.append("  -> **High IV** environment — favor selling premium")
            elif rank < 30:
                lines.append("  -> **Low IV** environment — favor buying premium")
            else:
                lines.append("  -> **Neutral IV** environment")
        lines.append("")

    # Liquidity
    liq = ctx.get("liquidity") or {}
    if liq:
        verdict = liq.get("verdict", "—")
        spread = liq.get("avg_spread_pct")
        oi = liq.get("avg_oi")
        spread_str = "{:.1f}%".format(spread * 100) if spread is not None else "—"
        oi_str = "{:,.0f}".format(oi) if oi is not None else "—"
        lines.append("**Liquidity**: {} | Avg Spread: {} | Avg OI: {}".format(
            verdict, spread_str, oi_str
        ))
        if verdict == "NO_GO":
            lines.append("  -> **WARNING**: Liquidity insufficient for options trading")
        lines.append("")

    # Earnings
    earnings = ctx.get("earnings") or {}
    if earnings:
        days = earnings.get("days_to_earnings")
        zone = earnings.get("zone", "UNKNOWN")
        edate = earnings.get("earnings_date", "—")
        status = earnings.get("date_status")
        status_str = " [{}]".format(status) if status else ""
        if days is not None:
            lines.append("**Earnings**: {} ({} days){} — Zone: **{}**".format(
                edate, days, status_str, zone
            ))
        elif zone == "CLEAR":
            lines.append("**Earnings**: No upcoming earnings found — Zone: **{}**".format(zone))
        else:
            lines.append(
                "**Earnings**: ⚠️ DATA UNAVAILABLE — Zone: **UNKNOWN**. "
                "Earnings gate is NOT cleared: verify the next earnings date by hand "
                "before any short-premium structure."
            )
        lines.append("")

    # 25-delta skew + expected move
    skew = ctx.get("skew_25d") or {}
    if skew.get("available"):
        lines.append(
            "**25Δ Skew** ({}, {} DTE): Put IV {:.1f}% / Call IV {:.1f}% / "
            "Skew {:.1f}pt / α {:.2f}".format(
                skew.get("expiration", "—"), skew.get("dte", "—"),
                skew["put_iv_25d"] * 100, skew["call_iv_25d"] * 100,
                skew["skew_25d_pt"], skew["skew_alpha"],
            )
        )
    em = ctx.get("expected_move")
    if em:
        lines.append(
            "**30D 1σ Expected Move**: ±${:.2f} (±{:.2f}%) → ${:.2f} – ${:.2f} "
            "(IV {:.1f}%, {})".format(
                em["move_1sigma"], em["move_pct"], em["lower"], em["upper"],
                em["iv"] * 100, em.get("iv_source", "—"),
            )
        )
    if skew.get("available") or em:
        lines.append("")

    # Term Structure
    ts = ctx.get("term_structure") or []
    if ts:
        lines.append("### Term Structure")
        lines.append("| Expiration | DTE | ATM IV | ATM Strike |")
        lines.append("|------------|-----|--------|------------|")
        for t in ts:
            iv_pct = "{:.1f}%".format(t["atm_iv"] * 100) if t.get("atm_iv") else "—"
            lines.append("| {} | {} | {} | ${:.0f} |".format(
                t.get("expiration", "—"),
                t.get("dte", "—"),
                iv_pct,
                t.get("atm_strike", 0),
            ))
        lines.append("")

    # Kill Conditions
    kills = ctx.get("kill_conditions") or []
    if kills:
        lines.append("### Kill Conditions")
        for k in kills:
            desc = k.get("description", "") if isinstance(k, dict) else str(k)
            lines.append("- {}".format(desc))
        lines.append("")

    # Deep Analysis Summary
    deep = ctx.get("deep_analysis") or {}
    if deep.get("executive_summary"):
        lines.append("### Deep Analysis Summary")
        lines.append(deep["executive_summary"][:500])
        if deep.get("debate_verdict"):
            lines.append("\n**Debate Verdict**: {}".format(deep["debate_verdict"]))
        lines.append("")

    return "\n".join(lines)


def _format_snapshot_freshness(fresh: Dict[str, Any]) -> str:
    status = fresh.get("status") or "MISSING"
    snap = fresh.get("snapshot_date") or "—"
    age = fresh.get("age_days")
    age_str = "{}d old".format(age) if age is not None else "age unknown"
    if status == "FRESH":
        return "**Snapshot Freshness**: {} ({}) — FRESH".format(snap, age_str)
    if status == "STALE":
        return (
            "**Snapshot Freshness**: {} ({}) — ⚠️ STALE: 只给结构，不给实盘报价".format(snap, age_str)
        )
    return "**Snapshot Freshness**: ❌ MISSING — 无可用期权快照，不给实盘报价"


def _money(v: float) -> str:
    return "${:,.0f}".format(v)


def _format_holdings(pf: Optional[Dict[str, Any]]) -> List[str]:
    lines = ["### Actual Holdings (Google Sheet (Whole Port Net Total), 只读)"]
    if pf is None or pf.get("portfolio_status") == "UNKNOWN" or pf.get("estimated_nav") is None:
        lines.append("- ⚠️ 持仓 / NAV 未加载 (UNKNOWN)：Google Sheet 不可用或 Whole Port "
                     "Net Total 缺失；实际持仓必须问 Boss，Max Loss 预算不给")
        lines.append("- {}".format((pf or {}).get("option_legs_note") or "期权腿未纳入"))
        return lines

    holding = pf.get("holding")
    if holding:
        lines.append("- 正股: {:g} 股 @ ${:.2f}".format(holding["shares"], holding["avg_cost"]))
    else:
        lines.append("- 未持有正股")
    lines.append("- {}".format(pf.get("option_legs_note") or "期权腿未纳入"))
    lines.append(
        "- NAV (Whole Port 投资 Net Total): {} | Max Loss 2%: {} | 3%: {}".format(
            _money(pf["estimated_nav"]), _money(pf["max_loss_2pct"]),
            _money(pf["max_loss_3pct"]))
    )
    return lines


def format_chain_table(
    contracts: List[Dict[str, Any]],
    side: Optional[str] = None,
) -> str:
    """Format option contracts as an aligned text table.

    Args:
        contracts: List of contract dicts
        side: Filter by 'call' or 'put' (None = both)

    Returns:
        Formatted markdown table
    """
    if side:
        contracts = [c for c in contracts if c.get("side") == side]

    if not contracts:
        return "(No contracts)"

    lines = []
    header = "| Strike | Side | Bid | Ask | Mid | IV | Delta | OI | Vol |"
    sep = "|--------|------|-----|-----|-----|-----|-------|------|-----|"
    lines.append(header)
    lines.append(sep)

    for c in contracts:
        strike = "${:.0f}".format(c.get("strike", 0))
        side_str = c.get("side", "—")[:1].upper()
        bid = "{:.2f}".format(c["bid"]) if c.get("bid") is not None else "—"
        ask = "{:.2f}".format(c["ask"]) if c.get("ask") is not None else "—"
        mid = "{:.2f}".format(c["mid"]) if c.get("mid") is not None else "—"
        iv = "{:.1f}%".format(c["iv"] * 100) if c.get("iv") is not None else "—"
        delta = "{:.2f}".format(c["delta"]) if c.get("delta") is not None else "—"
        oi = "{:,}".format(c["open_interest"]) if c.get("open_interest") is not None else "—"
        vol = "{:,}".format(c["volume"]) if c.get("volume") is not None else "—"

        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            strike, side_str, bid, ask, mid, iv, delta, oi, vol
        ))

    return "\n".join(lines)


def format_strategy_comparison(strategies: List[Dict[str, Any]]) -> str:
    """Format 2-3 strategies side-by-side for comparison.

    Args:
        strategies: List of strategy dicts with keys:
            name, structure, max_profit, max_loss, breakeven,
            risk_reward, net_cost, greeks

    Returns:
        Formatted markdown comparison
    """
    if not strategies:
        return "(No strategies to compare)"

    lines = []
    lines.append("### Strategy Comparison")
    lines.append("")

    # Header
    names = [s.get("name", "Strategy") for s in strategies]
    header = "| | " + " | ".join(names) + " |"
    sep = "|---|" + "|".join(["---"] * len(names)) + "|"
    lines.append(header)
    lines.append(sep)

    fields = [
        ("Structure", "structure"),
        ("Net Cost", "net_cost"),
        ("Max Profit", "max_profit"),
        ("Max Loss", "max_loss"),
        ("Breakeven", "breakeven"),
        ("Risk/Reward", "risk_reward"),
        ("Delta", "delta"),
        ("Theta", "theta"),
    ]

    for label, key in fields:
        values = []
        for s in strategies:
            val = s.get(key, "—")
            if isinstance(val, float):
                if key in ("delta", "theta"):
                    val = "{:.2f}".format(val)
                else:
                    val = "${:.2f}".format(val)
            values.append(str(val))
        lines.append("| **{}** | {} |".format(label, " | ".join(values)))

    return "\n".join(lines)


def format_probability_summary(prob: Dict[str, Any]) -> str:
    """Format probability-weighted P&L summary (Phase 2 compact version).

    Args:
        prob: Output from compute_probability_summary()

    Returns:
        3-line compact summary string
    """
    epnl = prob.get("expected_pnl", 0)
    win = prob.get("win_probability", 0)
    median = prob.get("median_pnl", 0)
    p25 = prob.get("pctl_25", 0)

    sign = "+" if epnl >= 0 else ""
    return (
        "Expected P&L: {}{:.0f} | Win Prob: {:.0f}%\n"
        "Median P&L: {}{:.0f} | Worst 25%: {}{:.0f}"
    ).format(
        sign, epnl,
        win * 100,
        "+" if median >= 0 else "", median,
        "+" if p25 >= 0 else "", p25,
    )


def _signed(v: float) -> str:
    return "{}{:.0f}".format("+" if v >= 0 else "", v)


def format_dual_probability_summary(dual: Dict[str, Any]) -> str:
    """Format Q vs P vs half-view comparison.

    Args:
        dual: Output from compute_dual_probability_summary()
    """
    lines = []
    lines.append("**Q vs P 双分布** ({}d horizon, 已扣滑点 ${:.0f}):".format(
        dual.get("days_forward", 0), dual.get("slippage_dollars", 0)
    ))
    lines.append("| 分布 | Drift | Vol | EV | Win Prob | Median | Worst 25% |")
    lines.append("|------|------:|----:|---:|---------:|-------:|----------:|")
    rows = [
        ("Q 风险中性", dual.get("r"), dual.get("market_iv"), "ev_q", "q_summary"),
        ("P Boss View", dual.get("view_drift"), dual.get("view_vol"), "ev_p", "p_summary"),
        ("View 折半压力", dual.get("half_view_drift"), dual.get("half_view_vol"),
         "ev_half_view", "half_view_summary"),
    ]
    for label, drift, vol, ev_key, sum_key in rows:
        summ = dual.get(sum_key) or {}
        lines.append("| {} | {} | {} | {} | {:.0f}% | {} | {} |".format(
            label,
            "{:.1f}%".format(drift * 100) if drift is not None else "—",
            "{:.1f}%".format(vol * 100) if vol is not None else "—",
            _signed(dual.get(ev_key, 0)),
            summ.get("win_probability", 0) * 100,
            _signed(summ.get("median_pnl", 0)),
            _signed(summ.get("pctl_25", 0)),
        ))

    lines.append("Edge (EV_P − EV_Q): {} — view 的价值，不是确定收益".format(
        _signed(dual.get("edge_ev", 0))
    ))
    if dual.get("ev_p", 0) > 0 and dual.get("ev_half_view", 0) < 0:
        lines.append("⚠️ View 折半后 EV 转负：edge 依赖 view 的强度")
    if dual.get("expected_move_1sigma") is not None:
        lines.append("1σ Move: ±${:.2f} (±{:.2f}%)".format(
            dual["expected_move_1sigma"], dual.get("expected_move_pct", 0)
        ))
    if dual.get("target_price") and dual.get("prob_target_q") is not None:
        lines.append("Target ${:.2f}: P_Q {:.0f}% | P_P {:.0f}%".format(
            dual["target_price"], dual["prob_target_q"] * 100, dual["prob_target_p"] * 100
        ))
    return "\n".join(lines)


def format_scenario_matrix(result: Dict[str, Any], strategy_name: str = "") -> str:
    """Format full scenario analysis as markdown (Phase 3 expanded version).

    Args:
        result: Output from generate_scenario_matrix()
        strategy_name: Display name for the strategy

    Returns:
        Formatted markdown string with P&L matrix, IV sensitivity, probability summary
    """
    lines = []
    S = result.get("underlying_price", 0)
    avg_iv = result.get("avg_iv", 0)
    max_dte = result.get("max_dte", 0)
    # Calendars: time axis ends at the shortest leg
    axis_dte = result.get("min_dte", max_dte)
    skew = result.get("skew_alpha", 0)

    # Header
    if strategy_name:
        lines.append("### {} Scenario Analysis".format(strategy_name))
    else:
        lines.append("### Scenario Analysis")
    lines.append("S=${:.2f} | IV={:.1f}% | DTE={} | Skew \u03b1={:.2f}".format(
        S, avg_iv * 100, axis_dte, skew
    ))
    lines.append("")

    # P&L Matrix
    price_points = result.get("price_points", [])
    time_points = result.get("time_points", [])
    pnl_matrix = result.get("pnl_matrix", {})
    sigma_labels = result.get("sigma_labels", {})

    # Header row: | Price (σ) | Entry | +7d | +14d | Expiry |
    time_headers = []
    for d in time_points:
        if d == 0:
            time_headers.append("Entry")
        elif d == axis_dte:
            time_headers.append("Expiry")
        else:
            time_headers.append("+{}d".format(d))

    header = "| Price | " + " | ".join(time_headers) + " |"
    sep = "|------:" + "|------:" * len(time_points) + "|"
    lines.append(header)
    lines.append(sep)

    for price in price_points:
        label = sigma_labels.get(price, "")
        price_col = "${:.0f} ({})".format(price, label)
        values = []
        for d in time_points:
            pnl = pnl_matrix.get(price, {}).get(d, 0)
            sign = "+" if pnl >= 0 else ""
            values.append("{}{:.0f}".format(sign, pnl))
        lines.append("| {} | {} |".format(price_col, " | ".join(values)))

    lines.append("")

    # IV Sensitivity
    iv_sens = result.get("iv_sensitivity", {})
    eval_day = result.get("iv_eval_day", 7)
    if iv_sens:
        lines.append("**IV Sensitivity** (+{}d, current price):".format(eval_day))
        parts = []
        for label, pnl in iv_sens.items():
            sign = "+" if pnl >= 0 else ""
            if pnl >= 0:
                parts.append("IV {}: +${:.0f}".format(label, pnl))
            else:
                parts.append("IV {}: -${:.0f}".format(label, abs(pnl)))
        lines.append("  " + " | ".join(parts))
        lines.append("")

    # Probability Summary
    prob = result.get("probability_summary", {})
    horizon = result.get("probability_horizon_days", 0)
    if prob:
        lines.append("**Probability-Weighted Summary** ({}d horizon):".format(horizon))
        epnl = prob.get("expected_pnl", 0)
        win = prob.get("win_probability", 0)
        median = prob.get("median_pnl", 0)
        p25 = prob.get("pctl_25", 0)
        p75 = prob.get("pctl_75", 0)
        lines.append("  E[P&L]: {}{:.0f} | Win Prob: {:.0f}%".format(
            "+" if epnl >= 0 else "", epnl, win * 100
        ))
        lines.append("  25th: {}{:.0f} | Median: {}{:.0f} | 75th: {}{:.0f}".format(
            "+" if p25 >= 0 else "", p25,
            "+" if median >= 0 else "", median,
            "+" if p75 >= 0 else "", p75,
        ))

    return "\n".join(lines)


def format_trade_memo(memo: Dict[str, Any]) -> str:
    """Format a trade memo for the user.

    Args:
        memo: Dict with trade details

    Returns:
        Formatted trade memo string
    """
    lines = []
    lines.append("```")
    lines.append("=" * 40)
    lines.append("          TRADE MEMO")
    lines.append("=" * 40)

    fields = [
        ("Symbol", memo.get("symbol")),
        ("Strategy", memo.get("strategy")),
        ("Structure", memo.get("structure")),
        ("Cost", memo.get("net_cost")),
        ("Max Profit", memo.get("max_profit")),
        ("Max Loss", memo.get("max_loss")),
        ("Breakeven", memo.get("breakeven")),
        ("Risk/Reward", memo.get("risk_reward")),
        ("Contracts", memo.get("contracts")),
        ("Expiry", memo.get("expiry")),
        ("DTE", memo.get("dte")),
    ]

    for label, value in fields:
        if value is not None:
            lines.append("  {:<14} {}".format(label + ":", value))

    # Greeks
    greeks = memo.get("greeks")
    if greeks:
        lines.append("")
        lines.append("  Greeks:")
        for g, v in greeks.items():
            if v is not None:
                lines.append("    {:<10} {}".format(g.capitalize() + ":", v))

    # Management plan
    plan = memo.get("management_plan")
    if plan:
        lines.append("")
        lines.append("  Management Plan:")
        for item in plan:
            lines.append("    - {}".format(item))

    lines.append("=" * 40)
    lines.append("```")
    return "\n".join(lines)
