# Options Strategy Playbook Index (期权策略手册索引)
> 23 Strategies | Quick Lookup Tables | Agent Decision Support

## How to Use This Index

This index serves as the **routing layer** for the Options Strategy Agent. Given a user's directional view, IV environment, risk tolerance, and complexity preference, use the lookup tables below to narrow down to 2-3 candidate strategies, then read the full playbook for implementation details.

**File naming convention**: All playbooks are in this directory (`knowledge/options/strategies/`).

---

## Master Strategy List

| # | File | Strategy (中文) | Category | Complexity | Max Risk | IV Environment |
|---|------|----------------|----------|------------|----------|----------------|
| 1 | `long-call.md` | Long Call (买入看涨) | Directional/Bullish | Beginner | Defined | Low |
| 2 | `long-put.md` | Long Put (买入看跌) | Directional/Bearish + Hedge | Beginner | Defined | Low |
| 3 | `bull-call-spread.md` | Bull Call Spread (牛市看涨价差) | Vertical/Debit | Intermediate | Defined | Any |
| 4 | `bear-put-spread.md` | Bear Put Spread (熊市看跌价差) | Vertical/Debit | Intermediate | Defined | Any |
| 5 | `bull-put-spread.md` | Bull Put Spread (牛市看跌价差) | Vertical/Credit | Intermediate | Defined | High |
| 6 | `bear-call-spread.md` | Bear Call Spread (熊市看涨价差) | Vertical/Credit | Intermediate | Defined | High |
| 7 | `collar.md` | Collar (领口策略) | Hedge/Protection | Beginner | Defined | Low |
| 8 | `straddle.md` | Straddle (跨式组合) | Volatility | Intermediate | Varies | Varies |
| 9 | `strangle.md` | Strangle (宽跨式组合) | Volatility | Intermediate | Varies | Varies |
| 10 | `iron-condor.md` | Iron Condor (铁鹰式) | Neutral/Premium Selling | Intermediate | Defined | High |
| 11 | `iron-butterfly.md` | Iron Butterfly (铁蝶式) | Neutral/Precision Selling | Advanced | Defined | High |
| 12 | `butterfly.md` | Butterfly (蝶式 + Broken Wing) | Neutral/Directional | Advanced | Defined | Any |
| 13 | `risk-reversal.md` | Risk Reversal (风险反转) | Synthetic/Directional | Intermediate | Undefined (one side) | Any |
| 14 | `synthetic-positions.md` | Synthetic Long/Short (合成头寸) | Synthetic | Intermediate | Undefined | Any |
| 15 | `pmcc.md` | PMCC (穷人版备兑) | Diagonal/Income | Advanced | Defined | Low (long leg) |
| 16 | `backspread.md` | Backspread (反比率价差) | Ratio/Tail Protection | Advanced | Defined (if credit) | Low |
| 17 | `front-ratio-spread.md` | Front Ratio Spread (正比率价差) | Ratio/Income | Advanced | Partially Undefined | High |
| 18 | `jade-lizard.md` | Jade Lizard (翡翠蜥蜴) | Combination/Premium | Advanced | Defined up / Undefined down | High |
| 19 | `wheel-strategy.md` | Wheel (飞轮策略) | Composite/CSP+CC Cycle | Intermediate | Undefined downside | High |
| 20 | `repair-strategy.md` | Repair Strategy (修复策略) | Repair/Recovery | Advanced | Defined | Any |
| 21 | `calendar-spread.md` | Calendar Spread (时间价差) | Time Spread | Intermediate | Defined | Any (benefits from IV rise) |
| 22 | `double-calendar.md` | Double Calendar (双时间价差) | Dual Time Spread | Advanced | Defined | Any (benefits from IV rise) |
| 23 | `gamma-scalping.md` | Gamma Scalping (Gamma 刷单) | Volatility Trading | Advanced | Defined (if hedged) | Low |

---

## Quick Lookup: By Direction

### Bullish (看涨)

