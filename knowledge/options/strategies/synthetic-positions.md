# Strategy: Synthetic Positions (合成头寸)
> Synthetic | Complexity: Intermediate | Max Risk: Undefined | IV Environment: Any

## Overview
Synthetic positions replicate the risk/reward profile of stock ownership using options. A synthetic long stock combines a long ATM call with a short ATM put at the same strike and expiry, perfectly mimicking 100 shares of stock. A synthetic short stock does the reverse. These structures provide capital efficiency (lower margin than buying shares), short-selling flexibility, and precise P&L equivalence to the underlying.

Synthetics are the foundation of options theory -- put-call parity made tradeable. Professional desks use them for arbitrage, delta hedging, and capital-efficient directional plays. For retail traders, they're useful when you want stock exposure without full capital commitment.

## Structure

### Synthetic Long Stock (合成做多)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Buy | ATM Call | At or near current price | Same expiry |
| 2 | Sell | ATM Put | Same strike as call | Same expiry |

### Synthetic Short Stock (合成做空)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1 | Buy | ATM Put | At or near current price | Same expiry |
| 2 | Sell | ATM Call | Same strike as call | Same expiry |

**Net Cost**: Approximately zero if ATM strikes are used and put-call parity holds. Small debit or credit depending on dividends, interest rates, and skew.

## When to Use
- You want stock-equivalent exposure with reduced capital outlay (margin vs. full share price)
- Short selling is difficult or expensive (hard-to-borrow stocks) -- synthetic short bypasses borrow fees
- Portfolio margining allows significant capital savings vs. holding shares
- Arbitrage: if the synthetic is cheaper than actual shares, buy synthetic + sell shares (or vice versa)
- Tax or regulatory reasons where options have different treatment than shares
- High conviction directional play where you want 1:1 delta exposure

