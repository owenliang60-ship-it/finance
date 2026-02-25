# Strategy: Calendar Spread (时间价差 / 日历价差)
> Time Spread | Complexity: Intermediate | Max Risk: Defined | IV Environment: Any (benefits from IV rise)

## Overview
A calendar spread (also called a time spread or horizontal spread) involves selling a near-term option and buying a longer-term option at the same strike price. The core mechanic: near-term options decay faster than far-term options, so you profit from the differential time decay. The ideal outcome is the stock sitting right at the strike price when the short option expires, maximizing the spread's value.

Calendar spreads are theta-positive but vega-positive -- a rare combination. You collect theta from the faster-decaying short leg while maintaining positive vega exposure via the long leg. This makes calendars unique: they profit from both time passage AND volatility expansion. They're most commonly used for neutral, range-bound forecasts with an expectation that IV will remain stable or rise.

## Structure

### Call Calendar
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | Call | ATM or slightly OTM | Near-term (30-45 DTE) |
| 2 | Buy | Call | Same strike | Far-term (60-90 DTE) |

### Put Calendar
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Sell | Put | ATM or slightly OTM | Near-term (30-45 DTE) |
| 2 | Buy | Put | Same strike | Far-term (60-90 DTE) |

**Net Debit**: Always entered for a debit. The far-term option always costs more than the near-term option.

## When to Use
- You expect the stock to stay near the current price through the near-term expiry
- IV is currently low-to-moderate and you expect it to rise (vega-positive)
- Term structure is in contango (near-term IV < far-term IV) -- this is normal and favorable
- You want a defined-risk, time-decay strategy without directional conviction
- Post-earnings: sell the front-month (which still has elevated IV) against a back-month that has normalized

## When NOT to Use
- You expect a large directional move -- the calendar loses if the stock moves far from the strike
- IV is extremely high and likely to decline (IV crush hurts the long leg more than it helps the short leg)
- Term structure is inverted (near-term IV > far-term IV significantly) -- this makes the calendar expensive and fragile
- The stock has a binary event between the two expiries that could cause a large gap
- Bid-ask spreads are wide on far-term options (slippage eats your edge)

## Entry Criteria
- **IV Environment**: Any, but best when IV Rank is 20-50%. Calendars benefit from subsequent IV rise
- **Direction**: Neutral. You're betting the stock stays near the strike
- **Time Frame**: Short leg: 30-45 DTE. Long leg: 60-90 DTE. The gap between expiries should be 30-45 days
- **Liquidity**: OI > 300 at the strike in BOTH expiry months. Far-term chain must have reasonable bid-ask
- **Term structure check**: Near-term IV should be equal to or less than far-term IV. If near-term IV is significantly higher (> 5 vol points), the calendar is distorted and risky
- **Max cost**: Debit paid should be < 30% of the strike width potential (max value of the calendar at short-leg expiry)

## Strike & Expiry Selection

### Strike Selection
- **ATM strike**: The standard. Maximum theta differential at ATM. Best for pure neutral view
- **Slightly OTM call**: Mildly bullish lean (stock at $100, calendar at $105). Profits if stock drifts up 3-5%
- **Slightly OTM put**: Mildly bearish lean. Same logic, downside
- **Avoid deep OTM/ITM**: Time value differential is minimal far from ATM. The strategy doesn't work

### Expiry Selection
- **Short leg**: 30-45 DTE. This is where theta decay is optimal
- **Long leg**: 60-90 DTE. Close enough to maintain correlation but far enough to retain value
- **Gap between legs**: 30-45 days is ideal. Too close (< 21 days) = not enough differential decay. Too far (> 60 days) = too expensive and less sensitive to near-term theta
- **Same monthly cycle**: Many traders use consecutive monthly expiries (e.g., March/April or March/May)