| Strategy | Conviction | IV | Risk Type | Capital Efficiency | Notes |
|----------|-----------|-----|-----------|-------------------|-------|
| Long Call | High | Low | Defined | High (max leverage) | Pure directional bet |
| Bull Call Spread | Medium-High | Any | Defined | High | Cost-controlled upside |
| Bull Put Spread | Medium | High | Defined | Medium | Credit strategy, sell IV |
| Risk Reversal | High | Any | Undefined (put side) | Very High (zero-cost) | Synthetic directional, must accept assignment |
| Synthetic Long | High | Any | Undefined | High (capital efficient) | Stock substitute via options |
| PMCC | High (long-term) | Low entry | Defined | Very High | Capital-efficient covered call |
| Wheel (CSP phase) | Medium-High | High | Undefined (put side) | Medium | Income + potential stock acquisition |
| Call Backspread | High (magnitude) | Low | Defined (if credit) | Medium | Explosive upside, pay attention to danger zone |
| Collar | Existing position | Low | Defined | N/A (hedging) | Protective structure for holdings |
| Repair Strategy | Existing loss | Any | Defined | N/A (recovery) | Break-even recovery for underwater shares |

### Bearish (看跌)

| Strategy | Conviction | IV | Risk Type | Capital Efficiency | Notes |
|----------|-----------|-----|-----------|-------------------|-------|
| Long Put | High | Low | Defined | High | Pure directional or hedge |
| Bear Put Spread | Medium-High | Any | Defined | High | Cost-controlled downside bet |
| Bear Call Spread | Medium | High | Defined | Medium | Credit strategy, sell IV |
| Synthetic Short | High | Any | Undefined | High | Short stock substitute |
| Put Backspread | High (crash) | Low | Defined (if credit) | Medium | Tail-risk protection |
| Risk Reversal (bearish) | High | Any | Undefined (call side) | Very High (zero-cost) | Bearish synthetic |

### Neutral / Range-Bound (中性/区间震荡)

| Strategy | Expected Range | IV | Risk Type | Precision | Notes |
|----------|---------------|-----|-----------|-----------|-------|
| Iron Condor | Wide | High | Defined | Low | Broad range premium selling |
| Iron Butterfly | Narrow (pin) | High | Defined | High | Maximum premium at one point |
| Butterfly | Pin point | Any | Defined | Very High | Low-cost precision play |
| Calendar Spread | Near strike | Any | Defined | Medium | Time decay + vega positive |
| Double Calendar | Between strikes | Any | Defined | Medium | Wider range than single calendar |
| Front Ratio Spread | Near short strike | High | Partially Undefined | Medium | Income, tent-shaped payoff |
| Jade Lizard | Stay above put | High | Mixed | Low-Medium | No upside risk if credit > width |
| Short Straddle | Pin at strike | High | Undefined | High | Maximum theta, maximum risk |
| Short Strangle | Wide range | High | Undefined | Low | Classic premium selling |

### Volatility Play (波动率交易 — 不确定方向)

| Strategy | Vol View | IV at Entry | Risk Type | Monitoring | Notes |
|----------|----------|-------------|-----------|------------|-------|
| Long Straddle | RV > IV | Low | Defined | Moderate | Profit from any big move |
| Long Strangle | RV > IV | Low | Defined | Moderate | Cheaper than straddle, needs bigger move |
| Gamma Scalping | RV > IV (active) | Low | Defined (hedged) | Intensive (daily+) | Delta-neutral, scalp gamma |
| Backspread | Tail risk | Low | Defined (credit) | Low-Moderate | Asymmetric tail payoff |
| Calendar Spread | IV rise expected | Low-Moderate | Defined | Moderate | Vega-positive time play |
| Double Calendar | IV rise expected | Low-Moderate | Defined | Moderate | Broader vega-positive play |

---

## Quick Lookup: By IV Environment

### Low IV (IV Rank < 30%) — Buy Premium / Buy Vol

| Strategy | Direction | Notes |
|----------|-----------|-------|
| Long Call | Bullish | Cheap premium, max leverage |
| Long Put | Bearish/Hedge | Cheap insurance |
| Collar | Bullish (hedge) | Cheap puts for protection |
| Long Straddle | Neutral (expect big move) | Cheap gamma |
| Long Strangle | Neutral (expect big move) | Even cheaper entry |
| Gamma Scalping | Neutral (RV > IV) | Professional vol trading |
| Backspread | Directional + tail | Cheap OTM options for asymmetry |
| PMCC (long leg) | Bullish long-term | Cheap LEAPS entry |
| Calendar/Double Calendar | Neutral | Expect IV to rise from low levels |

