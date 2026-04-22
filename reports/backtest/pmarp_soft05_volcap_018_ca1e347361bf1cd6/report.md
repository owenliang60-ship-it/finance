# Backtest Pipeline Report — pmarp_soft05_volcap_018

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.764, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.1887, 'pass': True}`
- oos_ic_positive: `{'value': -0.04038461538461537, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 1.555890052356021, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 1.5512, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.023122`
- is.annual_volatility: `0.030265`
- is.sharpe_ratio: `0.764`
- is.max_drawdown: `-0.033278`
- is.annual_turnover: `0.9873`
- is.excess_cagr: `-0.017414000000000002`
- is.ir: `-0.1766`
### OOS
- oos.cagr: `0.039217`
- oos.annual_volatility: `0.032993`
- oos.sharpe_ratio: `1.1887`
- oos.max_drawdown: `-0.029425`
- oos.annual_turnover: `1.5512`
- oos.excess_cagr: `-0.134886`
- oos.ir: `-0.8199`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.09233766233766234`
- is.combo.ic_tstat: `-0.8939237171216299`
- is.combo.top_bottom_spread: `-0.009231910926464915`
- is.combo.top_decile_excess_return: `-0.004271101560482448`
- is.combo.ic_decay: `{'5': -0.09233766233766234, '21': -0.19954545454545458, '63': -0.19341894060995185}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.04038461538461537`
- oos.combo.ic_tstat: `-0.4140615193013983`
- oos.combo.top_bottom_spread: `0.003364873402819375`
- oos.combo.top_decile_excess_return: `-0.0010317404447577779`
- oos.combo.ic_decay: `{'5': -0.04038461538461537, '21': 0.3055991627420198, '63': 0.29690749863163657}`

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
      "name": "PMARP_Rebound_V1",
      "params": {
        "confirm_floor": 0.5,
        "confirm_mode": "soft",
        "holding_window_days": 20,
        "max_trailing_volatility": 0.18,
        "pmarp_ema_period": 20,
        "pmarp_lookback": 150,
        "regime_fast_ema": 120,
        "regime_mode": "benchmark_ema",
        "regime_slope_lookback": 20,
        "regime_slow_ema": 144,
        "regime_symbol": "SPY",
        "rvol_lookback": 120,
        "rvol_threshold": 2.0,
        "score_mode": "signal_rvol",
        "trigger_threshold": 2.0,
        "vol_lookback": 60
      },
      "transform": "raw",
      "weight": 1.0
    }
  ],
  "notes": "Long-split baseline on 2026-04-12: IS 2021-07-01..2023-12-31, OOS 2024-01-01..2026-04-10. PMARP upcross 2% + soft RVOL confirmation (floor 0.5) + SPY EMA120/144 regime, carried for 20 trading days.",
  "period": {
    "start": "2021-07-01",
    "test_end": "2026-04-10",
    "train_end": "2023-12-31"
  },
  "portfolio": {
    "max_annual_turnover": 20.0,
    "max_position_weight": 0.1,
    "rebalance": "daily",
    "selection": "top_n",
    "threshold": null,
    "top_n": 10,
    "vol_lookback_days": 60,
    "weighting": "equal"
  },
  "spec_id": "pmarp_soft05_volcap_018",
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
