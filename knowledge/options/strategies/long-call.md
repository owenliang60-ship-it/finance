# Strategy: Long Call (买入看涨期权)
> 买方/方向性 | Complexity: Beginner | Max Risk: Defined | IV Environment: Low

## Overview
A long call is the most straightforward bullish options strategy: you pay a premium for the right to buy the underlying at a fixed strike price before expiration. It offers leveraged upside exposure with risk strictly limited to the premium paid. Best deployed when you have a strong directional conviction and implied volatility is relatively cheap, so you're not overpaying for optionality.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Call | ATM or slightly OTM | 30-90 DTE |

- Single leg, net debit trade.
- Max loss = premium paid. Max gain = theoretically unlimited.

## When to Use
- Strong bullish conviction on the underlying (催化剂明确)
- IV rank < 30% — options are cheap relative to historical norms
- Expecting a significant move within the option's lifetime (earnings, product launch, FDA)
- Want leveraged exposure without margin requirements
- As a stock replacement strategy to free up capital

## When NOT to Use
- IV rank > 50% — you're overpaying for time value; consider a spread instead
- No clear catalyst or timeline for the move
- Expecting a slow, grinding move up — theta will erode your position
- Underlying has low liquidity or wide bid-ask spreads
- You cannot afford to lose the entire premium

## Entry Criteria
- **IV Environment**: IV rank < 30%, ideally bottom quartile of 1-year range
- **Direction**: Clearly bullish; technical breakout or fundamental catalyst imminent
- **Time Frame**: 45-90 DTE for swing trades; 7-21 DTE for event-driven (earnings)
- **Liquidity**: Bid-ask spread < 5% of mid price; OI > 500 on chosen strike
- **Underlying**: Beta > 1.0 preferred (more bang for your buck)

## Strike & Expiry Selection
- **ATM calls** (delta ~0.50): Best balance of cost and probability. Use when conviction is moderate.
- **Slightly OTM** (delta 0.30-0.40): Lower cost, higher leverage, but needs a bigger move. Use when conviction is high.
- **ITM calls** (delta 0.70-0.80): Stock replacement strategy. Higher cost but less time value decay.
- **Expiry**: Buy at least 1.5x the expected holding period. If you expect the move in 30 days, buy 45-60 DTE minimum. This gives you a buffer against timing errors.
- Avoid weeklies unless it's a pure event play (earnings) — gamma risk cuts both ways.

## Position Sizing
- Risk no more than 1-2% of portfolio per long call position (全仓位风险控制)
- Premium paid = max loss. Size accordingly: `Contracts = (Portfolio × 0.01) / (Premium × 100)`
- Example: $500K portfolio, 1% risk = $5,000 max loss. If call costs $8.50, buy 5 contracts ($4,250).
- Never "average down" on a losing long call — that's doubling your theta bleed.

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | +0.30 to +0.60 (depends on strike) | Increases if stock rises (toward +1.0) |
| Gamma | Moderate positive | Accelerates near expiry (risk and reward) |
| Theta | Negative (enemy) | Accelerates after 30 DTE; steepest in final 2 weeks |
| Vega  | Positive (ally) | Decreases as expiry approaches; IV crush post-event hurts |

## Exit Protocol
- **Profit Target**: Close at 50-100% gain on the premium paid. Don't hold for the "ten-bagger."
- **Stop Loss**: Close if premium declines to 50% of entry cost. Or set a stock-price stop.
- **Time Stop**: If < 21 DTE and trade is not profitable, close or roll. Theta accelerates here.
- **Adjustment Trigger**: If stock is moving but slower than expected, consider rolling to a later expiry before theta kicks in hard.

## Adjustments & Rolling
- **Roll out**: If thesis is intact but timing was off, sell current call and buy a later-dated call at the same strike. Capture remaining extrinsic value.
- **Roll up and out**: If the stock is rising and you want to lock in some gains, sell current call, buy a higher strike with more DTE. This books partial profit and resets theta.
- **Convert to spread**: If IV spikes after entry, sell an OTM call against your long to create a bull call spread. This locks in the vega gain and reduces cost basis.
- **Close early on IV spike**: If IV jumps 20%+ before the catalyst, consider closing early since the vega gain may exceed what the directional move would add.

## Risk Controls
- Max loss per position: 1-2% of portfolio
- Max total long call exposure: 5% of portfolio across all positions
- Correlation check: Don't stack 3 long calls on tech stocks — that's one bet, not three (关联性控制)
- Track aggregate theta: If your portfolio bleeds > $500/day in theta, you're over-allocated to long options
- Size down when VIX > 25 — even "cheap" single-name IV may be elevated

## Common Traps
- **Buying too short-dated**: The #1 beginner mistake. Weekly calls are lottery tickets, not investments. Buy more time than you think you need.
- **Ignoring IV rank**: Buying calls when IV rank > 60% means you're paying a volatility premium. Even if you're right on direction, IV contraction can eat your profits.
- **Holding through earnings when that wasn't the plan**: IV crush after earnings can destroy 30-50% of premium overnight, even if the stock moves your way.
- **Falling in love with the trade**: Set your exit rules at entry. "It'll come back" is how 50% losses become 100% losses.
- **Not understanding breakeven**: Your breakeven is strike + premium. The stock needs to move past that point by expiry for you to profit. A $5 call on a $100 stock means you need $105 to break even — that's a 5% move.
- **Buying deep OTM for "cheap" exposure**: A $0.50 call is not "cheap" if it has a 5% probability of being ITM. Think in terms of expected value, not absolute price.