### High IV (IV Rank > 50%) — Sell Premium / Sell Vol

| Strategy | Direction | Notes |
|----------|-----------|-------|
| Bull Put Spread | Bullish | Rich credit from high IV |
| Bear Call Spread | Bearish | Rich credit from high IV |
| Iron Condor | Neutral | Classic high-IV play |
| Iron Butterfly | Neutral (pin) | Maximum premium collection |
| Short Straddle | Neutral (pin) | Aggressive premium selling |
| Short Strangle | Neutral (range) | Classic premium selling |
| Front Ratio Spread | Mild directional | Rich OTM premium to sell |
| Jade Lizard | Neutral-bullish | No upside risk premium selling |
| Wheel (CSP) | Bullish | High premium for target entry |
| PMCC (short leg) | Bullish | Sell expensive near-term vol |

### Any IV — Structure-Dependent

| Strategy | Notes |
|----------|-------|
| Bull Call Spread | Works in any IV; adjusts risk/reward |
| Bear Put Spread | Works in any IV; adjusts risk/reward |
| Butterfly | Structure flexibility for any environment |
| Risk Reversal | Skew matters more than absolute IV |
| Synthetic Positions | Delta play, not vol play |
| Repair Strategy | Emergency structure, IV is secondary |
| Calendar Spread | Benefits from IV rise but works in any |

---

## Quick Lookup: By Complexity

### Beginner (2 strategies)

| # | Strategy | Legs | Key Concept |
|---|----------|------|-------------|
| 1 | Long Call | 1 | Buy upside exposure |
| 2 | Long Put | 1 | Buy downside exposure / hedge |
| 7 | Collar | 3 (shares + 2 options) | Protective structure (simple concept) |

### Intermediate (10 strategies)

| # | Strategy | Legs | Key Concept |
|---|----------|------|-------------|
| 3 | Bull Call Spread | 2 | Cost-controlled bullish debit |
| 4 | Bear Put Spread | 2 | Cost-controlled bearish debit |
| 5 | Bull Put Spread | 2 | Bullish credit / premium selling |
| 6 | Bear Call Spread | 2 | Bearish credit / premium selling |
| 8 | Straddle | 2 | Volatility bet (direction-agnostic) |
| 9 | Strangle | 2 | Wider volatility bet |
| 10 | Iron Condor | 4 | Neutral range premium selling |
| 13 | Risk Reversal | 2 | Zero-cost directional synthetic |
| 14 | Synthetic Positions | 2 | Stock replacement via options |
| 19 | Wheel | 1-2 per phase | Systematic CSP/CC income cycle |
| 21 | Calendar Spread | 2 | Time decay differential |

### Advanced (10 strategies)

| # | Strategy | Legs | Key Concept |
|---|----------|------|-------------|
| 11 | Iron Butterfly | 4 | Precision premium selling |
| 12 | Butterfly / BWB | 3-4 | Low-cost precision positioning |
| 15 | PMCC | 2 (multi-cycle) | Capital-efficient covered call |
| 16 | Backspread | 3+ | Asymmetric tail risk play |
| 17 | Front Ratio Spread | 3 | Income via ratio selling (undefined risk) |
| 18 | Jade Lizard | 3 | No-upside-risk premium combination |
| 20 | Repair Strategy | 3 (with shares) | Cost-basis recovery structure |
| 22 | Double Calendar | 4 | Dual time decay + vega play |
| 23 | Gamma Scalping | 2 + dynamic hedges | Active volatility trading |

---

## Quick Lookup: By Risk Type

### Defined Risk (known max loss at entry)

| # | Strategy | Max Loss Formula |
|---|----------|-----------------|
| 1 | Long Call | Premium paid |
| 2 | Long Put | Premium paid |
| 3 | Bull Call Spread | Debit paid |
| 4 | Bear Put Spread | Debit paid |
| 5 | Bull Put Spread | (Width - credit) x 100 |
| 6 | Bear Call Spread | (Width - credit) x 100 |
| 7 | Collar | Stock purchase - put strike + net premium |
| 10 | Iron Condor | (Wider wing width - net credit) x 100 |
| 11 | Iron Butterfly | (Wing width - net credit) x 100 |
| 12 | Butterfly | Debit paid (standard); adjusted for BWB |
| 15 | PMCC | LEAPS debit - cumulative short call premiums |
| 16 | Backspread (credit) | Width x 100 - credit (danger zone); zero opposite side |
| 20 | Repair Strategy | Same as holding shares (repair adds no net risk) |
| 21 | Calendar Spread | Debit paid |
| 22 | Double Calendar | Total debit paid |
| 23 | Gamma Scalping | Straddle debit (if hedged properly) |

