# Strategy: Double Calendar Spread (双时间价差)
> Dual Time Spread | Complexity: Advanced | Max Risk: Defined | IV Environment: Any (benefits from IV rise)

## Overview
A double calendar spread deploys two calendar spreads simultaneously -- one on the call side and one on the put side -- at different strikes flanking the current stock price. This creates a wider profit zone than a single calendar, making it more forgiving of directional movement. The trade profits from time decay on the two short legs while maintaining positive vega exposure on the two long legs.

Think of it as a "wider tent": a single calendar has a narrow peak at one strike, while a double calendar has a broad plateau between two strikes. It's ideal when you expect the stock to stay within a range but aren't sure exactly where within that range. The cost is higher (two debit spreads), but the probability of profit is significantly improved.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | OTM Put | Below current price (support) | Near-term (30-45 DTE) |
| 2 | Buy | OTM Put | Same strike as Leg 1 | Far-term (60-90 DTE) |
| 3 | Sell | OTM Call | Above current price (resistance) | Near-term (same as Leg 1) |
| 4 | Buy | OTM Call | Same strike as Leg 3 | Far-term (same as Leg 2) |

**Net Debit**: Sum of both calendar debits. Typically 1.5-2x the cost of a single calendar.

**Example**: Stock at $100. Put calendar at $95 (sell front $95P, buy back $95P). Call calendar at $105 (sell front $105C, buy back $105C). Cost: $1.80 (put cal) + $2.00 (call cal) = $3.80 total debit. Profit zone: roughly $92-$108 at front-month expiry.

## When to Use
- Neutral view: you expect range-bound trading but are unsure where the stock pins within the range
- Earnings play (post-announcement): sell elevated near-term IV on both sides against cheaper far-term IV
- IV is low-to-moderate and you expect it to rise (double vega exposure)
- You tried a single calendar before and the stock moved too far from strike -- double calendar widens the tent
- Support and resistance levels are well-defined, giving you natural strike anchors

## When NOT to Use
- You expect a breakout in either direction -- the double calendar loses on large moves
- IV is extremely high and likely to collapse -- double vega exposure means double vega risk
- Term structure is sharply inverted -- you're overpaying for the structure
- The stock is trending strongly -- calendars are neutral strategies; they don't work in trends
- Liquidity is poor in far-term chains -- 4 legs with wide bid-ask spreads will eat your profits

## Entry Criteria
- **IV Environment**: Any, but ideal at IV Rank 20-50% with expectation of stability or rise
- **Direction**: Neutral/range-bound. This is a "stay between the lines" play
- **Time Frame**: Short legs: 30-45 DTE. Long legs: 60-90 DTE
- **Liquidity**: OI > 300 at all 4 strike/expiry combinations. Far-dated chains must have reasonable markets
- **Term structure**: Normal (contango) or flat. Avoid inverted term structure
- **Range confirmation**: The put and call strikes should encompass a range where the stock has traded 70%+ of the time over the last 30-60 days

## Strike & Expiry Selection

### Strike Selection
- **Put calendar strike**: At or near a major support level, 3-7% below current price
- **Call calendar strike**: At or near a major resistance level, 3-7% above current price
- **Symmetry vs. lean**: Equal distance from current price = neutral. Closer put = slightly bullish lean. Closer call = slightly bearish lean
- **Delta guidance**: Each short option should be ~0.25-0.35 delta. The strikes should represent the 1 SD range
- **Width between strikes**: The wider apart, the broader the profit zone but the lower the max profit at any single point. Typical: 5-10% of stock price between strikes
- **Avoid too-narrow width**: If puts at $97 and calls at $103, you've essentially recreated a single calendar. Width should be meaningful

### Expiry Selection
- **Short legs**: 30-45 DTE (both same expiry). This is the theta engine
- **Long legs**: 60-90 DTE (both same expiry). This is the vega and residual value anchor
- **Gap between**: 30-45 days. Same rationale as single calendar
- **Matching expiries**: Both short legs MUST share the same expiry. Both long legs MUST share the same expiry. Don't mix

