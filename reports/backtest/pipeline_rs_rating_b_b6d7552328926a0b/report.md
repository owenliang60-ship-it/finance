# Backtest Pipeline Report — pipeline_rs_rating_b

## Summary
- Benchmark: `SPY`
- Rebalance: `weekly`
- Factors: RS_Rating_B
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.1407, 'pass': True}`
- oos_sharpe_positive: `{'value': 0.5033, 'pass': True}`
- oos_ic_positive: `{'value': 0.001401088172451719, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 3.5771144278606966, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 25.3213, 'threshold': 12.0, 'pass': False}`

## Warnings
- 2021-02-01: no historical market cap coverage yet, moving effective_start forward
- 2021-02-08: no historical market cap coverage yet, moving effective_start forward
- 2021-02-16: no historical market cap coverage yet, moving effective_start forward
- 2021-02-23: no historical market cap coverage yet, moving effective_start forward
- 2021-03-02: no historical market cap coverage yet, moving effective_start forward
- 2021-03-09: no historical market cap coverage yet, moving effective_start forward
- 2021-03-16: no historical market cap coverage yet, moving effective_start forward
- 2021-03-23: no historical market cap coverage yet, moving effective_start forward
- 2021-03-30: no historical market cap coverage yet, moving effective_start forward
- 2021-04-07: no historical market cap coverage yet, moving effective_start forward

## Strategy Metrics
### IS
- is.cagr: `0.021815`
- is.annual_volatility: `0.155047`
- is.sharpe_ratio: `0.1407`
- is.max_drawdown: `-0.301882`
- is.annual_turnover: `22.346`
- is.excess_cagr: `-0.078114`
- is.ir: `-0.2921`
### OOS
- oos.cagr: `0.092701`
- oos.annual_volatility: `0.18417`
- oos.sharpe_ratio: `0.5033`
- oos.max_drawdown: `-0.197846`
- oos.annual_turnover: `25.3213`
- oos.excess_cagr: `-0.061254`
- oos.ir: `-0.162`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.0024931945049035597`
- is.combo.ic_tstat: `-0.19761521959547773`
- is.combo.top_bottom_spread: `-0.0005677951386180536`
- is.combo.top_decile_excess_return: `-0.0018015871194711202`
- is.combo.ic_decay: `{'5': -0.0024931945049035597, '10': -0.010808254415578929, '21': -0.006447622256407133}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.001401088172451719`
- oos.combo.ic_tstat: `0.060517017754998605`
- oos.combo.top_bottom_spread: `-0.00038513717921724496`
- oos.combo.top_decile_excess_return: `-0.00047576231069376115`
- oos.combo.ic_decay: `{'5': 0.001401088172451719, '10': -0.0016193066805454959, '21': 0.012914889732936858}`

## Spec
```json
{
  "benchmark": "SPY",
  "combo": {
    "method": "single"
  },
  "evaluation": {
    "newey_west_lag_days": 5
  },
  "execution": {
    "spread_bps": 2.0,
    "timing": "next_open",
    "transaction_cost_bps": 5.0
  },
  "factors": [
    {
      "direction": "higher_is_better",
      "name": "RS_Rating_B",
      "params": {},
      "transform": "rank_pct",
      "weight": 1.0
    }
  ],
  "notes": null,
  "period": {
    "start": "2021-01-04",
    "test_end": "2025-12-31",
    "train_end": "2024-12-31"
  },
  "portfolio": {
    "max_annual_turnover": 12.0,
    "max_position_weight": 0.1,
    "rebalance": "weekly",
    "selection": "top_n",
    "threshold": null,
    "top_n": 20,
    "vol_lookback_days": 60,
    "weighting": "equal"
  },
  "spec_id": "pipeline_rs_rating_b",
  "universe": {
    "exclude_sectors": [
      "Utilities",
      "Energy",
      "Real Estate"
    ],
    "market_cap_min_usd": 100000000000.0,
    "min_names": 20
  }
}
```
