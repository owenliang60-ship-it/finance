# Strategy: Bear Put Spread (熊市看跌价差)
> 买方/垂直价差 | Complexity: Intermediate | Max Risk: Defined | IV Environment: Any

## Overview
A bear put spread involves buying a put at a higher strike and selling a put at a lower strike, both with the same expiration. This creates a defined-risk bearish position at a lower cost than a naked long put. The short put reduces cost basis but caps the downside profit potential. Ideal for moderate bearish plays where you have a downside price target and want to control premium outlay.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Put  | Higher (ATM or slightly OTM) | 30-60 DTE |
| 2   | Sell      | Put  | Lower (OTM) | Same expiry |

- Net debit trade. Max loss = net debit paid. Max gain = (strike width - net debit).
- Breakeven = long strike - net debit.

## When to Use
- Moderately bearish — expecting a decline to a specific support level
- IV is elevated (rank > 40%) — short put offsets expensive premiums
- Want defined risk without the full cost of a long put
- Bearish into earnings or macro events where downside is capped by support levels
- Hedging a long portfolio with targeted downside bets

## When NOT to Use
- Expecting a catastrophic decline (>20%) — the short put caps your profit
- IV rank < 20% — consider a naked long put for unlimited downside capture
- The stock has strong support near your short strike — your max gain zone is blocked
- Unclear bearish thesis — spreads amplify the importance of timing

## Entry Criteria
- **IV Environment**: IV rank 30-70% is ideal. Works at any IV, but higher IV makes the spread more capital-efficient.
- **Direction**: Bearish with a specific downside target within 30-60 days
- **Time Frame**: 30-60 DTE. Shorter for event-driven plays (earnings misses, guidance cuts).
- **Liquidity**: Bid-ask on each leg < $0.10; OI > 300 on both strikes
- **Spread width**: $5-$10 on $50-200 stocks; adjust proportionally for price

## Strike & Expiry Selection
- **Long put**: ATM (delta ~-0.50) for maximum downside delta; slightly OTM (delta -0.40) for lower cost
- **Short put**: Place at or below your downside target / key support level
- **Width selection**: Wider = higher max gain and higher max loss. Target risk/reward of 1:1.5 to 1:3.
- Example: Stock at $150, buy $150 put, sell $140 put for $4.00 net debit. Max gain = $6.00 (150% return).
- **Expiry**: 30-45 DTE standard. 60 DTE if waiting for a specific catalyst.

## Position Sizing
- Max loss = net debit. `Spreads = (Portfolio × 0.02) / (Net Debit × 100)`
- Risk 1-3% of portfolio per position
- For hedging purposes, size to offset specific long exposure: notional value of spread should approximate the expected loss on the hedge target
- Example: $500K portfolio, 2% risk = $10,000. If spread costs $3.50, trade 28 spreads.

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Net negative (-0.15 to -0.30) | More negative as stock drops toward short strike |
| Gamma | Small net positive | Manageable; less gamma risk than naked puts |
| Theta | Slightly negative to neutral | Turns positive when stock is below the long strike |
| Vega  | Reduced (partially hedged) | Sell-off IV spike benefits long put more than it hurts short put (net positive, but less than naked long put) |

## Exit Protocol
- **Profit Target**: Close at 50-75% of max profit. In a declining stock, momentum can reverse fast.
- **Stop Loss**: Close if spread value drops to 50% of entry debit. Reassess if stock bounces above resistance.
- **Time Stop**: Close at 14 DTE unless the spread is deep ITM. Theta and gamma risk increase.
- **Adjustment Trigger**: If stock rallies above the long strike by more than the spread width, the trade is likely lost — close and reevaluate.

## Adjustments & Rolling
- **Roll down**: If stock drops through the short strike quickly, close the spread and open a new one at lower strikes to capture further downside. Book the profit first.
- **Roll out**: Same strikes, more DTE. Use when the move is happening but slower than expected.
- **Convert to butterfly**: If stock parks near the short strike, sell a put spread below to create a put butterfly. Adds credit and tightens the P&L range.
- **Partial close**: If the long put has most of the spread's value, close the short put first (for a small cost) and let the long put run. Only if conviction on further downside is very high.

## Risk Controls
- Max loss per position: 2-3% of portfolio
- Max aggregate bearish spread exposure: 8% of portfolio (bear positions should be smaller than bull)
- Correlation limit: Don't stack bear put spreads across correlated names (3 tech shorts = one big bet)
- Monitor for short squeezes: High short interest stocks can rally violently, instantly maxing out your loss
- Close before earnings if the trade was not designed for the binary event

## Common Traps
- **Fighting a strong uptrend**: The most common bear put spread mistake. Stocks can stay irrational longer than you can stay solvent. Require a catalyst, not just "it's overvalued."
- **Spread too narrow for the move**: If you expect a $20 drop, a $5-wide spread only captures the first $5. Match the spread width to your expected move magnitude.
- **Holding into expiry for pin risk**: If the stock is between your strikes near expiry, assignment risk on the short put creates unwanted stock positions. Close early.
- **Entering after a big drop**: If the stock already dropped 10%, IV is elevated, and the easy money is made. Bear put spreads work best when entered before or early in the decline.
- **Ignoring the vol tailwind timing**: Bear put spreads benefit less from IV expansion than naked long puts because the short put partially offsets vega. Factor this in when sizing.
- **Over-allocating to bearish positions**: Most portfolios should be net long most of the time. Bear spreads are surgical tools, not core positions.
