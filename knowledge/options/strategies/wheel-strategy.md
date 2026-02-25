# Strategy: Wheel Strategy (飞轮策略 / CSP-CC 循环)
> Composite/Cash-Secured Put to Covered Call Cycle | Complexity: Intermediate | Max Risk: Undefined downside | IV Environment: High IV

## Overview
The Wheel is a systematic premium-selling strategy that cycles between two phases: selling cash-secured puts (CSP) to potentially acquire shares at a discount, and selling covered calls (CC) on the acquired shares to generate income while waiting for a profitable exit. It's a disciplined framework for monetizing a stock you want to own long-term, combining directional conviction with consistent income generation.

Phase 1 (CSP): You sell puts at your target entry price, collecting premium. If the stock stays above, you keep the premium and repeat. If assigned, you acquire shares at a net cost below the strike (strike - premium collected).
Phase 2 (CC): Once you hold shares, you sell calls above your cost basis to generate income. If called away, you sell at a profit and return to Phase 1. If not called away, you keep the premium and sell another call.

The Wheel is NOT a "risk-free income machine." It's a systematic way to manage a stock position you'd own anyway.

## Structure

### Phase 1: Cash-Secured Put
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | OTM Put | At target entry price (support level) | 30-45 DTE |

### Phase 2: Covered Call (After Assignment)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Hold | 100 Shares | Acquired via assignment | N/A |
| 2 | Sell | OTM Call | Above cost basis | 30-45 DTE |

## When to Use
- DNA S/A stocks you genuinely want to own for the long term
- IV is elevated (more premium to collect in both phases)
- You have sufficient cash to secure the put (or buy 100 shares)
- You're patient and willing to hold shares through drawdowns
- The stock pays dividends (bonus income while holding during Phase 2)
- You have a clear target entry price based on fundamentals

