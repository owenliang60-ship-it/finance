# Strategy: Risk Reversal (风险反转)
> Synthetic/Directional | Complexity: Intermediate | Max Risk: Undefined on one side | IV Environment: Any

## Overview
A risk reversal combines a short OTM put with a long OTM call (bullish) or vice versa (bearish). It creates a synthetic directional position with zero or near-zero upfront cost by using the premium collected from the short option to finance the long option. This is a strong conviction play that expresses a clear directional view while accepting unlimited risk on the side you're selling.

The bullish version (short put + long call) is essentially saying: "I'm willing to buy the stock at the put strike and I believe it's going higher than the call strike." It's frequently used as an aggressive alternative to buying stock outright, or to finance a directional bet at minimal cost.

## Structure

### Bullish Risk Reversal
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | OTM Put | Below current price (typically 5-10% OTM) | Same expiry |
| 2 | Buy | OTM Call | Above current price (typically 5-10% OTM) | Same expiry |

### Bearish Risk Reversal
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Buy | OTM Put | Below current price | Same expiry |
| 2 | Sell | OTM Call | Above current price | Same expiry |

**Net Cost**: Ideally zero or small credit/debit (< $0.50 net).

## When to Use
- High conviction directional thesis with a defined catalyst
- IV skew is favorable: you want to sell the "expensive" side and buy the "cheap" side
- You are willing to own the stock at the put strike (bullish version)
- Replacement for buying shares when you want capital efficiency
- OPRMS DNA rating S/A with Timing A/S (strong conviction required for undefined risk)

## When NOT to Use
- Uncertain direction or low conviction -- undefined risk on one side makes this dangerous
- High IV rank on the side you're selling without wanting actual assignment
- Earnings within 10 days (binary risk + IV crush on your long leg)
- Illiquid options where the skew spread eats your edge
- B/C DNA stocks -- never sell naked puts on weak names

## Entry Criteria
- **IV Environment**: Any, but pay attention to skew. Ideal when put IV > call IV (bullish version)
- **Direction**: Strong bullish or bearish conviction (don't do this for a "maybe")
- **Time Frame**: 30-90 DTE optimal; shorter DTE increases gamma risk on short leg
- **Liquidity**: OI > 500 on both strikes, bid-ask < 8% of mid price
- **Skew Check**: The side you're selling should have higher implied vol than the side you're buying

## Strike & Expiry Selection

### Strike Selection
- **Bullish**: Short put at a price where you'd happily buy the stock (think support level or -1 SD). Long call at your target or resistance level
- **Delta guidance**: Short put delta ~0.25-0.35, long call delta ~0.25-0.35 for balanced structure
- **Zero-cost target**: Adjust strikes until net premium is near zero. Acceptable range: $0.50 debit to $0.50 credit
- **Skew exploitation**: If put skew is steep, you can widen the put strike further OTM and still collect enough to fund the call

### Expiry Selection
- **45-60 DTE sweet spot**: Enough time for thesis to play out, manageable theta on short leg
- **Avoid < 21 DTE**: Gamma risk on the short leg accelerates dangerously
- **LEAPS version (180+ DTE)**: Valid for high-conviction, long-term thesis; essentially a synthetic stock position

## Position Sizing
- **Max loss calculation**: Short put side has undefined risk down to zero: max loss = (put strike - 0) x 100 x contracts
- **Practical sizing**: Treat max loss as if you'd be assigned on the put. Size as if buying that many shares at the put strike
- **Portfolio rule**: Total assignment value should not exceed your intended position size for the stock (typically 5-15% of portfolio)
- **Margin requirement**: Broker requires margin on the short leg (typically 20% of underlying value)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | +0.40 to +0.70 (bullish); negative for bearish | Increases as stock moves toward call; decreases toward put |
| Gamma | Near zero at entry (both legs OTM) | Increases significantly as either strike is approached |
| Theta | Near zero if balanced | Becomes negative if stock near call (long leg), positive if near put (short leg) |
| Vega  | Mixed: long call has +vega, short put has -vega | Net vega depends on IV skew; typically slightly positive for bullish version |

## Exit Protocol
- **Profit Target**: Close when long call reaches 100-200% gain, or stock hits target. Don't get greedy -- the short put caps your ability to hold indefinitely
- **Stop Loss**: Close entire structure if stock breaks below the short put strike by more than 3%. At that point, your thesis is broken
- **Time Stop**: Close or roll at 21 DTE. Do not let the short leg enter gamma acceleration zone
- **Adjustment Trigger**: If stock drops within 5% of short put strike, evaluate: roll put down and out, or close entirely

## Adjustments & Rolling
- **Stock moves against you (drops toward short put)**: Roll the put down and out (further OTM, later expiry) to collect more credit and buy time. Accept that the call is now near-worthless
- **Stock moves in your favor (rallies past call)**: Consider closing the short put (now nearly worthless) to remove risk, and let the long call run. Or close the entire structure for a profit
- **Roll to next cycle**: If thesis intact but time running out, roll both legs out 30 days at the same relative strikes
- **Convert to spread**: If risk tolerance changes, buy a further OTM put to cap downside risk (becomes a 3-leg structure)

## Risk Controls
- **Assignment risk**: Be prepared to take assignment on the short put at all times. Have the capital or margin available
- **Max portfolio exposure**: No more than 2 risk reversals simultaneously (combined assignment value < 30% of portfolio)
- **Correlation check**: Don't run bullish risk reversals on 3 correlated tech stocks simultaneously
- **Earnings blackout**: Close or convert to defined-risk structure before T-5
- **Gap risk**: Overnight gaps can blow past your short strike. Always size assuming a 10% gap scenario

## Common Traps
- **Treating it as "free money"**: The zero-cost structure is not free -- you're accepting assignment risk. The risk is real and can be substantial
- **Ignoring assignment probability**: A 0.30 delta put has ~30% chance of being ITM at expiry. That's not negligible
- **Selling the expensive side in the wrong direction**: If you're bullish but put IV is cheap and call IV is expensive, the risk reversal economics don't work. Check skew first
- **Holding through earnings**: IV crush destroys the long leg while assignment risk spikes on the short leg. Worst of both worlds
- **Over-sizing**: Because it's "zero cost" to enter, traders put on too many contracts. Size based on assignment value, not entry cost
- **Not having a plan for assignment**: If you get assigned on the short put, what's your plan? If the answer is "panic," you shouldn't be in this trade
