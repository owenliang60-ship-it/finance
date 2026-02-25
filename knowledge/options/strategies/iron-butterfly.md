# Strategy: Iron Butterfly (铁蝶式组合)
> 卖方/精准定位 | Complexity: Advanced | Max Risk: Defined | IV Environment: High

## Overview
An iron butterfly is a special case of the iron condor where the short call and short put share the same ATM strike. It combines a short straddle with protective wings, creating a defined-risk structure that collects maximum premium when the stock expires exactly at the center strike. It offers higher credit than an iron condor but a narrower profit zone — you're making a precise bet that the stock will be "right here" at expiration. Think of it as a high-conviction, high-reward pinning play.

## Structure
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy       | Put  | Lower (OTM wing) | 30-45 DTE |
| 2   | Sell      | Put  | ATM (center) | Same expiry |
| 3   | Sell      | Call | ATM (same center strike) | Same expiry |
| 4   | Buy       | Call | Higher (OTM wing) | Same expiry |

- Net credit trade (4 legs). Center strike = ATM.
- Max gain = total credit received (stock pins at center strike).
- Max loss = wing width - total credit (one side only).
- Breakevens: center - credit (lower) and center + credit (upper).

## When to Use
- Strong conviction that the stock will pin near the current price
- IV rank > 70% — you're selling a lot of premium and IV contraction helps (极高IV环境下的精准武器)
- Before known pinning events (options expiration on high OI stocks, index rebalances)
- Stock has been consolidating in a tight range with decreasing volume
- Want higher credit than an iron condor, willing to accept a narrower profit zone

## When NOT to Use
- Expecting any significant directional move
- IV rank < 50% — the credit won't justify the narrow profit zone
- Earnings, FDA, or any binary event within the holding period
- Stock has high realized volatility (ATR > 3% daily)
- You're not prepared for active management — butterflies need attention

## Entry Criteria
- **IV Environment**: IV rank > 70%. The credit needs to be substantial because the profit zone is narrow.
- **Direction**: Pinpoint neutral. You need conviction that the stock stays at the current level.
- **Time Frame**: 30-45 DTE. Can also use 21 DTE for opex pinning plays.
- **Liquidity**: OI > 2000 on the ATM strike. OI > 500 on wings. Tight bid-ask critical on 4 legs.
- **Credit target**: Collect at least 40-50% of the wing width. $10-wide wings should net $4.00-$5.00+ in credit.
- **Pinning analysis**: Check open interest at the ATM strike — high OI increases pinning probability.

## Strike & Expiry Selection
- **Center strike**: The ATM strike closest to the current stock price. This is the pin point.
- **Wing width**: $5-$10 standard. Wider wings = higher credit but higher max loss.
- **Symmetric wings**: Both wings should be the same width for a standard iron butterfly. Unequal wings create a "broken wing" variant (see butterfly.md for those).
- **Example**: Stock at $150. Sell $150 put + $150 call. Buy $140 put + $160 call. Wings are $10 wide.
- **Expiry**: 30-45 DTE for standard income plays. 14-21 DTE for opex pinning plays.

## Position Sizing
- Max loss = wing width - credit. Size conservatively due to narrow profit zone.
- `Butterflies = (Portfolio × 0.015) / (Max Loss per butterfly)`
- Example: $10-wide wings, $4.50 credit. Max loss = $5.50 ($550). $500K portfolio, 1.5% = $7,500. Trade 13 butterflies.
- Keep total iron butterfly exposure < 8% of portfolio — this is a precision trade, not a core strategy.

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (centered) | Moves quickly as stock drifts from center |
| Gamma | Large negative (biggest risk) | Very high gamma exposure near the center strike — small moves create large delta |
| Theta | Large positive (primary edge) | Maximum theta at center. More theta than iron condor per dollar of risk. |
| Vega  | Large negative | IV contraction is highly profitable; IV expansion is painful |

## Exit Protocol
- **Profit Target**: Close at 25-30% of max credit. This is more conservative than iron condors because the profit zone is narrow and gamma is high. Collected $4.50 → close at $3.15-$3.40 remaining (buy back for $1.10-$1.35 profit).
- **Stop Loss**: Close if the butterfly value exceeds the credit received (you're underwater). Or if the stock moves > 1.5 standard deviations from the center.
- **Time Stop**: Close at 14 DTE unless the stock is pinned perfectly. Gamma risk in the last 2 weeks is extreme for iron butterflies.
- **Delta monitor**: If net delta exceeds +/-0.25 per contract, adjust or close immediately. Iron butterflies drift faster than iron condors.

## Adjustments & Rolling
- **Roll the center**: If the stock moves significantly, close the butterfly and re-center it at the new stock price. Only for a net credit or small debit.
- **Convert to iron condor**: If the stock is drifting, widen the short strikes by rolling the tested side away from ATM. This reduces credit but widens the profit zone.
- **Close the winning wing early**: If one side is completely OTM and worth $0.05, buy it back to reduce leg risk.
- **Add directional hedge**: If the stock is trending away from center, add a small directional position (long call or long put) to flatten delta.
- **Don't roll more than once**: If the stock won't pin, the thesis is wrong. Close the trade.

## Risk Controls
- Max loss per position: 1.5% of portfolio (lower than iron condors due to narrow zone)
- Max aggregate butterfly exposure: 8% of portfolio
- Gamma monitoring: Check gamma daily. Iron butterflies have 2-3x the gamma of iron condors.
- Sector concentration: Max 1 iron butterfly per sector
- Time limit: Never hold past 14 DTE unless perfectly pinned and you're comfortable with the gamma
- Event filter: No iron butterflies within 2 weeks of earnings
- Margin awareness: Iron butterflies have higher margin requirements per unit of credit than iron condors

## Common Traps
- **Treating it like a "better" iron condor**: It's not. It's a different trade with different risk characteristics. The narrow profit zone means you'll be adjusting or losing more often.
- **Holding to expiry chasing the max profit pin**: Max profit requires the stock to close at exactly the center strike. This almost never happens. Take profits at 25-30%.
- **Ignoring gamma risk in the last 2 weeks**: At 7 DTE, a $1 move in the stock can swing the butterfly value by $2-3. This is where amateurs get destroyed.
- **Opening on low IV**: A $10-wide iron butterfly that only collects $3.00 in credit has $7.00 max loss. That's terrible risk/reward. Only enter when credit is > 40% of width.
- **Not re-centering when needed**: If the stock moves $5 away from center in the first week, the trade is already in trouble. Re-center early or close — don't wait for a miracle mean reversion.
- **Using iron butterflies on volatile stocks**: This strategy works on stocks that pin. High-beta, momentum-driven stocks do not pin. Focus on large-cap, low-beta names with high open interest at the ATM strike.
- **Forgetting the 4-leg execution cost**: Four legs means four bid-ask spreads. On illiquid options, slippage alone can eat 10-20% of your credit. Only trade on liquid names with penny-wide markets.
