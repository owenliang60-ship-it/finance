# Strategy: Bear Call Spread (熊市看涨价差 / Credit Call Spread)
> 卖方/垂直价差 | Complexity: Intermediate | Max Risk: Defined | IV Environment: High

## Overview
A bear call spread is a credit spread: you sell a lower-strike call and buy a higher-strike call, collecting a net credit. You profit when the stock stays below the short strike through expiration. It's the bearish/neutral counterpart to the bull put spread — a high-probability, theta-positive trade that bets "the stock won't rally above X." Best deployed after a stock has run up into resistance with elevated IV.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Sell      | Call | Lower (OTM) | 30-45 DTE |
| 2   | Buy       | Call | Higher (further OTM) | Same expiry |

- Net credit trade. Max gain = credit received. Max loss = (strike width - credit received).
- Breakeven = short strike + credit received.

## When to Use
- Neutral to moderately bearish — "it won't rally above this level"
- IV rank > 50% — inflated premiums provide better credits
- Stock has run into strong resistance after a rally
- Overbought conditions (RSI > 70) with fading momentum
- Want to sell premium on the upside without naked call risk (有限风险的卖方策略)

## When NOT to Use
- IV rank < 30% — credit too thin to justify risk
- Strong bullish trend with no resistance in sight
- Stock is in a sector with momentum/rotation into it
- Earnings or M&A rumors — short calls can be deadly on gap-ups
- Low liquidity options with wide spreads

## Entry Criteria
- **IV Environment**: IV rank > 50%, ideally > 60%. Call IV can be lower than put IV (normal skew), so ensure the credit is still adequate.
- **Direction**: Neutral to bearish. Short strike above strong resistance.
- **Time Frame**: 30-45 DTE. Same theta sweet spot as bull put spreads.
- **Liquidity**: Bid-ask on each leg < $0.05; OI > 500 on short strike
- **Credit target**: Collect at least 1/3 of spread width. $5-wide spread should net $1.65+ credit.

## Strike & Expiry Selection
- **Short call**: Delta +0.15 to +0.25 (75-85% probability OTM). Place above the nearest strong resistance.
- **Long call**: 1-2 strikes above the short call. $5-wide is standard.
- **Note on call skew**: Calls typically have lower IV than puts (normal skew), so credit may be smaller than an equivalent bull put spread. Adjust width accordingly.
- **Expiry**: 30-45 DTE. Avoid selling calls with less than 21 DTE unless taking over from a rolled position.
- **Avoid selling calls near ex-dividend dates**: If the short call goes ITM, early assignment risk is highest just before ex-div.

## Position Sizing
- Max loss = (spread width - credit) × 100. Size by max loss, not credit.
- `Spreads = (Portfolio × 0.02) / (Max Loss per spread)`
- Example: $500K portfolio, 2% risk = $10,000. $5-wide spread for $1.50 credit = $3.50 max loss ($350). Trade 28 spreads.
- Total bear call spread exposure < 10% of portfolio (keep bearish bets smaller than bullish)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Small net negative (-0.05 to -0.15) | Approaches zero as time passes (if OTM) |
| Gamma | Small net negative | Rapid rallies hurt. Position away from ATM. |
| Theta | Positive (ally) | Steady decay works in your favor while OTM |
| Vega  | Negative (ally when IV drops) | IV contraction = profit. Post-earnings IV crush is your friend. |

## Exit Protocol
- **Profit Target**: Close at 50% of max credit. Collected $1.50 → buy back at $0.75. Redeploy capital.
- **Stop Loss**: Close if spread value reaches 2x credit received. Or if stock breaks above the short strike by 1 ATR.
- **Time Stop**: Close at 14 DTE if profitable. Don't let gamma risk ruin a winning trade.
- **Adjustment Trigger**: Stock rallying toward short strike with momentum — prepare to roll or close before it breaches.

## Adjustments & Rolling
- **Roll up and out**: Buy back the current spread, sell a new one at higher strikes with 30-45 more DTE. Must be for a net credit or even. Never pay to roll.
- **Roll out**: Same strikes, more time. Only if resistance thesis is intact.
- **Convert to iron condor**: If stock drops, sell a bull put spread below to collect additional credit and balance the position.
- **Close on breach**: If the short call is breached and holding, close the position. Rolling call spreads on a stock that's breaking out is how small losses become big ones.
- **Buyback short leg early**: If the spread has decayed to < $0.15, buy it back. Risking $500 to make $15 is never worth it.

## Risk Controls
- Max loss per position: 2-3% of portfolio
- Max total bear call spread exposure: 10% of portfolio
- Credit-to-width minimum: 30%
- Earnings buffer: Close at least 1 day before earnings. Gap-ups can breach your short strike instantly.
- Ex-dividend check: If stock goes ex-dividend during the spread's life and the short call could be ITM, monitor for early assignment.
- Never sell bear call spreads on stocks with pending M&A, buyouts, or FDA approvals — the gap risk is unlimited for practical purposes.

## Common Traps
- **Selling calls on strong momentum stocks**: "It can't go higher" is not a thesis. Mean reversion doesn't work on breakout stocks. Require a specific resistance level or exhaustion signal.
- **Underestimating gap risk**: Stocks can gap up 10-20% on earnings, upgrades, or deal announcements. Your $5 spread doesn't care — it's max loss instantly.
- **Confusing "overbought" with "about to decline"**: Overbought can stay overbought for weeks. RSI > 70 is a condition, not a signal. Look for divergences and volume confirmation.
- **Selling too close to ATM**: Delta +0.30+ calls get tested 30% of the time. That's too often for a strategy that relies on the stock staying put. Stay at delta +0.15 to +0.25.
- **Ignoring the risk asymmetry**: A $1.50 credit for $3.50 max loss means you need to win 70% of the time just to break even. Ensure your strike selection supports this win rate.
- **Rolling into hope**: If you've rolled twice and the stock keeps rallying, the thesis is wrong. Take the loss and move on. Max 2 rolls per position.
