# Strategy: Front Ratio Spread (正比率价差 / 收入型比率)
> Ratio/Income | Complexity: Advanced | Max Risk: Partially Undefined | IV Environment: High IV

## Overview
The front ratio spread sells more options than it buys, typically in a 1:2 ratio (buy 1 ATM, sell 2 OTM). It's the mirror image of a backspread: instead of buying more options for a big-move play, you're selling more options to collect premium. When entered correctly (for a credit), you profit if the stock stays near the short strikes, moves slightly in your direction, or moves against your direction (you keep the credit). The danger is a large move through the short strikes, which creates unlimited risk on the extra short leg.

This is a professional's income strategy that exploits high IV and overpriced OTM options. The short strikes are placed at levels where you believe the stock won't reach, and you collect the excess premium. Think of it as a credit spread with an extra short option for enhanced income.

## Structure

### Call Front Ratio (看涨正比率, 1:2)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Buy | 1x ATM/Slightly OTM Call | Lower strike | Same expiry |
| 2 | Sell | 2x OTM Call | Higher strike | Same expiry |

### Put Front Ratio (看跌正比率, 1:2)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Buy | 1x ATM/Slightly OTM Put | Higher strike | Same expiry |
| 2 | Sell | 2x OTM Put | Lower strike | Same expiry |

**Net Credit**: Should always be entered for a credit. The 2x short premium should exceed the 1x long premium.

## When to Use
- IV is elevated (IV Rank > 50%) and OTM options are rich
- You have a mild directional view but don't expect a massive move
- You want income with a "tent-shaped" payoff: max profit at the short strike
- The stock has strong support/resistance at specific levels where you can anchor the short strikes
- Post-earnings or post-event when IV is still elevated but the catalyst has passed

## When NOT to Use
- Low IV environment -- premium collected is too thin to justify the undefined risk
- You expect a large breakout move (unlimited risk on the extra short leg)
- The stock has a history of large gaps or volatile moves
- You're not comfortable monitoring and adjusting an undefined risk position daily
- Earnings or major catalyst is approaching (gap risk through short strikes)

## Entry Criteria
- **IV Environment**: High IV (IV Rank > 50%). The richer the OTM premium, the better
- **Direction**: Mild directional view. You're saying "I think it goes slightly higher/lower but NOT a lot"
- **Time Frame**: 30-45 DTE for the sweet spot of theta collection
- **Liquidity**: OI > 500 on all strikes. Tight bid-ask essential since you're managing 3 legs
- **Credit requirement**: Net credit received must be at least $0.50/contract. If credit is smaller, the risk isn't worth it

## Strike & Expiry Selection

### Strike Selection -- Call Front Ratio (1:2)
- **Long call**: ATM or 1-2 strikes OTM (delta 0.45-0.55)
- **Short calls (2x)**: 5-8% above current price (delta 0.20-0.30 each)
- **Breakeven above shorts**: Upper breakeven = short strike + (spread width - net credit). This must be at a level you believe is unreachable
- **Example**: Stock at $100. Buy 1x $100 call for $5.00, sell 2x $107 calls for $2.80 each ($5.60 total). Net credit: $0.60. Max profit at $107 = $7 spread + $0.60 credit = $7.60. Upper breakeven = $114.60

### Strike Selection -- Put Front Ratio (1:2)
- **Long put**: ATM or 1-2 strikes OTM (delta -0.45 to -0.55)
- **Short puts (2x)**: 5-8% below current price at a strong support level
- **Lower breakeven**: Must be at a catastrophic level you believe is essentially impossible

### Expiry Selection
- **30-45 DTE**: Standard income play. Theta working for you on 2 short legs
- **21-30 DTE**: More aggressive, higher theta decay but less time for adjustments
- **Avoid > 60 DTE**: Ties up margin too long and gives the stock more time to move against you

