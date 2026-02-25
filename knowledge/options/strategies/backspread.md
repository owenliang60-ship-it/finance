# Strategy: Backspread (反比率价差 / 尾部保护)
> Ratio/Tail Protection | Complexity: Advanced | Max Risk: Defined if done for credit | IV Environment: Low IV

## Overview
A backspread (also called a ratio backspread) involves selling one option and buying more options at a further OTM strike, typically in a 1:2 or 1:3 ratio. It's a volatility play with directional bias that profits from large moves in the direction of the long options. The defining feature is unlimited profit potential in one direction with limited or zero risk if entered for a credit.

The call backspread (sell 1 ITM/ATM call, buy 2 OTM calls) is bullish and profits from explosive upside moves. The put backspread (sell 1 ITM/ATM put, buy 2 OTM puts) is bearish and acts as tail-risk protection. The beauty of the backspread is that if entered for a credit, the worst case is a small profit or break-even when the stock moves in the opposite direction.

## Structure

### Call Backspread (看涨反比率, 1:2)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | 1x ITM/ATM Call | Lower strike (at or slightly ITM) | Same expiry |
| 2 | Buy | 2x OTM Call | Higher strike | Same expiry |

### Put Backspread (看跌反比率, 1:2)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | 1x ITM/ATM Put | Higher strike (at or slightly ITM) | Same expiry |
| 2 | Buy | 2x OTM Put | Lower strike | Same expiry |

**Net Cost**: Ideally entered for a small credit or zero cost. If entered for a debit, max loss is greater.

## When to Use
- You expect a large move in one direction but want protection if wrong
- Low IV environment: you're buying vol cheaply on the OTM legs
- Tail-risk hedging: put backspreads protect against crashes while maintaining upside
- Pre-catalyst play where the move could be outsized (FDA, antitrust, major product launch)
- You have a view on both direction AND magnitude (must be large to profit)

## When NOT to Use
- IV is already high -- the OTM options you're buying are expensive, destroying the risk/reward
- You expect a moderate, gradual move (the "danger zone" between the strikes kills you)
- The stock is range-bound with no catalyst for a large move
- Tight range-bound action with declining IV -- this position bleeds theta in the dead zone
- Short time to expiry (< 21 DTE): gamma can flip against you violently

## Entry Criteria
- **IV Environment**: Low IV (IV Rank < 30%). You want to buy cheap vol
- **Direction**: Strong directional conviction with expectation of a LARGE move (> 10%)
- **Time Frame**: 45-90 DTE recommended. Need time for the move to develop
- **Liquidity**: OI > 300 on all strikes involved. Both strikes need tight markets
- **Credit check**: Structure the ratio so the trade is entered for a net credit (or zero cost). This ensures no risk if the stock moves the wrong way

## Strike & Expiry Selection

### Strike Selection -- Call Backspread
- **Short call**: ATM or slightly ITM (delta 0.50-0.60). This is your financing leg
- **Long calls**: 5-10% OTM (delta 0.25-0.35 each). This is where your profit comes from
- **Width**: The wider the spread, the larger the "danger zone." Keep width to 1-2 standard deviations
- **Credit target**: Sell 1x $150 call for $8.00, buy 2x $160 calls for $3.80 each ($7.60 total). Net credit: $0.40

### Strike Selection -- Put Backspread
- **Short put**: ATM or slightly ITM (delta -0.50 to -0.60)
- **Long puts**: 5-10% OTM (delta -0.25 to -0.35 each)
- **Tail protection note**: For crash hedging, go wider on the puts (15-20% OTM). You want protection against large moves, and the wider puts are cheaper

### Expiry Selection
- **45-90 DTE**: Optimal. Gives time for the catalyst to trigger the move
- **Avoid < 30 DTE**: Time decay kills OTM legs too fast
- **LEAPS backspreads**: Possible for structural hedges but expensive and tie up margin

## Position Sizing
- **If entered for a credit**: Max loss occurs between the two strikes at expiry. Calculate: (width of spread x 100) - net credit received
- **Example**: Short 1x $150 call, long 2x $160 calls for $0.40 credit. Max loss at expiry if stock = $160: ($10 width x 100) - $40 credit = $960
- **Portfolio sizing**: Max loss should not exceed 1-2% of portfolio
- **Contract ratio**: Always maintain the ratio (1:2 or 1:3). Don't go 1:1 (that's just a spread) or 1:4 (over-leveraged)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Slightly directional (+0.10 to +0.30 for call backspread) | Increases dramatically if stock moves toward long strikes |
| Gamma | Positive (you own more options than you sold) | Long gamma increases as expiry approaches -- this is why you want a big move |
| Theta | Negative (you own more time value than you sold) | Theta accelerates if stock sits between strikes -- the danger zone |
| Vega  | Positive (long more options = net long vol) | IV spike benefits the position; IV crush hurts badly |

## Exit Protocol
- **Profit Target**: If stock moves strongly in your direction, close when long legs are 150-300% profitable. The structure has unlimited upside but diminishing marginal value per dollar move
- **Stop Loss**: If entered for a credit -- no action needed if stock moves wrong way (you keep the credit). If entered for a debit -- close if stock settles in the danger zone and you've lost 50% of max loss
- **Time Stop**: Close at 21 DTE. If the big move hasn't happened by then, theta will destroy the remaining value
- **Adjustment Trigger**: If stock enters the danger zone (between strikes) with > 30 DTE remaining, consider closing the short leg to convert to a simple long position

## Adjustments & Rolling
- **Stock in danger zone**: Close the short leg (buy it back), leaving you with 2x long options. This costs money but removes the worst-case scenario
- **Stock moves strongly in your favor**: Close one of the long options to lock in profits, keeping one for further upside ("free" position after covering cost)
- **IV spikes before the move happens**: Take profits on the vega gain. If IV rank goes from 20% to 60%, the position has profited even without a stock move
- **Rolling out in time**: If thesis intact but time running short, roll the entire structure to the next monthly expiry. Try to maintain the credit
- **Ratio adjustment**: If you want to reduce risk, close the short leg and one long leg, converting to a single long option

## Risk Controls
- **Credit entry is key**: Always aim to enter for a credit. This eliminates risk on the wrong-direction move
- **Danger zone awareness**: Calculate your max loss at the long strike at expiry. This is your true risk
- **Margin requirement**: Brokers treat the short leg as a spread (margin = width of spread). The extra long leg doesn't increase margin
- **Max exposure**: No more than 2% of portfolio at risk in the danger zone
- **Catalyst confirmation**: If the expected catalyst passes without a move, close the position. Don't hope for a delayed reaction

## Common Traps
- **Entering for a debit**: A backspread entered for a debit has risk in BOTH directions -- up if the move doesn't happen, down if it goes the wrong way. Always strive for credit entry
- **Danger zone paralysis**: The worst outcome is the stock sitting right at the long strike at expiry. Many traders freeze, hoping it'll move. Set a time stop and stick to it
- **Wrong IV environment**: Buying OTM options in high IV is anti-edge. The options are expensive, and IV contraction destroys your position. Low IV entry is critical
- **Too wide strikes**: A call backspread with strikes $30 apart on a $100 stock has a massive danger zone. Keep width proportional to expected move (1-2 standard deviations)
- **Forgetting about exercise/assignment**: If the short leg is ITM at expiry, you'll be assigned. Make sure you can handle the assignment and have shares (for short call) or cash (for short put) ready
- **Over-complicating with ratios**: 1:2 is the standard. 1:3 gives more upside but wider danger zone. Don't go 1:4 or 1:5 -- the margin and risk become unmanageable
