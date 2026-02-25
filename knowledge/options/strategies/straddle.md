# Strategy: Straddle (跨式组合 — Long & Short)
> 波动率策略 | Complexity: Intermediate | Max Risk: Varies | IV Environment: Varies

## Overview
A straddle combines a call and a put at the same strike price and expiration, creating a pure volatility play. A **long straddle** profits from large moves in either direction (买入波动率), while a **short straddle** profits when the stock stays pinned near the strike (卖出波动率). The straddle is the canonical volatility bet — you're expressing a view on *magnitude of movement*, not direction. The decision between long and short depends entirely on whether implied volatility is cheap or expensive relative to expected realized volatility.

## Structure

### Long Straddle (做多波动率)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Call | ATM | 30-60 DTE |
| 2   | Buy       | Put  | ATM (same) | Same expiry |

- Net debit. Max loss = total premium paid. Max gain = unlimited (upside) / strike - premium (downside).

### Short Straddle (做空波动率)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Sell      | Call | ATM | 30-45 DTE |
| 2   | Sell      | Put  | ATM (same) | Same expiry |

- Net credit. Max gain = total credit received. Max loss = unlimited (upside) / strike - credit (downside).
- **Requires margin.** Undefined risk.

## When to Use

### Long Straddle
- Expecting a big move but unsure of direction (earnings, FDA, trial verdict)
- IV rank < 25% — implied volatility is cheap relative to history
- Historical data shows the stock moves more than the straddle costs (跨式定价低于历史波动)
- Ahead of known catalysts where the market is underpricing the event

### Short Straddle
- Expecting the stock to stay range-bound around current price
- IV rank > 70% — implied volatility is expensive and likely to contract
- After a big move when IV has spiked and is expected to normalize
- Stocks with low beta and predictable behavior (utilities, consumer staples — though we filter these out)

## When NOT to Use

### Long Straddle
- IV rank > 50% — you're overpaying for volatility. The move needs to be massive to overcome the cost.
- No catalyst — without a reason for a big move, theta will eat you alive
- Stock historically moves less than the straddle price around similar events

### Short Straddle
- Earnings, FDA events, or any binary catalyst within the timeframe — gap risk is extreme
- IV rank < 40% — not enough premium to justify undefined risk
- You cannot monitor the position daily (short straddles need active management)
- Stock has history of 5%+ intraday moves

## Entry Criteria

### Long Straddle
- **IV Environment**: IV rank < 25%. Cross-check: straddle price < average historical move for the expected event.
- **Catalyst**: Must have a specific reason to expect movement (earnings, product launch, macro event)
- **Time Frame**: 30-60 DTE for swing trades; 3-7 DTE for pure event plays
- **Liquidity**: OI > 1000 on the ATM strike; bid-ask < 3% of straddle price
- **Strike**: Closest to ATM available

### Short Straddle
- **IV Environment**: IV rank > 70%. Premium should be > 1.5x the average expected move.
- **Direction**: Market thesis is rangebound/consolidation
- **Time Frame**: 30-45 DTE. Shorter = more gamma risk. Longer = less theta benefit.
- **Liquidity**: OI > 2000 on ATM; tight bid-ask essential for credit strategies
- **Margin**: Ensure you have 2-3x the credit received in margin available

## Strike & Expiry Selection
- **Strike**: Both legs at the ATM strike (closest to current price). A "true" straddle uses the exact same strike.
- **Expiry (long)**: If event-driven, expiry should be 1-2 weeks after the event. If swing-based, 45-60 DTE to minimize theta bleed before the move.
- **Expiry (short)**: 30-45 DTE. This is the theta-maximizing zone. Avoid weeklies (gamma explosion).
- For long straddles, if the ATM strike has poor liquidity, consider moving to the nearest liquid strike even if slightly off-center.

## Position Sizing

### Long Straddle
- Risk 1-2% of portfolio. Max loss = full premium.
- `Contracts = (Portfolio × 0.015) / (Straddle Price × 100)`
- Example: $500K portfolio, straddle costs $12.00 → 6 contracts ($7,200 risk)