## When NOT to Use
- DNA B/C stocks -- never wheel stocks you don't want to own
- The stock is in a structural downtrend (you'll get assigned and keep losing as it drops further)
- IV is too low -- the premium collected doesn't justify the capital commitment
- You can't afford 100 shares (or the cash to secure the put)
- You're trying to "wheel" highly volatile meme stocks for premium -- this ends badly
- The stock has binary event risk (FDA, antitrust) that could crater the price

## Entry Criteria
- **IV Environment**: IV Rank > 30% (higher is better; more premium per cycle)
- **Direction**: Bullish long-term. You must be willing to own this stock
- **Time Frame**: 30-45 DTE per cycle. Full wheel cycle: 2-6 months typically
- **Liquidity**: OI > 1,000 at common strikes, weekly options available preferred
- **OPRMS Check**: DNA S or A only. Timing B or better (don't sell puts into a crashing stock)
- **Fundamental conviction**: Would you buy this stock TODAY at the put strike? If no, don't sell the put

## Strike & Expiry Selection

### Phase 1 Strike (CSP)
- **Strike = target entry price**: This is the most important rule. Not a random delta number
- **Technical support**: Place the put strike at or below a major support level (50-day MA, previous breakout level, Fibonacci retracement)
- **Delta guidance**: 0.20-0.35 delta. But support level takes priority over delta
- **Premium minimum**: Collected premium should be >= 1% of strike price per 30 days (annualized ~12%). If not, premium is too thin
- **Example**: Stock at $150, support at $140. Sell $140 put for $2.50 (1.8% yield). Effective entry if assigned: $137.50

### Phase 2 Strike (CC)
- **Strike > cost basis**: This is the hard rule. Never sell calls below your cost basis (you'd lock in a loss)
- **Cost basis calculation**: Assignment strike - total premiums collected in all CSP cycles
- **Delta guidance**: 0.20-0.30 (70-80% probability of keeping shares)
- **Technical resistance**: Place the call strike at or above resistance
- **Premium minimum**: >= 0.5% of stock price per 30 days

### Expiry Selection (Both Phases)
- **30-45 DTE**: Optimal theta decay window
- **Weekly cycles**: For higher frequency but smaller premium per cycle
- **Avoid > 60 DTE**: Ties up capital too long per cycle

## Position Sizing
- **Phase 1 (CSP)**: Need 100% cash to cover assignment: put strike x 100 x contracts
- **Portfolio allocation**: Single wheel position should use max 15% of portfolio (DNA-S) or 10% (DNA-A)
- **Contract count**: (Target allocation in $) / (put strike x 100)
- **Example**: $300K portfolio, 10% allocation to AAPL wheel at $140 strike. $30,000 / $14,000 = 2 contracts
- **Phase 2 (CC)**: Already holding shares, no additional capital needed (margin for the call = shares held)

## Greeks Profile

### Phase 1 (CSP)
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | +0.20 to +0.35 | Increases if stock drops toward strike |
| Gamma | Low (OTM) | Increases near expiry if stock near strike |
| Theta | Positive (~$5-15/day per contract) | Accelerates 21-14 DTE |
| Vega  | Negative (short vol) | IV drop benefits you |

### Phase 2 (CC)
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | +0.70 to +0.80 (100 shares - short call delta) | Decreases if stock rallies past call strike |
| Gamma | Low (stock gamma = 0, short call gamma small) | Increases near expiry if stock near call strike |
| Theta | Positive (short call decays) | Accelerates 21-14 DTE |
| Vega  | Slightly negative (short call) | IV drop benefits short call slightly |

## Exit Protocol

### Phase 1 Exits
- **Profit target**: Close CSP at 50-75% of premium received. Don't wait for 100%
- **Assignment**: Let it happen if stock is below strike at expiry. You wanted to buy the stock at this price
- **Stop loss**: If stock drops more than 10% below your put strike AND thesis has changed, buy back the put at a loss. Don't accept assignment on a broken thesis
- **Roll trigger**: At 21 DTE, if put is OTM and premium < 20% remaining, close and sell new 30-45 DTE put

### Phase 2 Exits
- **Called away (profit)**: Shares sold at call strike. Return to Phase 1. Celebrate -- this is a win
- **Call expires OTM**: Keep shares, sell new 30-45 DTE call. Repeat
- **Stop loss**: If stock drops 15%+ below cost basis and thesis breaks, sell shares and take the loss. Don't sell calls at terrible prices trying to "recover"

## Adjustments & Rolling

### Phase 1 Adjustments
- **Stock drops to put strike**: Roll down and out (lower strike, 30 more days) for a net credit if thesis intact
- **Stock rallies away**: Let the put expire worthless, sell a new cycle. Consider adjusting strike closer to current price if thesis is even more bullish
- **Max rolls**: Don't roll more than 2-3 times. If the stock keeps dropping, accept assignment or walk away

### Phase 2 Adjustments
- **Stock rallies to call strike**: Roll up and out (higher strike, 30 more days) for a net credit if you want to keep shares. Only if you can maintain strike > cost basis
- **Stock drops significantly**: Stop selling calls temporarily -- wait for a bounce. Selling calls at low prices locks in a bad exit price
- **Dividend capture**: Time your call expiry to NOT overlap ex-dividend dates. You want to own shares through ex-div

## Risk Controls
- **Single stock concentration**: Max 2 wheel positions (one in Phase 1, one in Phase 2) in the same sector
- **Total wheel exposure**: Combined Phase 1 + Phase 2 should not exceed 30% of portfolio
- **Thesis validation every cycle**: Before selling the next put or call, re-check: "Do I still want to own this stock at this price?" If the answer is no, stop
- **Earnings handling**:
  - Phase 1: Don't sell puts that expire within 5 days of earnings. The post-earnings gap can assign you at a terrible effective price
  - Phase 2: Don't sell calls that span earnings unless you're willing to be called away on a post-earnings pop
- **Drawdown limit**: If a wheel position's total loss (including unrealized loss on shares) exceeds 5% of portfolio, pause and reassess

## Common Traps
- **"The premium makes up for the loss"**: No. If you sell a $140 put for $2.50 and the stock drops to $110, your loss is $27.50/share. The $2.50 in premium is a band-aid on a gunshot wound
- **Wheeling garbage stocks for high premium**: High IV on a weak stock means high premium AND high probability of catastrophic loss. Wheel quality stocks only (DNA S/A)
- **Selling calls below cost basis**: Desperation move after a drawdown. "I'll sell this $130 call on my $140 cost basis stock just to get some income." If assigned, you lock in a $10 loss. Never do this
- **Ignoring opportunity cost**: While your $30K is locked up in a wheel position, you can't use that capital elsewhere. If the broad market rallies 20% and your wheel stock is flat, you lost 20% in opportunity cost
- **Mechanical cycling without thesis checks**: "I always sell 30-delta puts every month" is not a strategy. Each cycle should involve a fresh thesis check. Markets change; your strikes should change with them
- **Over-wheeling**: Running 5+ wheel positions simultaneously. You can't monitor them all properly, and if the market drops, they all get assigned at once. Max 2-3 positions
- **Not tracking true P&L**: Track EVERY premium collected across ALL cycles. Your true cost basis = assignment price - sum of all CSP premiums - sum of all CC premiums. Without this number, you're flying blind