## Position Sizing
- **Max loss (undefined side)**: Unlimited beyond the upper/lower breakeven
- **Practical max loss**: Calculate loss if stock moves to 2x the spread width past the short strike. Example: short strike at $107, spread width $7 -- calculate loss at $121 (2x width past short = $7 x 2 + $107)
- **Size based on this practical max loss**: Should not exceed 2% of portfolio
- **Typical sizing**: 1-3 contracts for a $200K-$500K portfolio
- **Margin**: Broker requires margin on the naked short leg (1 of the 2 shorts is covered by the long, the other is naked)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Slightly directional (~+0.10 to -0.10 depending on structure) | Becomes increasingly directional as stock approaches short strikes |
| Gamma | Negative (you're short more options than you're long) | Gamma risk accelerates near short strikes and near expiry -- this is your main enemy |
| Theta | Positive (2 short legs decay faster than 1 long leg) | Theta is your friend until the stock approaches the short strikes |
| Vega  | Negative (net short vol) | IV decrease benefits you; IV spike hurts. This is a short vol strategy |

## Exit Protocol
- **Profit Target**: Close at 50% of max profit. Don't wait for the tent peak -- the risk/reward deteriorates rapidly past 50%
- **Stop Loss**: Close if the stock moves within 2% of the short strikes with > 14 DTE remaining. Don't wait for the breakeven
- **Time Stop**: Close at 14 DTE regardless of profit. Gamma risk on 2 short options near expiry is extreme
- **Adjustment Trigger**: If delta of either short option exceeds 0.40, take action immediately

## Adjustments & Rolling
- **Stock approaches short strikes**: Buy back the extra naked short option, converting to a simple vertical spread. This eliminates the unlimited risk at the cost of reducing your credit
- **Stock moves away from short strikes**: Let it ride. If the stock moves the opposite direction, your credit is safe and the position will expire worthless (you keep the credit)
- **Rolling**: Roll the short legs up/down and out if the position is challenged but thesis is intact. Must maintain a net credit on the roll
- **Converting to iron condor**: Add a further OTM long option to cap the naked risk. This defines your maximum loss
- **Widening**: If the stock approaches the short strike slowly, consider closing the position and re-establishing with wider short strikes

## Risk Controls
- **Always enter for a credit**: This is non-negotiable. The credit ensures one side of the trade is risk-free
- **Upper/lower breakeven must be at an extreme level**: The breakeven beyond the short strikes should be at a 2+ standard deviation move
- **Single position max loss < 2% of portfolio**: Calculate the practical max loss (not infinite, but at a reasonable worst case)
- **Max 2 front ratio positions simultaneously**: The gamma risk of multiple front ratios is correlated and compounds
- **Daily monitoring required**: This is not a set-and-forget strategy. Check your position at least once daily
- **Hard close at 14 DTE**: No exceptions. Gamma on the naked short near expiry is a portfolio killer
- **No earnings exposure**: Close before T-10 (not T-5 like defined-risk strategies). The gap risk on a naked short is too high

## Common Traps
- **Ignoring the naked leg**: Mentally, traders focus on the "tent" payoff and forget there's unlimited risk beyond it. The payoff diagram's right (or left) side goes to negative infinity
- **Holding to expiry**: Maximum profit is at the short strike AT EXPIRY. But approaching expiry with 2 short options is like playing with fire. Take profits early
- **Wrong IV environment**: In low IV, the credit received is too small. You're taking unlimited risk for pennies. Only do this when IV is genuinely rich
- **Size inflation**: "I'm only risking the credit if it goes the wrong way" -- true, but if it goes through your shorts, losses accelerate at 2x the rate of a simple short. Size conservatively
- **Pin risk at expiry**: If the stock pins near the short strike at expiry, you face assignment on 2 short options but only have 1 long option to cover. This creates a naked stock position overnight. Close before this scenario
- **Not calculating the breakeven**: You must know your exact breakeven beyond the short strikes. If that level is reachable (within 1 standard deviation of current price), the trade is too risky
- **Confusing with backspreads**: A front ratio spread has the opposite risk profile of a backspread. The front ratio has unlimited risk; the backspread has limited risk. Don't mix them up
