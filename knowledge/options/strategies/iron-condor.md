# Strategy: Iron Condor (铁鹰式组合)
> 卖方/中性策略 | Complexity: Intermediate | Max Risk: Defined | IV Environment: High

## Overview
An iron condor combines a bull put spread and a bear call spread on the same underlying with the same expiration. You collect premium from both sides, profiting when the stock stays within a defined range. It's the defined-risk version of a short strangle — you cap your losses on both sides using protective wings. This is the flagship strategy for options sellers who want consistent income with controlled risk. You're selling the thesis that "this stock will stay between A and B for the next 30-45 days."

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Put  | Lowest (OTM, wing) | 30-45 DTE |
| 2   | Sell      | Put  | Higher (OTM) | Same expiry |
| 3   | Sell      | Call | Higher still (OTM) | Same expiry |
| 4   | Buy       | Call | Highest (OTM, wing) | Same expiry |

- Net credit trade (4 legs).
- Max gain = total credit received.
- Max loss = wider spread width - credit (only one side can lose at expiry).
- Breakevens: short put - credit (lower) and short call + credit (upper).

## When to Use
- Neutral outlook — expecting rangebound price action for 30-45 days
- IV rank > 50%, ideally > 70% — inflated premiums widen the profit zone (高IV环境下的收割机)
- After a volatility spike when IV is expected to contract
- Stocks with established trading ranges and identifiable support/resistance
- Want to sell premium with defined risk and no margin surprises

## When NOT to Use
- Trending market — iron condors get destroyed by persistent trends
- IV rank < 30% — credit is too small relative to risk
- Earnings or binary events within the holding period
- Stock is breaking out of a long consolidation range
- You can't identify clear support AND resistance levels

## Entry Criteria
- **IV Environment**: IV rank > 50%. The higher the IV, the wider you can set the condor while collecting meaningful credit.
- **Direction**: Neutral. No strong bullish or bearish bias. If you're directional, use a spread instead.
- **Time Frame**: 30-45 DTE. This is non-negotiable for theta optimization.
- **Liquidity**: OI > 500 on all four strikes; bid-ask < $0.05 per leg.
- **Credit target**: Collect at least 1/3 of the narrower spread width. For $5-wide wings, collect $1.65+ total.
- **Expected move check**: Both short strikes should be outside the expected move (约1标准差之外).

## Strike & Expiry Selection
- **Short put**: Delta -0.15 to -0.20 (80-85% probability OTM). Below strong support.
- **Short call**: Delta +0.15 to +0.20. Above strong resistance.
- **Wing width**: $5 standard on $50-200 stocks. $10 on $200+ stocks. Both sides should have equal width for a balanced condor.
- **Symmetric vs. skewed**: Standard iron condors have equal-width spreads on both sides. If slightly bullish, widen the put side. If slightly bearish, widen the call side.
- **The profitable range**: The distance between the two short strikes is your profit zone. For a stock at $100 with short strikes at $90 and $110, the stock can move 10% in either direction without hitting max loss.
- **Expiry**: 30-45 DTE. Always.

## Position Sizing
- Max loss = wider wing width - total credit. Size by this number.
- `Condors = (Portfolio × 0.02) / (Max Loss per condor)`
- Example: $5-wide wings, $2.00 total credit. Max loss = $3.00 per condor ($300). $500K portfolio, 2% = $10,000. Trade 33 condors.
- Max total iron condor exposure: 15% of portfolio across all positions.

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero | Moves toward short strike that is being tested |
| Gamma | Net negative | Biggest risk — fast moves create directional exposure |
| Theta | Positive (primary edge) | Both sides decay simultaneously — double income |
| Vega  | Negative (ally when IV drops) | IV contraction profits both sides. This is your secondary edge. |

## Exit Protocol
- **Profit Target**: Close at 50% of max credit. Collected $2.00 → buy back at $1.00. This is the single most important rule for iron condors.
- **Stop Loss**: Close if the condor's value reaches 2x the credit. Or close the tested side if the short strike is breached.
- **Time Stop**: Close at 21 DTE if at 50%+ profit. If not yet profitable, evaluate rolling vs. closing.
- **One-side breach**: If one side is breached but the other is safe, you have two options: (1) close the entire condor, or (2) close the losing side and hold the winning side (now a standalone credit spread).

## Adjustments & Rolling
- **Roll the tested side**: If the put side is threatened, roll the put spread down and out. Same for calls — roll up and out. Must maintain net credit on the roll.
- **Close the winning side early**: If one side has decayed to $0.05, buy it back and keep the other side open. This frees up margin and reduces commission on the final close.
- **Convert to iron butterfly**: If the stock is pinned at the center, close the condor and sell an iron butterfly for more credit (advanced).
- **Widen the untested side**: If the stock moves toward the put side, you can roll the call spread closer to collect more credit and offset the put loss. Risky — only if you're confident in the new call position.
- **Max 2 adjustments**: If you've adjusted twice and still losing, close the position. The rangebound thesis is wrong.

## Risk Controls
- Max loss per condor position: 2-3% of portfolio
- Max aggregate condor exposure: 15% of portfolio
- Both sides must have positive expected value independently — don't sacrifice one side to subsidize the other
- Sector concentration: Max 2 condors in the same sector
- Earnings calendar: Close all condors at least 2 days before earnings
- Correlation monitor: During market stress, all condors lose simultaneously on the put side. Size accordingly.
- Daily review: Check delta drift. If net delta exceeds +/-0.20 per condor, address it.

## Common Traps
- **Chasing credit by selling strikes too close**: Collecting $3.00 on a $5-wide condor looks great until you realize the probability of success is only 50%. Keep short strikes at delta 0.15-0.20.
- **Holding to expiry for max profit**: The last 25% of credit is not worth the gamma risk in the final 2 weeks. Close at 50% and move on. Your annualized return is better this way.
- **Treating both sides as independent**: They're one position. If the put side is losing $500 and the call side is winning $200, the position is down $300. Manage it as one trade.
- **Not stress-testing the range**: "It should stay between $90 and $110" is not analysis. Check the expected move, ATR, and historical range for the DTE you're trading.
- **Ignoring the risk/reward math**: Collecting $2.00 with $3.00 max loss means you need >60% win rate to break even. Factor in adjustments and commissions — you need >65% in practice.
- **Selling condors in trending markets**: Iron condors are range-bound strategies. In a strong uptrend, the call side gets crushed. In a downtrend, the put side. Trade with the market regime, not against it.
- **Portfolio-level correlation blindness**: 10 iron condors on 10 mega-cap stocks all lose their put side simultaneously during a market sell-off. You don't have 10 independent bets — you have one big short-put bet. (关联性是最大隐藏风险)
