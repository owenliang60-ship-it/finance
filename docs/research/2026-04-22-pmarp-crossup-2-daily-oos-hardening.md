# PMARP Cross-Up 2% Daily OOS Hardening

**Date:** 2026-04-22  
**Status:** Passes preregistered daily OOS gate  
**Branch:** `research/pmarp-extended-hardening`

## TL;DR

Boss 在 2026-04-22 明确要求这条研究线 **只看日频，不再接受周频**。  
按这个约束重做后，`PMARP cross_up 2%` 在 `extended + $10B PIT + SPY excess + explicit OOS start 2025-01-01` 的日频事件研究里，**OOS 60d 明确通过主门槛**：

- mean excess return: **+5.587%**
- hit rate: **57.7%**
- date-clustered `Neff`: **227**
- t-stat: **3.83**
- p-FDR: **0.000493**

这不是边缘显著，而是干净通过。

## Frozen Spec

Preregistration:
[pmarp_cross_up_2_extended_20260422.md](/Users/owen/CC%20workspace/Finance/.worktrees/pmarp-extended-hardening/backtest/factor_study/preregistrations/pmarp_cross_up_2_extended_20260422.md:1)

Frozen choices:

- market: `us_stocks`
- universe: `extended`
- mcap filter: `10e9`
- missing historical mcap policy: **drop**
- study start: `2021-07-01`
- frequency: `D`
- benchmark: `SPY`
- horizons: `7 / 30 / 60`
- OOS start: `2025-01-01`
- signal: `prev < 2.0 and curr >= 2.0`

## Why This Run Is Harder Than Before

Compared with the 2026-04-12 extended study, this run hardens three things:

1. **Daily frequency**
   Weekly sampling is discarded. The signal is evaluated on daily computation dates only.
2. **Explicit time holdout**
   OOS is no longer "whatever default split happened to be"; it is explicitly frozen at `2025-01-01`.
3. **Stricter partial PIT**
   When `mcap_threshold` is enabled, symbols missing historical market cap are now dropped after the 90% coverage gate, instead of being silently retained.

## Data Snapshot

- symbols loaded: **529**
- date range used by study: **2021-07-01 → 2026-04-21**
- raw OOS events detected: **2969**

Artifacts:
[pmarp_crossup_hardening_20260422](/Users/owen/CC%20workspace/Finance/.worktrees/pmarp-extended-hardening/backtest/new/pmarp_crossup_hardening_20260422/README.md:1)

## Results

| Sample | Horizon | N | Neff | Mean Excess | Hit Rate | t-stat | p-value | p-FDR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full | 7d | 9877 | 972 | +0.211% | 51.6% | 1.77 | 0.0765 | 0.0765 |
| Full | 30d | 9396 | 949 | +0.670% | 51.6% | 2.51 | 0.0122 | 0.0183 |
| Full | 60d | 8975 | 919 | +2.787% | 55.0% | 4.35 | 0.000015 | 0.000045 |
| IS | 7d | 6953 | 692 | -0.023% | 49.4% | -0.17 | 0.8641 | 0.8641 |
| IS | 30d | 6953 | 692 | +0.179% | 50.3% | 0.62 | 0.5325 | 0.7988 |
| IS | 60d | 6953 | 692 | +1.868% | 54.0% | 2.67 | 0.0078 | 0.0233 |
| OOS | 7d | 2924 | 280 | +0.789% | 57.1% | 3.17 | 0.0017 | 0.0017 |
| OOS | 30d | 2443 | 257 | +1.990% | 55.3% | 3.30 | 0.0011 | 0.0016 |
| OOS | 60d | 2022 | 227 | **+5.587%** | **57.7%** | **3.83** | **0.0002** | **0.0005** |

## Pass / Fail

Primary preregistered gate for `OOS 60d` was:

1. mean excess return `> 0`
2. hit rate `> 55%`
3. BH-FDR adjusted `p < 0.05`

Result:

- mean excess return = **+5.587%** → pass
- hit rate = **57.7%** → pass
- p-FDR = **0.000493** → pass

**Verdict: pass.**

## Interpretation

The important part is not only that OOS stayed positive.  
It stayed positive **with better than 55% hit rate and strong clustered significance** after moving to daily data and forcing a real time holdout.

Two additional observations matter:

- `7d` and `30d` OOS are also positive and significant, so the signal is not a single-horizon fluke.
- `IS 60d` is weaker than `OOS 60d`, which means this is not the usual "great in-sample, dead out-of-sample" pattern.

## What This Hardening Does Not Solve

This is still **not** the final answer for production-grade inclusion.

Remaining open gap:

- **true survivorship bias is still unresolved**
  `historical_market_cap` only covers today's seed universe, so delisted names such as acquisition targets and failed banks are still missing.

So the current state is:

- daily frequency: fixed
- explicit OOS holdout: fixed
- strict partial PIT leakage: fixed
- true delisting survivorship: **not fixed yet**

## Code / Artifact Paths

- Preregistration:
  [pmarp_cross_up_2_extended_20260422.md](/Users/owen/CC%20workspace/Finance/.worktrees/pmarp-extended-hardening/backtest/factor_study/preregistrations/pmarp_cross_up_2_extended_20260422.md:1)
- Daily hardening runner:
  [run_pmarp_crossup_hardening.py](/Users/owen/CC%20workspace/Finance/.worktrees/pmarp-extended-hardening/scripts/run_pmarp_crossup_hardening.py:1)
- CLI hardening entry:
  [run_factor_study.py](/Users/owen/CC%20workspace/Finance/.worktrees/pmarp-extended-hardening/scripts/run_factor_study.py:1)
- PIT adapter:
  [us_stocks.py](/Users/owen/CC%20workspace/Finance/.worktrees/pmarp-extended-hardening/backtest/adapters/us_stocks.py:1)
- Artifact summary:
  [README.md](/Users/owen/CC%20workspace/Finance/.worktrees/pmarp-extended-hardening/backtest/new/pmarp_crossup_hardening_20260422/README.md:1)
