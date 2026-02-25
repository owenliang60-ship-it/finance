# Strategy: Collar (领口策略 / 持仓保护)
> 对冲/持仓保护 | Complexity: Beginner | Max Risk: Defined | IV Environment: Low

## Overview
A collar wraps a long stock position with a protective put (downside insurance) and a covered call (upside cap that finances the put). It creates a defined-risk range around your stock holding: you give up some upside in exchange for downside protection. The collar is the institutional go-to for protecting concentrated positions or locking in gains after a big run-up. When structured as a zero-cost collar, the call premium fully offsets the put cost — you get free insurance at the price of capping your gains.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 0   | Hold      | Stock | 100 shares per contract | Existing position |
| 1   | Buy       | Put  | OTM (below current price) | 60-90 DTE |
| 2   | Sell      | Call | OTM (above current price) | Same expiry |

- Net cost: Ideally zero (call premium = put premium) or small net debit.
- Max loss = current price - put strike + net debit. Max gain = call strike - current price - net debit.
- Breakeven: Same as stock entry (if zero-cost collar).

## When to Use
- Protecting unrealized gains on a long position after a significant rally (锁定利润)
- Holding a concentrated stock position (>10% of portfolio) that you can't or won't sell (tax reasons, lockup, conviction)
- Ahead of known risk events (earnings, macro) when you want to stay long but limit downside
- When you want to hedge but buying puts alone is too expensive
- Transitioning out of a position gradually — the collar lets you hold with peace of mind
- Tax-efficient hedging: Collar avoids triggering a capital gains event that selling would cause

## When NOT to Use
- You're willing to sell the stock — just sell it and buy back later. A collar is for "I want to keep the shares."
- Very bullish — the covered call caps your upside at the call strike. Don't collar a stock you expect to rally 20%.
- IV is very high — the call premium is rich, but so is the put. The collar may be cheap but you're locking in a narrow range. Consider a put spread instead.
- Short-term holding — collars work best over 60-90+ days. For 2-week protection, just buy a put.
- Stock is very illiquid or options have wide bid-ask spreads (4 legs with stock = execution cost matters)

## Entry Criteria
- **IV Environment**: Low-to-moderate IV rank (< 40%) is ideal. Low IV means cheap puts (your insurance) and the call premium you collect is enough to offset. In high IV, both legs are expensive and the collar range narrows.
- **Direction**: You want to stay long but protect against a drawdown of X%.
- **Time Frame**: 60-90 DTE typical. Roll quarterly for continuous protection.
- **Liquidity**: OI > 500 on both strikes; bid-ask < $0.10.
- **Zero-cost target**: Find the call strike where the call premium approximately equals the put premium. This is the "natural" collar level.
- **Acceptable range**: The put-to-call range should cover your risk tolerance. A collar on a $100 stock with a $90 put and $115 call gives you 10% downside protection with 15% upside room.

## Strike & Expiry Selection
- **Put strike**: 5-10% below current price. This is your "deductible" — how much loss you're willing to absorb. Typical choices:
  - 5% OTM put: Tighter protection, but more expensive (larger deductible on covered call)
  - 10% OTM put: Cheaper protection, but absorbs more downside before kicking in
- **Call strike**: Set by the zero-cost constraint. Match the call premium to the put cost.
  - If $90 put costs $3.50 on a $100 stock, find the call that sells for ~$3.50 (might be $112 or $115)
  - Willing to pay a small debit ($0.50-$1.00)? Move the call strike higher for more upside room.
- **Expiry**: 60-90 DTE. Quarterly rolls are standard for ongoing protection.
  - Shorter expiry = cheaper but more frequent management
  - Longer expiry = more expensive but less management
- **Skip selection**: Use standard monthly expirations for best liquidity.

