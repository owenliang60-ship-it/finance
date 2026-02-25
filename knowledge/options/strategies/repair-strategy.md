# Strategy: Repair Strategy (修复策略 / 被套后的结构化自救)
> Repair/Recovery | Complexity: Advanced | Max Risk: Defined | IV Environment: Any

## Overview
The repair strategy is deployed when you own shares of a stock that has declined significantly and you want to recover your cost basis without waiting for the stock to fully rebound. It achieves this by adding a bull call spread (buy 1 ATM call, sell 2 OTM calls at your original cost basis) using zero or near-zero additional capital. The structure effectively doubles your upside exposure between the current price and your cost basis, allowing you to break even with only a partial recovery.

This is not a magic fix. It's a rational restructuring of a losing position that trades away upside above your cost basis for faster break-even. It's the options equivalent of "I don't need the stock to go back to my entry; I just need it to recover halfway."

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 0 | Hold | 100 Shares | Purchased at original price (now underwater) | N/A |
| 1 | Buy | 1x ATM Call | At current (lower) price | 60-90 DTE |
| 2 | Sell | 2x OTM Call | At original purchase price (cost basis) | Same expiry |

**Net Cost**: Zero or small debit. The 2x short call premium should approximately equal the 1x long call cost.

**Example**: Bought stock at $100, now trading at $85. Buy 1x $85 call for $5.00, sell 2x $100 calls for $2.50 each ($5.00 total). Net cost: $0. If stock recovers to $100: gain on shares = $15, gain on long call = $15, short calls expire ATM = $0 loss. Total = $30 gain on 100 shares + 1 long call, effectively breaking even at $100 instead of needing the stock to return to $100 on shares alone.