## Position Sizing
- **Max loss**: The debit paid. This is your defined risk
- **Typical debit**: $2-$5 per spread on a $100-200 stock, depending on IV and width between expiries
- **Portfolio rule**: Max loss per calendar spread position < 1-2% of portfolio
- **Number of spreads**: (Risk budget in $) / (debit per spread x 100)
- **Example**: $300K portfolio, 1.5% risk = $4,500. Debit = $3.50/spread. Max 12-13 spreads

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (both legs are same strike, roughly offsetting) | Becomes directional if stock moves away from strike |
| Gamma | Slightly negative (short leg has more gamma than long leg at same strike) | Gamma risk increases as short leg nears expiry |
| Theta | Positive (short leg decays faster than long leg) | Theta peaks when stock is at strike with 7-14 DTE on short leg |
| Vega  | Positive (long leg has more vega than short leg) | IV rise increases spread value; IV drop decreases it |

## Exit Protocol
- **Profit Target**: Close at 25-40% of debit paid. Calendars have an asymmetric payoff -- the max profit is hard to realize because it requires perfect pin. Take profits early
- **Stop Loss**: Close if you've lost 50% of debit paid. The stock has moved too far from strike
- **Time Stop**: Close or adjust at 7-10 DTE on the short leg. Gamma acceleration makes the position unpredictable
- **Adjustment Trigger**: If delta exceeds +/-0.15, the stock has drifted too far. Consider adjustment or exit
- **IV trigger**: If IV drops by > 10 vol points after entry, the long leg loses value disproportionately. Close to limit vega loss

## Adjustments & Rolling

### Short Leg Management
- **Stock stays at strike (ideal)**: At 7-10 DTE on the short leg, buy it back and sell the next 30-45 DTE cycle at the same strike. This "rolls" the calendar forward
- **Stock moves away from strike**: The calendar loses value. Options:
  1. Close the entire position (best if move is large)
  2. Roll the short leg to the new ATM strike (converts to a diagonal -- changes the risk profile)
  3. Add a second calendar at the new price level (creates a double calendar -- see double-calendar.md)

### Rolling Forward
- At 7-10 DTE: Buy back the short leg, sell next month's expiry at the same strike
- This creates a new calendar with the original long leg as the anchor
- Can roll 2-3 times before the long leg itself needs management (< 30 DTE)

### Long Leg Management
- When the long leg reaches 30-45 DTE, either close the entire position or roll the long leg out to a new far-dated expiry
- Don't hold the long leg below 21 DTE -- it becomes a wasting asset

## Risk Controls
- **Max loss = debit paid**: This is defined and known at entry
- **Position limit**: Max 3 calendar spreads simultaneously (they're all sensitive to the same risk -- large stock moves)
- **IV monitoring**: Track the spread's vega daily. A sudden vol crush can erase 30-50% of the position's value
- **Event check**: No binary events (earnings, FDA) should fall between the two expiry dates. The near-term IV inflation from the event distorts the calendar
- **Correlation**: Don't run calendars on highly correlated stocks (e.g., AAPL calendar + MSFT calendar + GOOGL calendar). A market-wide move kills all of them
- **Assignment risk**: American-style options -- the short call can be assigned early, especially near ex-dividend. Monitor and close before ex-div if necessary

## Common Traps
- **Chasing the "max profit" pin**: The max profit diagram shows a beautiful tent peak at the strike. In practice, the stock almost never pins exactly there. Set realistic profit targets (25-40% of debit)
- **Ignoring term structure**: If near-term IV is 10 points above far-term IV (inverted structure), you're selling cheap vol and buying expensive vol. The calendar will lose money if term structure normalizes
- **Holding through short leg expiry**: Letting the short leg expire while holding the long leg is NOT a free long option. You've already paid for it with the calendar debit. If the thesis has changed, close both legs
- **Rolling endlessly**: "I'll just keep rolling the short leg" -- each roll has bid-ask slippage cost. After 2-3 rolls, evaluate whether the cumulative slippage has eroded your edge
- **Ignoring the gamma cliff**: As the short leg approaches 7-5 DTE with the stock near the strike, gamma accelerates. A small move can flip the position from profitable to losing in hours. Close early
- **Wrong sizing**: Because the max loss is just the debit paid, traders over-size calendars. Twenty calendars at $3.50 each = $7,000 at risk. That may be more than 2% of your portfolio
- **Confusing calendar with diagonal**: A calendar has the same strike on both legs. A diagonal has different strikes. They have very different risk profiles. Don't accidentally build a diagonal when you want a calendar
