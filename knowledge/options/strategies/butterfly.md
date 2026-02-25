# Strategy: Butterfly Spread (蝶式价差 — Including Broken Wing)
> 中性+方向 | Complexity: Advanced | Max Risk: Defined | IV Environment: Any

## Overview
A butterfly spread uses three strike prices to create a position that profits when the stock expires near the center strike. It can be constructed with all calls, all puts, or a combination (iron butterfly — see separate playbook). The standard butterfly is a debit trade with very low cost and a high reward-to-risk ratio if the stock pins. The **broken wing butterfly** (BWB) shifts one wing to collect a credit or reduce cost, adding a directional bias. Butterflies are precision instruments for traders who have a specific price target and want asymmetric risk/reward.

## Structure

### Standard Call Butterfly (标准蝶式)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy 1     | Call | Lower (A) | 30-60 DTE |
| 2   | Sell 2    | Call | Center (B) | Same expiry |
| 3   | Buy 1     | Call | Higher (C) | Same expiry |

- A-B = B-C (equal wing width). Net debit trade.
- Max gain = B-A - net debit (at center strike). Max loss = net debit.

### Standard Put Butterfly
- Same structure using puts. Identical P&L profile.

### Broken Wing Butterfly (BWB / 非对称蝶式)
| Leg | Direction | Type | Strike | Expiry |
|-----|-----------|------|--------|--------|
| 1   | Buy 1     | Call | A | 30-60 DTE |
| 2   | Sell 2    | Call | B | Same expiry |
| 3   | Buy 1     | Call | C (wider gap from B than A-B) | Same expiry |

- Unequal wings: C-B > B-A. This skips one or more strikes on the long wing.
- Can be entered for a credit or very small debit.
- Has directional bias (bullish if call BWB, bearish if put BWB).
- Risk is on the wide side; the narrow side is risk-free or positive.

## When to Use

### Standard Butterfly
- You have a specific price target for the stock at expiration
- Want extremely asymmetric risk/reward (risk $1 to make $4-9)
- IV environment doesn't matter much — the trade is cheap enough that IV level is secondary
- Pinning plays near options expiration (高OI行权价附近的钉扎效应)
- "Lottery ticket" with better odds — small debit, large potential payoff

### Broken Wing Butterfly
- Same price target but you want to eliminate risk on one side
- Want a credit or zero-cost entry with a directional bias
- Post-pullback entries where you expect a bounce to a specific level
- Replace a credit spread with better risk/reward at the target price
- "If it goes to X, I make a lot; if it goes past X, I make a little; if it goes against me, I lose nothing or very little"

## When NOT to Use
- No specific price target — butterflies need precision
- High-volatility stocks with ATR > 3% — they won't pin
- Illiquid options — 3-leg executions on wide bid-ask spreads are execution nightmares
- You need the trade to work at any price above/below a level (use a spread instead)
- Very short DTE (< 14 days) unless it's an intentional opex pinning play

## Entry Criteria
- **IV Environment**: Any for standard butterflies. For BWBs, moderate-to-high IV helps collect a credit.
- **Price Target**: Must have a specific target price within the next 30-60 days. This becomes the center strike.
- **Time Frame**: 30-60 DTE for standard. 21-45 DTE for BWBs. 7-14 DTE for opex pinning plays.
- **Liquidity**: OI > 500 on all three strikes. Bid-ask < $0.10. Penny increments preferred.
- **Debit**: Standard butterfly should cost < 25% of the wing width. A $5-wide butterfly should cost < $1.25.

## Strike & Expiry Selection

### Standard Butterfly
- **Center strike (B)**: Your price target. This is where max profit occurs.
- **Wing width**: $5 standard, $10 for higher-priced stocks. Equal on both sides.
- **Example**: Stock at $100, target $110. Buy $105/$110/$115 call butterfly for $1.20. Max gain at $110 = $3.80.
- **Expiry**: 30-45 DTE for target-based plays. 7-14 DTE for gamma-driven pinning plays.

### Broken Wing Butterfly
- **Center strike (B)**: Your price target.
- **Narrow wing** (A-B): Standard width ($5).
- **Wide wing** (B-C): Skip 1-2 strikes ($7.50-$10). This is the risk side.
- **Example (Bullish BWB)**: Stock at $100. Buy $105 call, sell 2× $110 call, buy $120 call. Skip creates credit/low debit. Risk only above $120. No risk below $105.
- **Credit/debit target**: Try to enter for a small credit or zero cost. The free/credit entry is the BWB's superpower.