## When NOT to Use
- You're not comfortable with undefined risk on the short option leg
- The stock pays significant dividends (synthetic long misses dividends; short put has early assignment risk around ex-div)
- Options liquidity is poor -- ATM bid-ask spreads are wide
- You need to hold the position for years without rolling (shares don't expire; options do)
- Margin requirements exceed what you'd pay for shares anyway (depends on broker/account type)

## Entry Criteria
- **IV Environment**: Any. Synthetics are delta plays, not vol plays. But check that put-call parity holds (no unusual skew at ATM)
- **Direction**: Strong directional conviction -- this is a 1:1 stock substitute
- **Time Frame**: 30-180 DTE. Longer = more stock-like but higher extrinsic value to manage
- **Liquidity**: ATM options must have OI > 1,000 and bid-ask < 3% of mid price. Tight markets are essential
- **Dividend check**: Calculate upcoming dividends. Synthetic long misses dividends; factor this into the cost

## Strike & Expiry Selection

### Strike Selection
- **Ideal**: Use the strike closest to current stock price. True synthetics use the exact ATM strike
- **Delta check**: Combined delta should be very close to +1.00 (long) or -1.00 (short). If delta of long call is +0.52 and short put is +0.48, net delta = +1.00
- **Split strikes**: In rare cases where ATM doesn't exist cleanly, you can split: e.g., buy $150 call + sell $152.50 put. This creates a "risk reversal" variant, not a pure synthetic
- **Avoid deep OTM/ITM**: That's a risk reversal, not a synthetic. Keep both legs ATM

### Expiry Selection
- **60-90 DTE**: Good balance of stock-like behavior and manageable roll frequency
- **LEAPS (180-365 DTE)**: Best stock replacement. Roll annually. Higher initial extrinsic but less management
- **< 30 DTE**: Avoid. Gamma risk too high, and you'll be rolling constantly
- **Quarterly expiries**: Often more liquid and better pricing than monthly

## Position Sizing
- **Size as if buying shares**: If you'd buy 200 shares of a $150 stock ($30,000 exposure), use 2 contracts
- **Margin requirement**: Typically 20% of underlying value for portfolio margin accounts, or full put assignment value for Reg-T accounts
- **Capital efficiency**: With portfolio margin, a $30,000 synthetic might require only $6,000-$8,000 margin vs. $30,000 for shares
- **Max exposure**: Single synthetic position should not exceed what you'd allocate to the stock position in your portfolio (e.g., 15% for DNA-A)

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | +1.00 (long) / -1.00 (short) | Stays near +/- 1.00 unless stock moves significantly away from strike |
| Gamma | Near zero (ATM call gamma offsets ATM put gamma) | Increases as expiry approaches if stock near strike |
| Theta | Near zero (long call theta offset by short put theta) | Becomes negative as expiry nears (long call decays faster than short put credit) |
| Vega  | Near zero (long call vega offset by short put vega) | Stays near zero -- this is a delta play, not a vol play |

## Exit Protocol
- **Profit Target**: Same as your stock target. If you'd sell shares at $180, close the synthetic at $180
- **Stop Loss**: Same as your stock stop. If you'd stop out at $135, close the synthetic at $135. Use the underlying price, not the option prices, for decision-making
- **Time Stop**: Roll at 21-30 DTE to maintain stock-like characteristics. Don't let it get into gamma territory
- **Adjustment Trigger**: If margin requirements spike (volatility increase), evaluate whether to reduce size or switch to actual shares

## Adjustments & Rolling
- **Rolling to next cycle**: At 21-30 DTE, close current position and reopen at same ATM strike for the next cycle. Cost: usually small (mainly bid-ask slippage)
- **Rolling with strike adjustment**: If stock has moved significantly, roll to the new ATM strike. This crystalizes the P&L on the old position
- **Convert to stock**: If you want to hold long-term and rolling costs add up, exercise the long call (or take assignment on the short put) to convert to actual shares
- **Add protection**: If conviction drops, buy a put (long synthetic) or buy a call (short synthetic) to cap risk. This converts to a risk reversal or collar
- **Dividend adjustment**: Before ex-dividend dates, consider closing the synthetic long and buying shares to capture the dividend, then re-establishing after

## Risk Controls
- **Assignment risk on short leg**: American-style options can be assigned early, especially near ex-dividend dates. Monitor daily
- **Margin monitoring**: Margin requirements change with IV. A volatility spike can cause a margin call even if the stock hasn't moved
- **Max portfolio allocation**: Treat synthetic exposure identically to stock exposure for all risk limits
- **Gap risk**: Identical to stock gap risk. A 10% overnight gap = 10% loss on the synthetic
- **Liquidity for exit**: Ensure you can close both legs simultaneously. Use limit orders for the pair, not market orders on individual legs
- **Correlation limits**: Combined synthetic + actual stock exposure in same name should not exceed max allocation

## Common Traps
- **Forgetting dividends**: Synthetic long does NOT receive dividends. If a stock pays $2/share quarterly, your synthetic is $2/contract worse off per quarter vs. owning shares. Factor this in
- **Early assignment surprise**: Around ex-dividend dates, your short put (long synthetic) or short call (short synthetic) may get assigned early. Have a plan and capital ready
- **Ignoring roll costs**: Each roll costs bid-ask spread x 4 legs (close 2, open 2). Over a year with quarterly rolls, this adds up. Compare total cost to just buying shares
- **Margin complacency**: "I only used $8K of margin for $30K of exposure" -- yes, but you still lose $3K if the stock drops 10%. The leverage amplifies losses relative to capital deployed
- **Using synthetics on illiquid options**: If the ATM bid-ask is $0.50 wide, your synthetic costs $1.00 in slippage to enter and $1.00 to exit. That's $200/contract round trip. Just buy the stock
- **Not monitoring after hours**: Your synthetic has the same overnight risk as stock, but you can't trade options after hours. Major after-hours moves leave you locked in until market open
