# Strategy: Jade Lizard (翡翠蜥蜴)
> Combination/Premium Selling | Complexity: Advanced | Max Risk: Defined upside / Undefined downside | IV Environment: High IV

## Overview
The Jade Lizard is a three-legged premium selling strategy that combines a short put with a short call spread (bear call spread). The key construction rule: the total credit received must exceed the width of the call spread, which eliminates all upside risk. You can only lose money to the downside (short put assignment). It's essentially a more efficient way to sell premium on both sides of the market with no upside risk.

The name comes from the TastyTrade community, and the strategy is popular among premium sellers who want to collect theta from both sides while eliminating the risk of a rally. It's ideal when IV is high, you have a neutral-to-bullish bias, and you're willing to take assignment on the put side (similar to a CSP).

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | OTM Put | Below current price | Same expiry |
| 2 | Sell | OTM Call | Above current price | Same expiry |
| 3 | Buy | Further OTM Call | Above the short call | Same expiry |

**Critical Rule**: Total credit received > width of call spread. This eliminates upside risk entirely.

**Example**: Stock at $100. Sell $93 put for $2.00, sell $107 call for $1.50, buy $110 call for $0.50. Total credit: $3.00. Call spread width: $3.00. Credit ($3.00) >= width ($3.00). Upside risk = $0.

## When to Use
- IV is elevated (IV Rank > 50%) and you want to harvest premium from both sides
- Neutral to slightly bullish outlook -- you don't expect a big move in either direction
- You're willing to own the stock at the put strike (this is the key risk)
- You want more premium than a simple CSP provides
- The stock has support at a defined level where you'd place the short put
- Call skew is flat or slightly elevated, making the short call spread worthwhile

## When NOT to Use
- Low IV -- the credit received won't exceed the call spread width (violates the construction rule)
- You're bearish -- the short put will get crushed if the stock drops
- Major catalyst approaching (earnings, FDA) -- gap risk on the put side
- You're not willing to take assignment at the put strike
- The stock is in a strong downtrend -- this is not a bottom-fishing strategy

## Entry Criteria
- **IV Environment**: High IV (IV Rank > 50%). Minimum: IV Rank > 40% with favorable skew
- **Direction**: Neutral to slightly bullish. The put side is where all the risk lies
- **Time Frame**: 30-45 DTE standard premium-selling window
- **Liquidity**: OI > 500 on all three strikes. The call spread legs especially need tight markets
- **Credit test**: Total credit MUST exceed call spread width. If it doesn't, adjust strikes or don't trade
- **Put strike selection**: Must be at or below a price you'd happily own the stock

## Strike & Expiry Selection

### Short Put
- **Delta**: 0.20-0.30 (70-80% probability OTM)
- **Strike placement**: At a strong technical support level or below the last significant low
- **DNA requirement**: Only sell puts on DNA A+ stocks. Never sell puts on DNA B/C names

### Short Call Spread (Bear Call Spread)
- **Short call delta**: 0.15-0.25 (above resistance)
- **Call spread width**: $3-$5 for stocks under $200, $5-$10 for stocks over $200
- **The math**: Short put premium + short call spread net credit >= call spread width
- **Narrower is better**: A $3 wide call spread is easier to fill the "credit > width" rule than a $10 wide spread

### Expiry Selection
- **30-45 DTE**: Sweet spot for theta decay across all 3 legs
- **Weekly (7-14 DTE)**: Can work for aggressive theta play but less time for adjustments
- **Avoid > 60 DTE**: Ties up margin and the "no upside risk" property becomes less valuable with more time

## Position Sizing
- **Max loss**: Occurs on the put side. Max loss = (put strike x 100) - total credit received. This is the same as a CSP
- **Practical sizing**: Treat this as a CSP for sizing purposes. Ask: "Would I buy this many shares at the put strike?"
- **Portfolio rule**: Total put assignment value < 15% of portfolio for DNA-S, < 10% for DNA-A
- **Margin**: Broker charges margin on the short put (CSP margin). The call spread side requires (width - credit) which may be zero

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Slightly positive to neutral (+0.05 to +0.15) | Becomes more negative if stock drops toward put; neutral if stock stays between strikes |
| Gamma | Negative (short gamma on all sold options) | Accelerates near any short strike as expiry approaches |
| Theta | Positive (net seller of 2 options) | Strong theta decay, especially 21-14 DTE |
| Vega  | Negative (net short vol) | IV decrease benefits all legs; IV spike hurts |

## Exit Protocol
- **Profit Target**: Close at 50% of max profit (total credit received x 50%). Don't squeeze out the last 50% -- risk/reward deteriorates
- **Stop Loss**: Close if the stock drops within 3% of the short put strike. At that point, reassess thesis
- **Time Stop**: Close at 14 DTE. Gamma risk on 3 legs near expiry is significant
- **Adjustment Trigger**: If put delta exceeds 0.40, take defensive action (roll or close)

## Adjustments & Rolling
- **Stock drops toward put**:
  1. First line: Roll the put down and out (further OTM, 30 more days). Aim for a credit on the roll
  2. Second line: Close the entire position if the stock breaks below the short put
  3. The call spread side is now nearly worthless -- let it expire or close for pennies

- **Stock rallies toward call spread**:
  1. No action needed! Credit >= spread width means you have zero upside risk
  2. If you want to capture more upside, buy back the call spread cheaply and let the short put expire
  3. Re-establish the call spread at higher strikes if you want to continue the structure

- **Rolling to next cycle**:
  1. At 14-21 DTE, close all legs and reopen at the same relative strikes for the next 30-45 DTE cycle
  2. Re-verify the credit > width rule on the new position

## Risk Controls
- **Credit > width rule is LAW**: Never violate this. If you can't get enough credit, don't trade
- **Put strike = comfortable buy price**: Only sell puts where you'd genuinely buy the stock. OPRMS DNA A+
- **No earnings overlap**: Close before T-5. The gap risk on the put side is the primary danger
- **Max 3 jade lizards simultaneously**: Each one has put-side risk. Multiple jade lizards on correlated stocks creates concentrated downside exposure
- **Daily monitoring**: Check put delta daily. If it crosses 0.40, you need to act
- **Worst case scenario test**: What happens if the stock drops 15% overnight? Can you handle the assignment?

## Common Traps
- **Violating the credit > width rule**: If total credit is $2.80 and call spread is $3.00 wide, you have $0.20 of upside risk. That defeats the entire purpose. Adjust strikes until the math works
- **Ignoring the put side because "upside is free"**: The call side is free. The put side is a CSP. Treat it with the same respect you'd give any naked put
- **Over-collecting premium**: Adding the call spread premium to the put premium feels like "extra income." But you're still exposed to full downside on the put. The call spread doesn't hedge downside
- **Selling on weak stocks for higher premium**: High IV on a weak stock often means it's crashing for a reason. DNA B/C stocks with high IV are not jade lizard candidates
- **Skipping the math check**: Before entry, explicitly calculate: (a) total credit, (b) call spread width, (c) confirm credit >= width. Write it down. Don't estimate
- **Forgetting that this is 3 legs to manage**: More legs = more bid-ask slippage on entry, adjustment, and exit. Factor in $0.05-0.10 per leg per contract
