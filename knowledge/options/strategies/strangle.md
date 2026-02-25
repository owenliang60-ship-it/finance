# Strategy: Strangle (宽跨式组合 — Long & Short)
> 波动率策略 | Complexity: Intermediate | Max Risk: Varies | IV Environment: Varies

## Overview
A strangle is similar to a straddle but uses different strikes for the call and put, both OTM. This makes it cheaper (long) or wider-ranged (short) compared to a straddle. A **long strangle** is a cheaper bet on a big move in either direction, while a **short strangle** profits from range-bound price action with a wider margin of safety than a short straddle. The trade-off: the stock needs to move more for a long strangle to profit, but a short strangle gives more breathing room before losing.

## Structure

### Long Strangle (做多波动率 — 低成本版)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Call | OTM (above current price) | 30-60 DTE |
| 2   | Buy       | Put  | OTM (below current price) | Same expiry |

- Net debit. Max loss = total premium. Max gain = unlimited (up) / lower strike - premium (down).

### Short Strangle (做空波动率 — 宽缓冲版)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Sell      | Call | OTM (above current price) | 30-45 DTE |
| 2   | Sell      | Put  | OTM (below current price) | Same expiry |

- Net credit. Max gain = total credit. Max loss = unlimited on both sides.
- **Requires margin.** Undefined risk on both sides.

## When to Use

### Long Strangle
- Expecting a large move but direction is unclear
- IV rank < 20% — cheaper than straddles and volatility is near historic lows
- Ahead of major catalysts: earnings, macro events, geopolitical uncertainty
- When the straddle is too expensive but you still want long vol exposure
- Stock has history of moving more than the strangle price around events

### Short Strangle
- Rangebound market with identifiable support and resistance
- IV rank > 70% — fat premiums provide wide profit zone
- After IV spikes (post-earnings, post-event) when IV is expected to normalize
- High-quality stocks that move slowly (low realized vol relative to implied)
- Income generation with wider safety margin than straddles

## When NOT to Use

### Long Strangle
- IV rank > 40% — both options are expensive; use a different structure
- No catalyst for a move — you're just donating theta to sellers
- Stock historically stays within the strangle range around events (check implied move)
- Very illiquid options — wide bid-ask on OTM strikes makes entry and exit expensive

### Short Strangle
- Binary events (earnings, FDA) within the spread's lifetime
- IV rank < 40% — insufficient premium for the risk
- Stock has beta > 1.5 or history of gap moves
- You cannot monitor and adjust the position at least daily
- Bear market / high-correlation sell-off environment (everything drops together)

## Entry Criteria

### Long Strangle
- **IV Environment**: IV rank < 20%. Both OTM options should be cheap.
- **Catalyst**: Must exist. Without it, you're bleeding theta with no payoff.
- **Time Frame**: 45-60 DTE for swing; 5-10 DTE for event plays.
- **Liquidity**: OI > 500 on both strikes; bid-ask < 5% of option price.
- **Price check**: Strangle cost should be < 75% of the expected move (based on historical events).

### Short Strangle
- **IV Environment**: IV rank > 70%. Premium must justify the undefined risk.
- **Time Frame**: 30-45 DTE (theta sweet spot).
- **Liquidity**: OI > 1000 on both strikes; penny-wide markets preferred.
- **Width**: Put delta -0.15 to -0.20; call delta +0.15 to +0.20. Probability of profit > 70%.
- **Margin**: Maintain 3x credit received in available margin.

## Strike & Expiry Selection

### Long Strangle
- **Call strike**: Delta +0.25 to +0.35. Close enough to participate in a rally.
- **Put strike**: Delta -0.25 to -0.35. Close enough to capture a sell-off.
- **Symmetric vs. skewed**: Use roughly equal deltas for a direction-neutral bet. Skew toward calls if slightly bullish (buy closer call, further put) or vice versa.
- **Expiry**: 1.5-2x the expected holding period. If catalyst is in 30 days, buy 45-60 DTE.

### Short Strangle
- **Call strike**: Delta +0.15 to +0.20. Above the nearest strong resistance.
- **Put strike**: Delta -0.15 to -0.20. Below the nearest strong support.
- **Width between strikes**: Should encompass the expected range for the holding period. Check the expected move (straddle price as % of stock).
- **Expiry**: 30-45 DTE. Beyond 45 DTE, you're not getting enough theta benefit for the extended risk window.

## Position Sizing

