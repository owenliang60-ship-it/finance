# Strategy: Bull Put Spread (牛市看跌价差 / Credit Put Spread)
> 卖方/垂直价差 | Complexity: Intermediate | Max Risk: Defined | IV Environment: High

## Overview
A bull put spread is a credit spread: you sell a higher-strike put and buy a lower-strike put, collecting a net credit. You profit when the stock stays above the short strike through expiration. It's a high-probability, theta-positive trade that benefits from time decay and IV contraction. This is the bread-and-butter of options sellers who want defined risk and consistent income — essentially getting paid to express a "not going below X" thesis.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Sell      | Put  | Higher (OTM) | 30-45 DTE |
| 2   | Buy       | Put  | Lower (further OTM) | Same expiry |

- Net credit trade. Max gain = credit received. Max loss = (strike width - credit received).
- Breakeven = short strike - credit received.

## When to Use
- Neutral to moderately bullish outlook — "it won't go below this level"
- IV rank > 50% — elevated IV inflates the credit you receive (卖方优势环境)
- Expecting the stock to hold above support or consolidate sideways
- Want to generate income with defined risk and high probability
- After a pullback to support — sell fear, collect premium

## When NOT to Use
- IV rank < 30% — credit is too small to justify the risk
- Bearish outlook or expecting a significant downturn
- Earnings or major binary events within the spread's lifetime (unless intentionally trading the event)
- The "support level" is just a hope, not a real technical or fundamental floor
- Illiquid options — wide bid-ask spreads destroy credit strategy edge

## Entry Criteria
- **IV Environment**: IV rank > 50%, ideally > 70%. The higher the IV, the wider you can set the spread while still collecting meaningful credit.
- **Direction**: Neutral to bullish. Short strike placed below a strong support level.
- **Time Frame**: 30-45 DTE. This is the theta sweet spot (时间衰减最佳区间).
- **Liquidity**: Bid-ask on each leg < $0.05; OI > 1000 on the short strike. Tighter markets = better fills.
- **Credit target**: Collect at least 1/3 of the spread width. Example: $5-wide spread should collect $1.65+ in credit.

## Strike & Expiry Selection
- **Short put**: Delta -0.20 to -0.30 (70-80% probability OTM). Place below the nearest strong support level.
- **Long put**: 1-2 strikes below the short put. $5-wide spreads are standard.
- **Rule of thumb**: The short strike should be a price where you'd say "I'd be happy buying this stock here."
- **Expiry**: 30-45 DTE. Do not sell weekly puts unless you're very experienced — gamma is dangerous.
- **Width vs. credit**: Wider spreads collect more credit but have higher max loss. Keep max loss manageable (2-3% of portfolio).

## Position Sizing
- Max loss = (spread width - credit) × 100. Size based on max loss, not credit received.
- `Spreads = (Portfolio × 0.02) / (Max Loss per spread)`
- Example: $500K portfolio, 2% risk = $10,000. $5-wide spread for $1.75 credit = $3.25 max loss per spread ($325). Trade 30 spreads.
- Keep total credit spread exposure < 15% of portfolio (across all positions)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Small net positive (+0.05 to +0.15) | Approaches zero as time passes (if OTM) |
| Gamma | Small net negative | Your enemy — big moves hurt. Manageable with proper strike selection. |
| Theta | Positive (ally) | This is your profit engine. Theta accelerates as expiry approaches while OTM. |
| Vega  | Negative (ally when IV drops) | IV contraction is profit. IV expansion is pain. Enter when IV is high. |

## Exit Protocol
- **Profit Target**: Close at 50% of max credit. If you collected $2.00, buy back at $1.00. Don't hold for the last 50% — the risk/reward flips.
- **Stop Loss**: Close if the spread reaches 2x the credit received (e.g., collected $1.75, close if spread is worth $3.50). Or close if the stock breaks below the short strike by more than 1 ATR.
- **Time Stop**: If < 14 DTE and profitable, strongly consider closing. Gamma risk is elevated.
- **Adjustment Trigger**: If the stock drops to within 1 strike of the short put, prepare to adjust or close.

## Adjustments & Rolling
- **Roll down and out**: If the stock is approaching the short strike, buy back the current spread and sell a new one at lower strikes with 30-45 more DTE. Only roll for a net credit or even — never pay to roll a losing credit spread.
- **Roll out**: Same strikes, more time. Gives theta more runway. Only if the support thesis is intact.
- **Close the tested side**: If the short put is breached, close the entire spread. Don't hope. Capital preservation > stubbornness.
- **Add a call spread** (iron condor conversion): If the stock rallies strongly, sell a bear call spread above to create an iron condor. Collects additional credit and uses the "safe" side.
- **Never roll into a bigger position**: Rolling should maintain or reduce risk, not increase it.

## Risk Controls
- Max loss per position: 2-3% of portfolio
- Max total credit spread notional: 15% of portfolio
- Credit-to-width ratio minimum: 30%. If you can't collect 30% of the width, the trade doesn't have enough edge.
- Monitor delta: If net portfolio delta from credit spreads exceeds your directional comfort, reduce positions
- Earnings filter: Close all credit spreads at least 1 day before earnings, unless the spread was specifically designed for the event
- Sector concentration: Max 3 bull put spreads in the same sector

## Common Traps
- **Selling puts on garbage stocks**: "High IV" often means the market knows something you don't. High IV on a stock with deteriorating fundamentals is a warning, not an invitation.
- **Holding to expiry for max profit**: The last 25% of credit takes 75% of the remaining time to collect, while gamma risk maxes out. Close at 50% and redeploy.
- **Rolling losers indefinitely**: "I'll just roll it out" is how small losses become big ones. If your thesis is wrong, take the loss. Max 2 rolls per position.
- **Ignoring correlation**: Five bull put spreads on FAANG stocks is one big bet on tech, not five independent trades.
- **Position sizing by credit received instead of max loss**: A $10-wide spread that collects $3.00 looks great until you realize you're risking $7.00 per spread. Always size by max loss.
- **Selling too close to ATM for bigger credits**: Delta -0.40 puts collect more premium but get tested 40% of the time. Stick to delta -0.20 to -0.30 for sustainable results.