## Position Sizing

### Standard Butterfly
- Debit is small, so you can size aggressively in contracts (but keep total dollar risk in check).
- Risk per position: 1-2% of portfolio (debit is max loss).
- `Contracts = (Portfolio × 0.01) / (Debit × 100)`
- Example: $500K portfolio, 1% risk = $5,000. Butterfly costs $1.50 → 33 contracts.

### Broken Wing Butterfly
- If entered for a credit: no risk on one side, but max loss on the other = wide wing width - credit.
- Size by the max loss on the risk side: `Contracts = (Portfolio × 0.02) / (Max Loss × 100)`
- If credit entry: you literally cannot lose money on one side. This allows more aggressive sizing on the risk side.

## Greeks Profile
| Greek | At Entry | Over Time |
|-------|----------|-----------|
| Delta | Near zero (standard) / Directional (BWB) | Swings as stock approaches wings |
| Gamma | Mixed — positive away from center, negative at center | Extreme near expiry. At 7 DTE, tiny moves swing P&L wildly. |
| Theta | Small positive (if centered) | Accelerates if stock is near center strike as expiry approaches |
| Vega  | Slightly negative (when centered) | Butterfly benefits slightly from IV contraction at center |

## Exit Protocol
- **Profit Target**: Close at 50-75% of max profit. Do NOT hold to expiry trying to nail the exact pin. At max profit, the butterfly is worth $3.80 from a $1.50 entry — close at $3.00-$3.50.
- **Stop Loss**: Close if the stock moves > 1 wing width away from center. The trade is dead.
- **Time Stop**: For non-pinning trades, close at 14 DTE. For pinning plays, manage actively until expiry.
- **Pin proximity**: If at 7 DTE the stock is within $2 of the center strike, hold. Otherwise, close.

## Adjustments & Rolling

### Standard Butterfly
- **Recenter**: If the stock moves early, close and reopen centered at the new price (if thesis still valid).
- **Convert to condor**: If the stock is between your center and wing, sell an additional spread to widen the profit zone. You now have a condor instead.
- **Add a directional leg**: If the stock is trending toward your center, add a long option on the same side to increase delta exposure while maintaining the butterfly payoff.

### Broken Wing Butterfly
- **Roll the wide wing in**: If the stock is moving toward your risk side, buy back the far wing and sell a closer one. This reduces max loss but narrows the profit zone.
- **Close the risk side early**: If the BWB was entered for credit and the stock moves to the safe side, you've already won. Close if the credit is locked in.
- **Never add to the risk side**: If the stock blows through the wide wing, the trade is lost. Accept it.

## Risk Controls
- Standard butterfly: Max 2% per position, but consider that the probability of max profit is low (10-20%).
- BWB: Max 2% per position on the risk side. Zero on the safe side (if credit entry).
- Max total butterfly exposure: 10% of portfolio
- Execution risk: Always use limit orders, ideally at the mid price. Market orders on 3-leg trades will cost you dearly.
- Pin probability: Realistically, stocks pin within $2 of target about 25-35% of the time. Size accordingly — this is not a high-probability trade.
- Opex-week gamma: In the last week, butterflies can swing 50-100% in value per day. Only experienced traders should hold.

## Common Traps
- **Falling in love with the risk/reward ratio**: "Risk $1 to make $5" sounds amazing, but the probability of the stock pinning at the exact center strike is low. Expected value, not max payoff, is what matters.
- **Execution slippage on 3 legs**: If each leg slips $0.03, that's $0.09 on a $1.50 butterfly — 6% of your cost. Only trade liquid names.
- **Holding to expiry and getting assigned**: If the stock closes between the center and a wing, you may be assigned on the short calls. Close before the last day to avoid this.
- **BWB: Ignoring the risk side**: "I got in for free so there's no risk" — wrong. The wide wing side can still lose the full wing width minus credit. Size by that number.
- **Using butterflies as a primary strategy**: Butterflies are supplementary. They're precision bets, not portfolio workhorses. Keep them as 5-10% of your options allocation.
- **Not having a real price target**: "I think it'll be around here" is not a price target. Butterflies require conviction on a specific level. Use technical analysis (support, resistance, Fib levels, volume profile) to justify the center strike.
