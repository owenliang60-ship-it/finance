# Strategy: Gamma Scalping (Gamma 刷单 / 波动率交易)
> Volatility Trading / Delta-Neutral | Complexity: Advanced | Max Risk: Defined (if hedged) | IV Environment: Low IV entry

## Overview
Gamma scalping is a volatility trading strategy that starts with a long straddle or strangle (long gamma position) and dynamically hedges the delta by trading the underlying stock. As the stock moves up, you sell shares to flatten delta; as it drops, you buy shares. Each hedge "locks in" a small profit from the gamma-driven delta change. Over time, these small scalps accumulate to offset (and ideally exceed) the time decay (theta) of the long options.

This is the purest expression of volatility trading: you're not betting on direction, you're betting that realized volatility (how much the stock actually moves) will exceed implied volatility (what the market priced in). If RV > IV, the scalps exceed theta decay, and you profit. If RV < IV, theta eats you alive. This is a professional market maker's bread-and-butter strategy.

## Structure

### Base Position: Long Straddle
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Buy | ATM Call | At current price | 45-90 DTE |
| 2 | Buy | ATM Put | Same strike | Same expiry |

### Delta Hedges (Dynamic)
| Action | Trigger | Instrument |
|--------|---------|------------|
| Sell shares | Delta becomes positive (stock rallies) | Underlying stock |
| Buy shares | Delta becomes negative (stock drops) | Underlying stock |

**Net Debit**: Cost of the straddle. This is your "theta budget" -- the amount you need to scalp back from delta hedges.

## When to Use
- IV is low relative to historical realized vol (IV Rank < 30%, ideally < 20%)
- You expect realized volatility to exceed implied volatility going forward
- Pre-catalyst periods where the market underestimates upcoming movement
- You have the infrastructure to monitor and hedge frequently (intraday or daily)
- The stock has a history of realized vol > implied vol (check RV/IV ratio over 30-60 days)
- You're genuinely directionally agnostic -- you don't care which way the stock moves, only that it DOES move

