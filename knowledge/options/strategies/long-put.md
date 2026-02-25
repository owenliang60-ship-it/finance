# Strategy: Long Put (买入看跌期权)
> 买方/方向+对冲 | Complexity: Beginner | Max Risk: Defined | IV Environment: Low

## Overview
A long put gives you the right to sell the underlying at a fixed strike price, providing leveraged bearish exposure or portfolio insurance. It is the cleanest way to profit from a decline or hedge existing long stock positions. Like the long call, risk is capped at the premium paid, making it a defined-risk bearish bet. Best used when IV is low and you expect a sharp move down.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Put  | ATM or slightly OTM | 30-90 DTE |

- Single leg, net debit trade.
- Max loss = premium paid. Max gain = strike price - premium (stock goes to $0).

## When to Use
- **Directional**: Bearish conviction with a catalyst (earnings miss, macro deterioration, broken support)
- **Hedge**: Protect unrealized gains on a long stock position (保险策略)
- IV rank < 30% — puts are cheap relative to history
- Expecting a sharp, fast decline (puts benefit from rising IV during sell-offs)
- Short selling is restricted, unavailable, or you want defined risk

## When NOT to Use
- IV rank > 50% — you're overpaying; use a bear put spread instead
- Slow bearish thesis with no catalyst — theta will grind you down
- The stock pays a large dividend soon (put value partially prices this in already)
- Your "hedge" costs more than the potential loss it prevents (cost-benefit analysis)
- Market is in a low-volatility uptrend — fighting the trend with long puts is expensive

## Entry Criteria
- **IV Environment**: IV rank < 30% for directional plays; for hedges, accept higher IV if protection is urgent
- **Direction**: Bearish with identifiable catalyst or clear technical breakdown
- **Time Frame**: 45-90 DTE for swing trades; 7-14 DTE for earnings-driven plays
- **Liquidity**: Bid-ask spread < 5% of mid; OI > 500 on chosen strike
- **Put skew**: Check if put skew is elevated — if puts are much more expensive than calls, the "insurance" is already priced in

## Strike & Expiry Selection
- **ATM puts** (delta ~-0.50): Best for directional conviction. Balanced cost and sensitivity.
- **Slightly OTM** (delta -0.30 to -0.40): Cheaper, but needs a bigger move. Good for short-term event plays.
- **ITM puts** (delta -0.70 to -0.80): More expensive but behave like short stock with defined risk. Ideal for hedging.
- **For hedging a $100 stock**: Buy the 95 or 90 put (5-10% OTM). Think of it as a deductible — you absorb the first 5-10% loss.
- **Expiry**: Match to your risk window. Hedging for earnings? Buy puts that expire 1 week after the event. Portfolio insurance? Buy 60-90 DTE and roll quarterly.

## Position Sizing
- **Directional**: Risk 1-2% of portfolio per position. `Contracts = (Portfolio × 0.01) / (Premium × 100)`
- **Hedge**: Size to cover the specific position. 100 shares of stock = 1 put contract. For partial hedge, buy fewer contracts.
- Example: 500 shares of NVDA at $120, want to protect below $110. Buy 5 puts at the $110 strike. Cost = insurance premium.
- Hedge cost should not exceed 2-3% of the position value per quarter (成本控制)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | -0.30 to -0.60 (depends on strike) | Becomes more negative as stock falls |
| Gamma | Moderate positive | Accelerates near expiry; works for you in a crash |
| Theta | Negative (enemy) | Accelerates after 30 DTE; constant bleed |
| Vega  | Positive (ally) | Sell-offs typically spike IV, amplifying put gains (双重获利) |

## Exit Protocol
- **Profit Target**: Close at 50-100% gain for directional plays. For hedges, close the put when you close the stock.
- **Stop Loss**: Close if premium drops to 50% of entry. Reassess thesis.
- **Time Stop**: If < 21 DTE and not profitable, close or roll. Exception: if holding as catastrophe insurance, let it expire worthless (that's the plan).
- **Adjustment Trigger**: If stock drops but not enough, and IV has risen, you may be profitable from vega alone — consider taking the vega win.

## Adjustments & Rolling
- **Roll out**: Sell current put, buy same strike with more DTE. Use when thesis is intact but move is delayed.
- **Roll down and out**: If stock drops partially, sell current put, buy a lower strike with more DTE. This books partial profit.
- **Convert to spread**: If IV spikes after entry, sell a lower-strike put to create a bear put spread. Locks in vega profit.
- **Hedge roll**: For portfolio insurance, roll 30 days before expiry to maintain continuous coverage. Sell the expiring put (still has some value) and buy the next cycle.
- **Profit-taking on hedge**: If the stock drops sharply, your put is very profitable. Consider selling the put and replacing it with a cheaper OTM put to lock in gains while maintaining some protection.

## Risk Controls
- Max loss per directional put: 1-2% of portfolio
- Max hedge cost: 0.5-1.0% of portfolio per quarter (年化 2-4%，类似保险费)
- Don't stack bearish puts during a strong uptrend — you're fighting the tape
- Track total negative delta exposure: Ensure your hedges don't accidentally make you net short
- Monitor put/call skew: If skew is extreme (>10% above call IV), puts are expensive — consider alternatives

## Common Traps
- **Using long puts as a primary income strategy**: Puts lose money most of the time. Stocks go up more than they go down. Use puts surgically, not habitually.
- **Buying puts after a stock has already dropped 20%**: IV is now elevated, puts are expensive, and the easy move may be over. You're buying insurance after the house is on fire.
- **Over-hedging**: Spending 5% of portfolio annually on puts that expire worthless is a massive drag on returns. Hedge specific risks, not general anxiety.
- **Ignoring the vol tail wind**: Puts have a unique advantage — when you're right (stock drops), IV usually rises too, giving you a double tailwind. This is why puts can be more profitable than expected in crashes.
- **Forgetting assignment risk**: Deep ITM puts near expiry may be assigned. If you don't want to be short stock, close before expiry.
- **Confusing "cheap" with "good value"**: A $0.30 far-OTM put expiring in 2 weeks is not a hedge — it's a prayer. Effective hedges cost real money.