## Position Sizing
- **Max loss**: Total debit paid for both calendars. Occurs if stock makes an extreme move far from either strike
- **Typical debit**: $3-$8 per double calendar on a $100-200 stock
- **Portfolio rule**: Max loss per double calendar < 1.5-2% of portfolio
- **Contract count**: (Risk budget in $) / (total debit x 100). Usually 3-8 contracts given the higher per-spread cost
- **Example**: $300K portfolio, 1.5% risk = $4,500. Total debit = $4.00/double calendar. Max 11 contracts

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (call side delta offsets put side delta) | Becomes directional if stock moves toward either wing |
| Gamma | Slightly negative (2 short near-term options have combined higher gamma) | Gamma risk increases as short legs near expiry |
| Theta | Positive (2 short legs decay faster than 2 long legs) | Theta is broad-based: profitable as long as stock stays between strikes |
| Vega  | Positive (2 long legs have more combined vega) | Double the vega exposure of a single calendar -- IV moves have amplified effect |

## Exit Protocol
- **Profit Target**: Close at 20-30% of debit paid. The wide profit zone means max profit per unit is lower than a single calendar. Take what the market gives you
- **Stop Loss**: Close if position has lost 40-50% of debit paid. Stock has broken out of the range
- **Time Stop**: Close or roll short legs at 7-10 DTE. Gamma acceleration on 2 short options near expiry is dangerous
- **Adjustment Trigger**: If the stock breaks outside the strike range (below put strike or above call strike) with > 14 DTE on short legs, evaluate closing the losing calendar and keeping the profitable one
- **IV collapse**: If IV drops by > 8 vol points, close the position. The double vega exposure amplifies the damage

## Adjustments & Rolling

### Stock Moves Toward One Side
- **Stock drops toward put strike**: The put calendar gains value, call calendar loses value. Options:
  1. Close the call calendar (minimal value remaining) and keep the put calendar
  2. Roll the call calendar down to a new lower strike (converts to a concentrated position near support)
  3. Add a third calendar between the two strikes (creates a triple calendar -- complex but wider coverage)

- **Stock rallies toward call strike**: Mirror of above. Close or roll the put calendar

### Rolling Short Legs Forward
- At 7-10 DTE, buy back both short legs and sell the next month's expiry at the same strikes
- Verify term structure hasn't inverted before rolling
- Cost: bid-ask slippage on 4 legs. Budget $0.20-0.40 total slippage per roll

### Rebalancing
- If one calendar has gained 50%+ while the other has lost most of its value, close the winner and re-establish a new balanced double calendar at the new price range
- Re-center the strikes around the current stock price

## Risk Controls
- **Max loss = total debit**: Defined and known. This is the key advantage over other multi-leg structures
- **Range validation**: Before entry, confirm the stock has stayed within the strike range > 70% of the time in the last 30 days (historical range check)
- **IV monitoring**: Daily vega check. Combined vega position is roughly double that of a single calendar. A 5-point IV move can swing the position value by 15-25%
- **Event avoidance**: No earnings or binary events should fall between the short and long leg expiries. This distorts the term structure and creates gap risk
- **Max positions**: 2 double calendars simultaneously. Each one has 4 legs to manage -- complexity increases quadratically with more positions
- **Correlation**: Avoid running double calendars on stocks in the same sector. A sector rotation kills all of them

## Common Traps
- **Paying too much debit**: Two calendars cost roughly 2x. If total debit is > 40% of the maximum theoretical value, the entry is too expensive. Shop for better IV conditions
- **Treating it as "can't lose"**: The wider profit zone creates a false sense of security. Large breakouts (> 8-10%) still produce max loss. This is not an Iron Condor alternative -- it has a different risk profile
- **Over-managing**: With 4 legs, there's a temptation to adjust constantly. Set your exits at entry and stick to them. Every adjustment costs slippage on 4 legs
- **Ignoring the vega risk**: Double calendar = double vega. An IV crush event can destroy the position even if the stock stays perfectly in range. Monitor IV daily
- **Mixing expiries**: Having the put short leg expire 1 week before the call short leg creates a mess. All short legs same expiry, all long legs same expiry. No exceptions
- **Not accounting for total slippage**: 4 legs at entry ($0.05-0.10/leg), potential roll (4 more legs), and exit (4 legs) = up to $0.60-$1.20 in total slippage per contract. On a $4 debit, that's 15-30% of your capital. Only do this on tight-market names
- **Overcomplicating exit decisions**: When one side is profitable and the other is not, the temptation is to "fix" the losing side. Often the best move is to close the winner, take the loss on the loser, and move on. Don't turn a 4-leg trade into a 6-leg or 8-leg monstrosity