## When NOT to Use
- IV is already high (you're paying too much theta for the gamma)
- You can't monitor and hedge at least daily (unmanaged gamma bleeds theta)
- The stock is range-bound with declining realized vol
- Transaction costs (commissions + slippage) are high relative to scalp profits
- You have a directional view -- just buy a call or put instead of setting up a gamma scalp
- Overnight gaps are the primary source of moves (you can't scalp intraday during overnight gaps)

## Entry Criteria
- **IV Environment**: Low IV (IV Rank < 30%). The cheaper the straddle, the lower your theta hurdle
- **Direction**: Delta-neutral at entry. No directional bias
- **Time Frame**: 45-90 DTE for the straddle. Shorter = more gamma but more theta; longer = less theta but less gamma
- **Liquidity**: ATM options must have OI > 2,000 and bid-ask < 3% of mid. Underlying stock must have high share volume (> 2M daily) for clean delta hedges
- **RV/IV analysis**: Calculate trailing 30-day realized vol. If RV > IV by > 3 vol points, the setup is favorable
- **Hedging frequency**: You must be able to hedge at least once daily. Ideal: every 1% move in the underlying

## Strike & Expiry Selection

### Strike Selection
- **ATM straddle**: The standard. Maximum gamma at ATM
- **Alternative -- ATM strangle**: Buy slightly OTM call + slightly OTM put (1-2 strikes out). Lower cost but lower gamma. Wider scalp bands needed
- **Strike = current price exactly**: For maximum delta neutrality at entry. If stock is $150.50, use the $150 strike

### Expiry Selection
- **45-60 DTE**: Best gamma/theta ratio. Enough gamma to generate scalps, theta is manageable
- **60-90 DTE**: Lower theta but lower gamma per dollar. Better for lower-frequency hedging (daily vs. intraday)
- **Avoid < 30 DTE**: Theta accelerates too fast. You need to scalp massive moves daily to stay ahead
- **Avoid > 120 DTE**: Gamma is too low. Stock moves don't generate enough delta change to scalp meaningfully

## Position Sizing
- **Max loss**: Defined as the straddle debit (if you abandon the position with no scalps). In practice, losses are the debit minus accumulated scalp profits
- **Theta budget**: Calculate daily theta. This is what you "pay" to hold the position each day. Your scalps must exceed this amount on average
- **Example**: $100 stock. Buy 45 DTE $100 straddle for $8.00 ($800/contract). Daily theta ~$15/day/contract. You need to scalp > $15/day from delta hedges to break even
- **Portfolio allocation**: Max 3-5% of portfolio in gamma scalping positions. This is a specialized strategy, not a core allocation
- **Share hedge sizing**: Delta x 100 shares per contract. If delta drifts to +0.15, sell 15 shares per straddle contract

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | 0 (ATM straddle is delta neutral) | Drifts with stock movement; you hedge back to zero |
| Gamma | Maximum (both legs are ATM) | Decreases slowly at first, then rapidly as expiry approaches |
| Theta | Negative (this is the cost of holding gamma) | Accelerates as expiry approaches; the "clock" speeds up |
| Vega  | Positive (long both options) | IV increase benefits you (bonus); IV decrease hurts (your straddle loses value) |

## Exit Protocol
- **Profit Target**: There's no fixed profit target. The strategy generates ongoing P&L from scalps vs. theta. Exit when:
  1. Cumulative scalp profits exceed the straddle cost (you've won; close to lock in)
  2. A big move occurs (> 5%) and the straddle has significant intrinsic value -- close the whole position
- **Stop Loss**: Close if cumulative loss (theta decay minus scalps) reaches 50% of the straddle debit. The vol forecast was wrong
- **Time Stop**: Close at 14-21 DTE. Gamma accelerates but theta accelerates faster. The math turns unfavorable
- **IV change trigger**: If IV spikes > 30% from entry levels, consider closing. Your straddle has gained vega profit -- take it and re-enter if IV drops again

## Adjustments & Rolling

### Scalping Mechanics
1. **Set hedge bands**: Decide in advance how much delta drift triggers a hedge
   - **Tight bands (aggressive)**: Hedge when delta reaches +/- 0.10. More trades, more slippage, but captures more gamma
   - **Wide bands (conservative)**: Hedge when delta reaches +/- 0.25. Fewer trades, less slippage, but misses some gamma
   - **Typical**: Hedge when delta = +/- 0.15, or when stock moves ~1.5% from last hedge price
2. **Hedge execution**: Trade shares, not options. Shares have no theta/vega/gamma -- they're pure delta
3. **Accumulation tracking**: Keep a running log of all hedge trades. Total scalp P&L = sum of all share trades' P&L

### Rolling the Straddle
- At 21 DTE: Close the straddle and open a new 45-60 DTE straddle at the current ATM strike
- Calculate: cumulative scalp P&L + remaining straddle value - new straddle cost. If the net is positive, the strategy is working
- Re-evaluate RV/IV before rolling. If RV has dropped below IV, don't roll -- the edge is gone

### Position Adjustments
- **Stock trends strongly in one direction**: Your hedges have been one-directional (all buys or all sells). Consider closing half the straddle (the losing leg) and keeping the winning leg as a directional play
- **Realized vol collapses**: Scalps are too small to cover theta. Close the position early to preserve remaining premium
- **IV spikes (exogenous event)**: Your straddle gains vega value. Take the windfall -- close and reassess

## Risk Controls
- **Daily theta tracking**: Know your daily theta to the penny. This is your "cost of doing business"
- **Scalp P&L vs. theta**: Calculate the ratio daily. If scalp P&L / theta < 0.8 over 5+ days, the realized vol isn't high enough. Consider closing
- **Max holding period**: Don't hold a gamma scalp beyond 30 DTE. Theta acceleration makes the math nearly impossible to win
- **Transaction cost budget**: Commission + slippage per hedge should be < 20% of the expected scalp profit. If commissions are $1/trade and expected scalp is $3, the ratio is 33% -- too high
- **No earnings exposure**: Unless you specifically want to bet on earnings vol, close before T-5. Post-earnings IV crush will devastate your straddle value
- **Hedging discipline**: This is not optional. If you buy a straddle and don't hedge, you're not gamma scalping -- you're speculating on direction. The discipline of hedging IS the strategy

## Common Traps
- **"I'll scalp when it moves more"**: Waiting for bigger moves means the stock might reverse before you hedge, and you've held through theta decay for nothing. Hedge at your pre-set bands religiously
- **IV misjudgment**: Buying a straddle at "low IV" that's actually fair value. Low IV rank doesn't always mean cheap -- compare to historical realized vol, not just IV's own history
- **Over-trading**: Hedging too frequently (every $0.50 move) generates more commission than scalp profit. Find the optimal frequency through backtesting or the rule of thumb: hedge at ~1 SD daily move
- **Ignoring transaction costs**: Each hedge costs bid-ask spread on shares + commissions. On 50+ hedges over a 45-day period, this adds up to $200-$500 per contract. Budget for it
- **Holding too long**: "The straddle still has some gamma left at 14 DTE." Yes, but theta is eating $25/day and gamma only generates $10/day in scalps. The math has flipped. Close
- **Confusing realized vol types**: Intraday vol (good for scalping) vs. close-to-close vol (what IV prices in) are different. High intraday vol with low close-to-close vol = scalping paradise. Low intraday vol = gamma scalping doesn't work regardless of IV
- **One big move destroys discipline**: Stock drops 5% in a day. You get excited, don't hedge properly, and it reverses 3% the next day. You've missed both scalps. Stick to the system
- **Not tracking cumulative P&L**: Without a trade log showing each hedge trade, straddle cost, daily theta, and running total, you're flying blind. This strategy REQUIRES meticulous record-keeping
