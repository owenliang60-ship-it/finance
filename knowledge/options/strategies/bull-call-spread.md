# Strategy: Bull Call Spread (牛市看涨价差)
> 买方/垂直价差 | Complexity: Intermediate | Max Risk: Defined | IV Environment: Any

## Overview
A bull call spread involves buying a call at a lower strike and selling a call at a higher strike, both with the same expiration. This creates a defined-risk, defined-reward bullish position at a lower cost than a naked long call. The short call partially finances the long call, reducing the net debit and dampening vega/theta exposure. It's the workhorse spread for moderate bullish conviction when you want capital efficiency.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Call | Lower (ATM or slightly OTM) | 30-60 DTE |
| 2   | Sell      | Call | Higher (OTM) | Same expiry |

- Net debit trade. Max loss = net debit paid. Max gain = (strike width - net debit).
- Breakeven = long strike + net debit.

## When to Use
- Moderately bullish — expecting a move up, but not a moon shot
- IV is elevated (rank > 40%) — the short call offsets the high IV cost
- Want defined risk with better capital efficiency than a long call
- Post-pullback entries where you expect a bounce to a known resistance level
- When you can identify a realistic price target (short strike = target)

## When NOT to Use
- Expecting a massive move (>15%) — the short call caps your upside
- IV rank < 20% — just buy a long call; the spread's cost reduction isn't worth capping gains
- Underlying is in a strong downtrend with no reversal signal
- Very narrow spreads on illiquid names — commissions and slippage eat the edge

## Entry Criteria
- **IV Environment**: IV rank 30-70% is the sweet spot. Works at any IV but shines when IV is moderate-to-high.
- **Direction**: Bullish with a defined price target within 30-60 days
- **Time Frame**: 30-60 DTE. Shorter DTEs only for event-driven trades.
- **Liquidity**: Bid-ask on each leg < $0.10; OI > 300 on both strikes; strike increments of $2.50 or $5 preferred
- **Spread width**: $5-$10 width on $50-200 stocks; $10-$20 on $200+ stocks

## Strike & Expiry Selection
- **Long call**: ATM (delta ~0.50) for maximum delta exposure; slightly OTM (delta 0.40) for lower cost
- **Short call**: Place at your price target or a key resistance level
- **Width selection rule**: Wider spreads = higher max gain but higher max loss. Narrow spreads = higher probability but smaller payout.
- **Risk/reward target**: Aim for spreads where max gain is 1.5-3x the net debit. Example: Pay $3.00 for a $5-wide spread (max gain $2.00, or 67% return).
- **Expiry**: 30-45 DTE gives optimal theta/gamma balance. 60 DTE if the catalyst is further out.

## Position Sizing
- Max loss = net debit per spread. Size based on this: `Spreads = (Portfolio × 0.02) / (Net Debit × 100)`
- Risk 1-3% of portfolio per spread position
- Example: $500K portfolio, 2% risk = $10,000. If spread costs $4.00, trade 25 spreads.
- With defined risk, you can run multiple spread positions simultaneously — but watch correlation.

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Net positive (+0.15 to +0.30) | Increases if stock moves toward short strike |
| Gamma | Small net positive | Mostly neutralized by the spread structure |
| Theta | Slightly negative to neutral | Turns positive when stock is near/above short strike and time passes |
| Vega  | Reduced (partially hedged) | Less IV sensitivity than a naked long call — this is the point |

## Exit Protocol
- **Profit Target**: Close at 50-75% of max profit. Don't hold to expiry chasing the last 25% — pin risk is real.
- **Stop Loss**: Close if spread value drops to 50% of entry (e.g., paid $4.00, close at $2.00).
- **Time Stop**: Close at 14 DTE regardless, unless deep ITM. Gamma risk increases sharply.
- **Adjustment Trigger**: If stock drops below the long strike by more than the spread width, the trade is likely dead — close and redeploy.

## Adjustments & Rolling
- **Roll up**: If the stock blows through your short strike early, close the entire spread and open a new one at higher strikes. Don't get greedy — take the win.
- **Roll out**: If thesis is intact but timing was slow, close and reopen at the same strikes with more DTE.
- **Convert to butterfly**: If the stock is sitting at your short strike with 14 DTE, sell a second call spread above to create a call butterfly. This adds credit and defines risk tighter.
- **Leg out (advanced)**: If the stock surges past the short strike, you can buy back the short call and let the long call run. Only do this if you have strong conviction — you're now in a naked long call with different risk.

## Risk Controls
- Max loss per position: 2-3% of portfolio
- Max aggregate directional spread exposure: 10% of portfolio
- Correlation limit: No more than 3 bull call spreads in the same sector
- Monitor earnings dates: Close or adjust before earnings to avoid binary risk
- Avoid holding through ex-dividend dates — early assignment risk on short ITM calls (被提前行权风险)

## Common Traps
- **Spread too narrow**: A $1-wide spread on a $150 stock costs $0.60 and makes $0.40. After commissions, the edge is gone. Use wider spreads for better risk/reward.
- **Chasing max profit to expiry**: The last 25% of max profit requires the stock to be above the short strike at expiry. Pin risk, gamma, and transaction costs make this a bad gamble. Take profits at 50-75%.
- **Ignoring early assignment**: If your short call is ITM near ex-dividend, you may be assigned. Monitor this — especially on high-dividend stocks.
- **Setting the short strike too close**: This makes the trade cheaper but caps your gains severely. The short strike should be at a realistic price target, not just "the cheapest spread."
- **Panic-closing on a small dip**: Spreads are slower to react than single options. A small dip doesn't destroy the trade. Stick to your stop-loss rules, not your emotions.
