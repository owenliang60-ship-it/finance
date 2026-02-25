# Strategy: Poor Man's Covered Call / PMCC (穷人版备兑 / 对角价差)
> Diagonal Spread | Complexity: Advanced | Max Risk: Defined | IV Environment: Low IV for long leg

## Overview
The Poor Man's Covered Call (PMCC) replaces the 100 shares in a traditional covered call with a deep ITM LEAPS call, dramatically reducing capital requirements while maintaining a similar risk/reward profile. You buy a long-dated, deep ITM call (delta 0.75-0.85) and sell short-term OTM calls against it, collecting premium over multiple cycles. It's a capital-efficient way to run a covered call program on expensive stocks.

The PMCC is essentially a long diagonal spread: long a far-dated call at a low strike, short a near-dated call at a higher strike. The key insight is that the LEAPS call acts as a stock substitute while the short calls generate recurring income. This strategy is ideal for DNA S/A stocks where you're bullish long-term but want to reduce cost basis over time.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 (Long) | Buy | Deep ITM Call | Delta 0.75-0.85 (typically 15-20% ITM) | 6-12 months out (LEAPS) |
| 2 (Short) | Sell | OTM Call | Delta 0.20-0.30 (above current price) | 30-45 DTE (near-term) |

**Net Debit**: Cost of LEAPS minus premium received from short call. Typically 50-65% of owning 100 shares.

## When to Use
- Bullish on a stock long-term but it's too expensive to buy 100 shares (e.g., AAPL at $200 = $20K per lot)
- You want covered-call-like income with 40-50% less capital
- IV is relatively low (cheap LEAPS) and you expect gradual appreciation
- DNA S/A stocks with strong fundamentals where you'd hold for 6-12+ months
- You want to systematically reduce cost basis through short call cycles

## When NOT to Use
- IV is extremely high -- LEAPS will be expensive, destroying the capital efficiency advantage
- You expect a sharp, immediate move higher (short call caps upside; just buy a call instead)
- The stock is volatile enough to gap through your short strike regularly
- You're bearish or neutral -- this is a bullish strategy
- Options are illiquid in the far-dated chain (wide LEAPS spreads kill you)
- Stock pays large dividends (better to own shares and capture dividends)

## Entry Criteria
- **IV Environment**: Low-to-moderate IV for the long leg (IV Rank < 40%). Moderate-to-high IV for the short leg is ideal (sell expensive near-term vol)
- **Direction**: Bullish, medium to long term
- **Time Frame**: LEAPS: 6-12 months DTE. Short calls: 30-45 DTE, rolled monthly
- **Liquidity**: LEAPS chain must have OI > 200 at your strike, bid-ask < $0.80. Near-term chain should have weekly or monthly options with tight spreads
- **Term structure**: Ideal when near-term IV > far-term IV (contango in vol term structure)

## Strike & Expiry Selection

### Long Leg (LEAPS)
- **Delta**: 0.75-0.85. This gives stock-like behavior while keeping some downside cushion
- **Extrinsic value test**: The extrinsic value in the LEAPS should be < 5% of the stock price. If AAPL is $200 and a $170 LEAPS call costs $38, intrinsic = $30, extrinsic = $8 (4%). Acceptable
- **Expiry**: 9-12 months minimum. This gives you time to sell 6-9 cycles of short calls
- **Roll trigger**: When LEAPS reaches 90-120 DTE, roll to a new 9-12 month LEAPS

### Short Leg (Near-term Calls)
- **Delta**: 0.20-0.30 (70-80% probability OTM). More aggressive: 0.30-0.35 for higher income
- **Strike**: At or above a technical resistance level
- **DTE**: 30-45 DTE sweet spot. Weekly cycles (7-14 DTE) collect less premium but reduce risk of being tested
- **Earnings rule**: Do NOT sell a short call that spans an earnings date. Either sell expiring before earnings, or wait until after