## When to Use
- You own shares that are down 10-25% from your purchase price
- Your investment thesis is still intact (critical -- don't repair a broken thesis)
- You believe the stock can recover to your cost basis within 2-3 months but may not go significantly higher
- You want to break even without adding more capital (the repair is self-financing)
- IV is moderate enough that the ATM call isn't prohibitively expensive

## When NOT to Use
- The stock is down > 30% -- the repair structure can't realistically bring break-even within reach
- Your thesis has fundamentally changed -- don't repair, cut the loss
- You believe the stock will rally well beyond your original cost basis -- the repair caps your upside at cost basis. Just hold the shares
- IV is extremely high -- the ATM call is too expensive and 2x OTM calls don't cover the cost
- The stock is down due to a permanent impairment (fraud, bankruptcy risk, sector obsolescence)
- You're using the repair as a psychological crutch to avoid taking a loss

## Entry Criteria
- **IV Environment**: Any, but moderate IV (30-50 IV Rank) is ideal. Very high IV makes the ATM call expensive; very low IV makes OTM calls worthless
- **Direction**: Moderately bullish. You believe partial recovery (50-70% of the decline) is likely
- **Time Frame**: 60-90 DTE. Needs enough time for recovery but not so long that theta erodes the structure
- **Liquidity**: OI > 500 at both strikes. The cost-basis strike especially needs liquidity
- **Zero-cost check**: The structure should be implementable for $0 net cost (max $0.50 debit acceptable)
- **Decline range**: Stock should be down 10-25%. Less than 10% -- just wait. More than 25% -- strikes are too far apart for the structure to work at zero cost

## Strike & Expiry Selection

### Long Call (ATM)
- **Strike**: At or within $1-2 of current stock price
- **Delta**: ~0.50
- **Purpose**: Doubles your upside exposure from current price to cost basis

### Short Calls (2x at Cost Basis)
- **Strike**: At or very near your original purchase price
- **Delta**: 0.15-0.30 each (since they're OTM by the amount the stock has declined)
- **Premium target**: Combined premium from 2 short calls should cover the long call cost
- **Adjustment**: If 2x doesn't cover the cost, either accept a small debit or widen (move short calls slightly above cost basis)

### Expiry Selection
- **60-90 DTE**: Best balance. Recovery takes time, but longer expiries make the structure more expensive
- **Avoid < 45 DTE**: Not enough time for meaningful recovery
- **Avoid > 120 DTE**: Too much time value in the long call; 2x short calls can't cover the cost
- **Quarterly expiry**: Often better liquidity for strikes at round numbers

## Position Sizing
- **No additional capital needed**: The repair is applied to existing share holdings. 1 repair structure per 100 shares held
- **Max loss**: Same as holding the shares naked -- if the stock drops further, you lose on the shares. The repair options expire worthless (net zero cost means net zero additional loss)
- **Upside cap**: Profit is capped at the cost basis (short call strike). Maximum gain = (cost basis - current price) x 2 (due to the long call doubling exposure). But this exactly equals your original loss, so you break even
- **Partial repair**: If you have 500 shares, consider repairing only 200-300 (2-3 contracts) to maintain some uncapped upside

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | ~+1.50 per 100 shares (100 shares + 1 long call - 2 short calls) | Increases toward +2.00 if stock approaches cost basis; decreases if stock drops further |
| Gamma | Slightly positive (1 long call gamma > 2x OTM short call gamma) | Shifts negative as short calls approach ATM |
| Theta | Near zero (1 long call theta offset by 2x short call theta) | Becomes slightly negative as long call loses time value faster |
| Vega  | Near zero (1 long call vega offset by 2x short call vega) | Small positive bias since ATM long has higher vega than OTM shorts |

## Exit Protocol
- **Stock recovers to cost basis**: Close entire position (shares + options). You've achieved the goal: break-even recovery. Don't get greedy
- **Stock recovers partially (50-75% of decline)**: Consider closing the repair options at a profit and keeping the shares for further upside. The options have done their job
- **Stock drops further**: The repair options expire worthless (zero net cost). You're back to naked long shares. Reassess thesis
- **Time Stop**: At 21 DTE, if stock hasn't recovered significantly, close the option legs. You can set up a new repair at the new ATM price if thesis still holds
- **Thesis changes**: If fundamentals deteriorate during the repair period, close everything (shares + options). Don't let the repair prevent you from cutting a losing position

## Adjustments & Rolling
- **Stock recovers past cost basis**: Both short calls go ITM. At this point, your profit is capped. If you want more upside, buy back one of the short calls (accept the cost) to uncap half your position
- **Stock drops further during repair**: The repair structure becomes less effective (strikes are further apart). Options: (a) let it expire and set up a new repair at lower strikes, (b) roll the long call down to the new ATM (costs money, destroys the zero-cost feature)
- **Rolling to next cycle**: At 21 DTE, close the current repair and re-establish at 60-90 DTE. The new structure may require a small debit if the stock has moved
- **Converting to collar**: If confidence drops during the repair, add a long put below the current price to limit further downside. This adds cost

## Risk Controls
- **Thesis integrity test**: Before setting up the repair, answer honestly: "Would I buy this stock today at this price?" If no, sell the shares instead of repairing
- **Maximum decline for repair**: Don't repair stocks down more than 25%. Beyond that, the strike distance is too wide and the structure doesn't work at zero cost
- **One repair per holding**: Don't layer multiple repair structures. One repair per 100 shares
- **Time limit**: If the repair hasn't worked after 2 cycles (4-6 months), the thesis may be wrong. Re-evaluate from scratch
- **No doubling down**: The repair replaces the urge to "average down." You get double exposure without adding capital. Don't also add more shares
- **Portfolio context**: The repair reduces your effective delta temporarily. Account for this in portfolio delta calculations

## Common Traps
- **Repairing a broken thesis**: The most dangerous trap. "The stock is down 20% because they lost their biggest customer, but I'll repair it." NO. Repair is for stocks down on temporary or sentiment-driven reasons, not fundamental impairment
- **Forgetting the upside cap**: At cost basis, you break even. Above cost basis, you actually LOSE money on the extra short call (one is covered by shares, the other by the long call, but above the short strike, the 2 shorts outpace the 1 long + shares). Make sure you understand the payoff
- **Waiting too long to repair**: If you wait until the stock is down 35%, the repair can't be done at zero cost. Set a rule: if down 10-15%, evaluate. If thesis intact, repair immediately
- **Treating it as a guarantee**: The repair only works if the stock recovers to your cost basis within the option's lifetime. If it doesn't, you're back where you started (minus any debit paid)
- **Ignoring taxes**: If you've held the shares for over a year, you have long-term capital gains treatment. The repair options are short-term. Consider the tax implications of being called away via the repair vs. holding longer
- **Repair addiction**: Setting up repair after repair, cycle after cycle, because you can't emotionally accept the loss. After 2 failed repair cycles, accept reality. Cut the position
- **Not understanding the payoff diagram**: Between current price and cost basis, you have double exposure (good). Above cost basis, you have capped exposure (break-even). Below current price, you have normal 100-share exposure (same as without repair). Draw this out before entering
