# Backtest Pipeline Report — pmarp_soft05_volcap_025

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.033, 'pass': True}`
- oos_sharpe_positive: `{'value': 0.1726, 'pass': True}`
- oos_ic_positive: `{'value': -0.02255207217226236, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 5.2303030303030305, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 6.7311, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.00223`
- is.annual_volatility: `0.067651`
- is.sharpe_ratio: `0.033`
- is.max_drawdown: `-0.100873`
- is.annual_turnover: `3.4526`
- is.excess_cagr: `-0.038306`
- is.ir: `-0.2905`
### OOS
- oos.cagr: `0.012326`
- oos.annual_volatility: `0.071416`
- oos.sharpe_ratio: `0.1726`
- oos.max_drawdown: `-0.090063`
- oos.annual_turnover: `6.7311`
- oos.excess_cagr: `-0.161777`
- oos.ir: `-0.9423`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.14077441943083657`
- is.combo.ic_tstat: `-2.1494200336517966`
- is.combo.top_bottom_spread: `-0.0015701997683026366`
- is.combo.top_decile_excess_return: `-0.0012718340756479934`
- is.combo.ic_decay: `{'5': -0.14077441943083657, '21': -0.16259374092930243, '63': 0.05080915062815516}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.02255207217226236`
- oos.combo.ic_tstat: `-0.5191718930219861`
- oos.combo.top_bottom_spread: `-0.0002689120785151034`
- oos.combo.top_decile_excess_return: `-0.002633136702443036`
- oos.combo.ic_decay: `{'5': -0.02255207217226236, '21': -0.030056479349156298, '63': -0.025499757242911043}`

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
        "max_trailing_volatility": 0.25,
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
  "spec_id": "pmarp_soft05_volcap_025",
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