## Position Sizing
- **Capital requirement**: LEAPS cost (debit paid). Example: $38 x 100 = $3,800 per contract vs. $20,000 for 100 shares of a $200 stock
- **Max loss**: Debit paid for LEAPS minus all premium collected from short calls. In practice, if the stock drops significantly, you lose most of the LEAPS value
- **Portfolio allocation**: Size based on the LEAPS cost as your "invested capital." Max 7-10% of portfolio per PMCC position
- **Number of contracts**: (Target allocation in $) / (LEAPS cost per contract x 100)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | +0.50 to +0.65 (net of short call) | Increases as short call decays; resets each short call cycle |
| Gamma | Low (deep ITM LEAPS has low gamma) | Short call gamma increases near expiry -- watch for pin risk |
| Theta | Slightly positive (short call theta > LEAPS theta) | Net theta positive as long as short call is generating income |
| Vega  | Positive (LEAPS has large vega, short call has small vega) | IV increase helps the long leg more than it hurts the short leg |

## Exit Protocol
- **Profit Target**: When total return (LEAPS appreciation + cumulative short call premium) reaches 30-50% of initial debit, consider closing
- **Stop Loss**: If stock drops to the LEAPS strike (your long call goes ATM), the position is failing. Close if LEAPS value drops below 50% of entry cost
- **Time Stop**: Roll LEAPS when it reaches 90-120 DTE. If you can't roll profitably, close the position
- **Adjustment Trigger**: If stock rallies to within 2% of short call strike, act (roll up or close short call)

## Adjustments & Rolling

### Short Call Management (Monthly Cycle)
1. **Expires OTM (ideal)**: Let it expire, sell a new 30-45 DTE call at delta 0.20-0.30
2. **Stock approaches short strike**: Roll up and out -- buy back the short call, sell a higher strike with more DTE. Aim for net credit on the roll
3. **Stock blows through short strike**: This is the most critical scenario:
   - If short call is ITM with < 7 DTE: Buy it back (take the loss on the short call)
   - Your LEAPS gained value, so the net P&L is usually still positive
   - **Key rule**: Never let the short call get assigned while holding LEAPS. You'd be forced to exercise the LEAPS to deliver shares, losing all remaining extrinsic value

### LEAPS Management
- **Roll at 90-120 DTE**: Sell current LEAPS, buy a new 9-12 month LEAPS at the same relative delta
- **Stock has rallied significantly**: Roll to a higher strike LEAPS to lock in gains
- **Stock has dropped**: Evaluate thesis. If still bullish, hold. If thesis broken, close everything

## Risk Controls
- **Max loss = LEAPS debit - total premiums collected**: Track cumulative premiums to know your adjusted cost basis
- **Short call placement rule**: NEVER sell a short call below your LEAPS break-even price (LEAPS strike + LEAPS debit - premiums collected). This creates an unmanageable situation if assigned
- **Earnings protocol**: Remove short call before earnings window (T-5). Reopen after earnings
- **Vega risk**: A sudden IV spike helps your LEAPS but hurts if you need to buy back the short call. Monitor net vega
- **Dividend risk**: If stock goes ex-dividend and your LEAPS is deep ITM, early exercise risk is low but not zero. Check before ex-div dates

## Common Traps
- **Buying cheap LEAPS (low delta)**: A 0.50 delta LEAPS is NOT a stock substitute. It has too much extrinsic value and gamma exposure. Always buy 0.75+ delta
- **Selling short calls too close to the money**: Chasing higher premium by selling 0.40-0.50 delta short calls means you'll be rolled/tested constantly. Stick to 0.20-0.30 delta
- **Ignoring the extrinsic value test**: If your LEAPS has $15 of extrinsic value on a $200 stock, you need to collect $15 in short call premiums just to break even on time decay. That might take 6+ cycles
- **Forgetting to roll the LEAPS**: At 60 DTE, your LEAPS is no longer acting like a stock substitute. Roll early
- **Running through earnings**: Selling short calls into earnings is asking for trouble. The stock gaps, your short call goes deep ITM, and you're stuck in a losing roll
- **Not tracking cumulative P&L**: This is a multi-cycle strategy. Track each short call cycle's P&L and your running cost basis on the LEAPS. Without tracking, you can't make informed roll decisions
- **Selling short calls for tiny premium**: If the short call only generates $0.30/contract, the bid-ask spread might eat half your profit. Minimum target: $1.00/contract premium