## Position Sizing
- Collar is applied per 100 shares of stock. Size = your existing stock position.
- No additional capital required for a zero-cost collar (the call finances the put).
- If paying a net debit: debit × contracts × 100 = cost of protection. Should be < 1% of position value per quarter.
- Example: 500 shares of AAPL at $175. Buy 5 × $160 puts, sell 5 × $195 calls. Zero cost. Protected below $160, capped above $195.

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Reduced from +1.0 to roughly +0.60 to +0.75 | Stock delta + put delta + short call delta |
| Gamma | Small net (put gamma partially offset by short call) | Increases near expiry on both wings |
| Theta | Approximately neutral (zero-cost collar) | Put bleeds theta, call earns theta — they offset |
| Vega  | Small net positive (put vega > call vega for OTM strikes) | Minor IV sensitivity in a zero-cost collar |

## Exit Protocol
- **Profit Target**: If stock reaches the call strike, you have two choices:
  1. Let it be called away (sell at the call strike) — if you're ready to exit
  2. Roll the call up and out to keep the position and extend upside
- **Stop Loss**: If stock drops to the put strike, your loss is capped. You can:
  1. Exercise the put (sell stock at put strike) — clean exit
  2. Sell the put for profit and hold the stock (if you believe in recovery)
  3. Roll the entire collar down to a lower range
- **Time Stop**: Roll the collar 14-21 days before expiry to maintain continuous protection. Never let the collar expire with the stock between the strikes — you'll be unprotected.
- **Collar removal**: If your thesis changes to very bullish, buy back the call and sell the put. You're back to naked long stock.

## Adjustments & Rolling
- **Quarterly roll**: The core maintenance action. 2-3 weeks before expiry, close the current collar and open a new one 60-90 days out. Aim for zero-cost or small debit.
- **Roll the call up**: If stock is rallying toward the call strike and you want to keep it, buy back the call and sell a higher-strike call further out. May cost a small debit.
- **Roll the put up**: If stock has rallied significantly, roll the put up to lock in more of the gains. The higher put strike means tighter protection at the new elevated level.
- **Widen the collar**: If you want more upside room, accept a deeper OTM put (less protection) and move the call higher. Trade-off between protection and participation.
- **Unwind one leg**: If circumstances change — e.g., you're now willing to accept downside risk for more upside — buy back the call and sell the put. Or vice versa.

## Risk Controls
- Collar is inherently risk-managed — max loss is defined from day one
- Ensure the collar range matches your risk tolerance: If a 10% drop is unacceptable, use a 5% OTM put, not 10%
- Tax implications: Collars may trigger constructive sale rules if too tight (IRS considers a collar with very tight strikes as a sale). Consult a tax advisor. General rule: keep put > 10% OTM to avoid constructive sale issues.
- Assignment risk: Short call can be assigned if ITM, especially near ex-dividend. Monitor and roll before ex-div if necessary.
- Opportunity cost: The capped upside is a real cost. Track what you would have made without the collar to evaluate whether continuous protection is worth it.
- Portfolio-level: Only collar positions that represent concentrated risk (>10% of portfolio). Diversified positions don't need individual collars — portfolio-level hedges are more efficient.

## Common Traps
- **Putting a collar on a diversified position**: If you have 30 stocks each at 3% weight, collaring each one is expensive and unnecessary. Use index hedges (SPY puts) instead.
- **Too-tight collar for "free protection"**: A collar from $95 to $105 on a $100 stock is essentially selling the position. You've locked in a $10 range. What's the point of holding?
- **Forgetting to roll**: The collar expires, and you're suddenly unprotected. Set calendar reminders. Roll 2-3 weeks before expiry.
- **Constructive sale trap**: The IRS may treat an overly tight collar as a taxable sale. If your collar range is < 15% wide, consult your tax advisor.
- **Collar on a stock you should just sell**: If you don't believe in the long-term thesis anymore, just sell. The collar is for conviction positions you want to protect, not for postponing a decision you've already made.
- **Ignoring dividends**: If the stock pays a dividend and the short call goes ITM, you may be assigned early and lose the dividend. Monitor the call delta vs. the upcoming dividend.
- **Zero-cost obsession**: Insisting on zero-cost can force you into selling calls too close to ATM or buying puts too far OTM. It's okay to pay $0.50-$1.00 per share for a better collar structure. The cost is < 1% per quarter — cheap insurance.