### Short Straddle
- Size by buying power reduction AND margin requirements.
- Max loss is theoretically unlimited. Set a mental max loss of 2-3x the credit received and use that for sizing.
- `Contracts = (Portfolio × 0.03) / (3 × Credit × 100)`
- Keep total undefined risk positions < 20% of portfolio buying power

## Greeks Profile

### Long Straddle
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (delta neutral) | Moves toward +1/-1 depending on stock direction |
| Gamma | Large positive (your edge) | Your best friend — big moves generate delta quickly |
| Theta | Large negative (your enemy) | Destroys your position daily; this is the cost of the bet |
| Vega  | Large positive (ally) | IV expansion profits you even without stock movement |

### Short Straddle
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (delta neutral) | Moves against you as stock moves away from strike |
| Gamma | Large negative (your enemy) | Big moves create rapid delta exposure against you |
| Theta | Large positive (your edge) | Daily income; this is why you sell straddles |
| Vega  | Large negative (ally when IV drops) | IV contraction is profit; IV expansion is severe pain |

## Exit Protocol

### Long Straddle
- **Profit Target**: Close at 25-50% gain on premium paid. Straddles rarely hit max theoretical profit.
- **Stop Loss**: Close if straddle loses 50% of value and no catalyst has arrived yet.
- **Time Stop**: Close at 21 DTE if the expected move hasn't happened. Theta is killing you.
- **Post-event**: Close the morning after the event. IV crush will destroy the losing leg, and possibly both legs.

### Short Straddle
- **Profit Target**: Close at 25% of max credit. Don't be greedy with undefined risk.
- **Stop Loss**: Close if the loss reaches 2x the credit received. No exceptions.
- **Time Stop**: Close at 21 DTE if target hasn't been hit.
- **Delta monitor**: If net delta exceeds +/-0.30 per contract, consider adjusting or closing.

## Adjustments & Rolling

### Long Straddle
- **Close the losing leg early**: If the stock moves decisively one way, close the losing leg to recoup some premium and let the winning leg run.
- **Convert to strangle**: Roll the untested leg closer to ATM to reduce cost basis.
- No rolling — if the catalyst has passed, the trade is over.

### Short Straddle
- **Delta hedge**: If the stock moves significantly, buy/sell shares to flatten delta. This is how market makers manage straddles.
- **Roll the tested side**: If the call is being breached, roll it up. If the put is breached, roll it down. Must maintain net credit.
- **Convert to strangle**: Roll both legs OTM to widen the profitable range (at the cost of reduced credit).
- **Close on breach**: If the stock moves > 1.5x the credit received past either strike, close. The trade is failed.

## Risk Controls
- **Long**: Max 2% of portfolio per straddle. Max 5% total in long vol positions.
- **Short**: Max 3% of portfolio in notional risk. Margin buffer of 3x credit. Daily monitoring required.
- Never sell straddles on stocks with pending binary events
- Correlation: Only one straddle per sector to avoid correlated vol bets
- Short straddle max hold time: 30 days. Close and re-evaluate.

## Common Traps
- **Long straddle: Overpaying for the event**: The market prices earnings moves into straddles. If the straddle costs $10 and the stock historically moves $8 on earnings, you're underwater before it starts. Check the implied move vs. historical.
- **Short straddle: Unlimited risk hubris**: "It always works until it doesn't." One 15% gap wipes out months of premium collected. Respect the stop loss.
- **Both: Ignoring IV rank**: Long straddles in high IV environments and short straddles in low IV environments are both negative expected value by construction.
- **Long straddle: Holding too long after the event**: IV crush post-event devastates straddle value. Take what the market gives you and close.
- **Short straddle: Not adjusting delta**: A short straddle that drifts to delta +0.50 is no longer a vol trade — it's a directional bet. Monitor and hedge.
- **Comparing straddle price to stock price**: A $15 straddle on a $300 stock is a 5% move. A $5 straddle on a $50 stock is a 10% move. Think in percentages, not dollars.
