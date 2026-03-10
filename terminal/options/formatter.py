"""
Options Formatter — Format chain data, Greeks, P/L, and comparison tables.

Used by commands.py and the /options skill to present data to the user.
"""
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

    # Price + OPRMS
    oprms = ctx.get("oprms") or {}
    price_str = "${:.2f}".format(price) if price else "N/A"
    dna = oprms.get("dna", "—")
    timing = oprms.get("timing", "—")
    pos_pct = oprms.get("position_pct")
    pos_str = "{:.1f}%".format(pos_pct) if pos_pct else "—"

    lines.append("**Price**: {} | **OPRMS**: DNA={} Timing={} | **Position**: {}".format(
        price_str, dna, timing, pos_str
    ))
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
        if days is not None:
            lines.append("**Earnings**: {} ({} days) — Zone: **{}**".format(
                edate, days, zone
            ))
        else:
            lines.append("**Earnings**: No upcoming earnings found — Zone: **{}**".format(zone))
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
    skew = result.get("skew_alpha", 0)

    # Header
    if strategy_name:
        lines.append("### {} Scenario Analysis".format(strategy_name))
    else:
        lines.append("### Scenario Analysis")
    lines.append("S=${:.2f} | IV={:.1f}% | DTE={} | Skew \u03b1={:.2f}".format(
        S, avg_iv * 100, max_dte, skew
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
        elif d == max_dte:
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