### Long Strangle
- Risk 1-2% of portfolio. Max loss = full premium.
- `Contracts = (Portfolio × 0.015) / (Strangle Price × 100)`
- Typically cheaper than straddles, so you can buy slightly more contracts for the same dollar risk.

### Short Strangle
- Size by max tolerable loss (2-3x credit) since max loss is unlimited.
- `Contracts = (Portfolio × 0.03) / (3 × Credit × 100)`
- Keep total short strangle buying power usage < 25% of portfolio.
- Account for worst-case margin expansion during volatile moves.

## Greeks Profile

### Long Strangle
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (delta neutral) | Swings positive or negative with stock movement |
| Gamma | Positive but less than straddle | Needs bigger move to generate meaningful delta |
| Theta | Negative (enemy) | Daily bleed, though less than straddle dollar-for-dollar |
| Vega  | Positive (ally) | IV expansion helps both legs |

### Short Strangle
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (delta neutral) | Drifts as stock moves toward either wing |
| Gamma | Negative (enemy) | Rapid moves create unhedged directional exposure |
| Theta | Positive (ally) | Decays both legs simultaneously — double theta collection |
| Vega  | Negative (ally when IV drops) | IV contraction is profit; expansion is pain on both sides |

## Exit Protocol

### Long Strangle
- **Profit Target**: Close at 50-100% gain. Strangles need bigger moves, so be patient but not stubborn.
- **Stop Loss**: Close if strangle value drops to 40% of entry. The move isn't coming.
- **Time Stop**: Close at 21 DTE if no significant move has occurred.
- **Post-event**: Close immediately. IV crush hits both legs. Take whatever profit (or loss) the event gave you.

### Short Strangle
- **Profit Target**: Close at 50% of max credit. $3.00 collected → buy back at $1.50.
- **Stop Loss**: Close if either leg reaches 2x its individual credit (not the total). If you sold the call for $1.50 and it's now $3.00, close the call side at minimum.
- **Time Stop**: Close at 21 DTE if at 50%+ profit. If not profitable, evaluate whether to roll or close.
- **Breach protocol**: If either strike is breached, close that side immediately. Do not hope.

## Adjustments & Rolling

### Long Strangle
- **Close the losing leg**: If stock moves decisively, close the dead leg to recover premium. Let the winner run.
- **Convert to spread**: If one leg is profitable, sell a further OTM option against it to lock in gains and reduce theta.
- Don't roll long strangles — they're event-driven trades. If the event passed, close the trade.

### Short Strangle
- **Roll the tested side**: If the call is threatened, roll it up and out for a credit. Same for the put — roll down and out.
- **Invert if necessary**: In extreme cases, both sides can be rolled past each other ("inverted strangle"). Only do this if you can maintain a net credit overall.
- **Delta hedge**: Buy/sell shares to flatten delta when the stock moves. Active management.
- **Widen the strangle**: If both sides are safe, roll strikes further OTM for less credit but more safety.
- **Max 2 rolls**: If the trade has been rolled twice and is still losing, close it. The market is telling you something.

## Risk Controls
- **Long**: Max 2% per position; max 5% total long vol exposure
- **Short**: Max 3% notional risk; 3x credit in margin buffer; daily monitoring mandatory
- Never hold a short strangle through earnings without explicit event-trading intent
- Portfolio-level vol exposure cap: Net vega across all positions should not exceed 0.5% of portfolio value per 1% IV move
- Correlation check: One short strangle per sector maximum
- Use portfolio stress test: "What happens if the stock gaps 10% either way?" If the answer is unacceptable, reduce size.

## Common Traps
- **Long strangle: Buying too far OTM for "cheapness"**: A $1.00 strangle with delta +-0.10 strikes needs a 15% move to break even. That's not cheap — it's improbable. Stay at delta 0.25-0.35.
- **Short strangle: Ignoring correlation risk**: Five short strangles on mega-cap tech is one bet on "tech stays flat." A sector rotation wipes out all five.
- **Both: Confusing high IV rank with high IV level**: IV rank 90% on a stock that normally has 15% IV means IV is at 25%. That's not necessarily expensive in absolute terms. Context matters.
- **Short strangle: Adding to losers**: "The premium is even better now!" — Yes, because the risk is worse. Never add to a losing short strangle.
- **Long strangle: IV crush after event**: The most common failure mode. You bought the strangle for earnings, the stock moved 3%, and your strangle lost money because IV dropped 40%. Always compare strangle cost to expected move.
- **Short strangle: Asymmetric risk management**: Managing only the tested side while ignoring the other. After rolling the put down, the call side might now be too close. Manage both sides holistically.