### Undefined Risk (potential for large/unlimited loss)

| # | Strategy | Risk Side | Max Loss |
|---|----------|-----------|----------|
| 8 | Short Straddle | Both | Unlimited |
| 9 | Short Strangle | Both | Unlimited |
| 13 | Risk Reversal | One side (put for bullish) | Stock to zero minus strike |
| 14 | Synthetic Positions | One side | Unlimited (like stock) |
| 17 | Front Ratio Spread | One side (extra naked leg) | Unlimited past breakeven |
| 18 | Jade Lizard | Downside (put side) | Put strike to zero |
| 19 | Wheel | Downside (assignment) | Stock to zero |

---

## Decision Tree (Quick Reference)

```
Start: What is your directional view?
│
├─ BULLISH
│  ├─ IV Low?  → Long Call / Bull Call Spread / PMCC / Backspread
│  ├─ IV High? → Bull Put Spread / CSP (Wheel) / Jade Lizard
│  └─ Any IV?  → Risk Reversal / Synthetic Long
│
├─ BEARISH
│  ├─ IV Low?  → Long Put / Bear Put Spread / Put Backspread
│  ├─ IV High? → Bear Call Spread
│  └─ Any IV?  → Synthetic Short / Risk Reversal (bearish)
│
├─ NEUTRAL
│  ├─ IV High? → Iron Condor / Iron Butterfly / Front Ratio / Jade Lizard / Short Strangle
│  ├─ IV Low?  → Calendar / Double Calendar / Butterfly
│  └─ Pin?     → Butterfly / Iron Butterfly
│
├─ VOL PLAY (direction unknown, big move expected)
│  ├─ IV Low?  → Long Straddle / Long Strangle / Gamma Scalping
│  ├─ IV High? → DON'T (or Reverse Iron Condor with tight risk)
│  └─ Tail?    → Backspread
│
├─ HEDGE (existing position)
│  ├─ IV Low?  → Collar / Protective Put
│  ├─ IV High? → Bear Put Spread (cheaper hedge)
│  └─ Underwater? → Repair Strategy
│
└─ INCOME (systematic)
   ├─ Own shares? → Covered Call → Wheel Phase 2
   ├─ Want shares? → CSP → Wheel Phase 1
   └─ Capital efficient? → PMCC
```

---

## Strategy Comparison Matrix (Top-Level)

| Dimension | Best Strategies | Worst Strategies |
|-----------|----------------|-----------------|
| **Capital efficiency** | PMCC, Risk Reversal, Synthetic, Backspread | Wheel (full cash), Covered Call |
| **Income generation** | Wheel, Iron Condor, PMCC, Front Ratio | Long Call/Put, Backspread |
| **Risk control** | Iron Condor, Butterfly, Spreads, Collar | Naked Short, Synthetic, Risk Reversal |
| **Simplicity** | Long Call/Put, Collar | Gamma Scalping, Double Calendar, Jade Lizard |
| **Tail protection** | Put Backspread, Long Put, Collar | Short Strangle, Naked Put |
| **IV exploitation** | Iron Condor (high IV), Calendar (low IV) | Long Straddle (high IV), Short Strangle (low IV) |
| **Recovery** | Repair Strategy, Wheel (post-assignment) | None (prevention > cure) |

---

## Notes for the Agent

1. **Always check IV Rank first** -- it determines which side of the table (buy vs. sell) you start on
2. **Liquidity is a hard gate** -- if bid-ask > 10% of mid, do not recommend the strategy regardless of how perfect the setup looks
3. **Earnings window rules apply to ALL strategies** -- check T-5 blackout before any recommendation
4. **OPRMS DNA gate for undefined risk** -- never recommend naked puts, CSPs, wheels, or jade lizards on DNA B/C stocks
5. **Complexity should match conviction** -- high conviction = simple structure; low conviction = defined risk
6. **The best strategy is no strategy** -- if conditions aren't right, tell the user to wait
