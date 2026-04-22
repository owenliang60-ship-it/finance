# Backtest Pipeline Report — pipeline_pmarp_rebound_v1_peak7_longsplit

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.3277, 'pass': False}`
- oos_sharpe_positive: `{'value': -0.0249, 'pass': False}`
- oos_ic_positive: `{'value': -0.028683436611411067, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 0.07598413182789136, 'threshold': 0.5, 'pass': False}`
- annual_turnover_within_limit: `{'value': 12.3435, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.030569`
- is.annual_volatility: `0.09329`
- is.sharpe_ratio: `-0.3277`
- is.max_drawdown: `-0.153238`
- is.annual_turnover: `4.733`
- is.excess_cagr: `-0.071105`
- is.ir: `-0.4854`
### OOS
- oos.cagr: `-0.003217`
- oos.annual_volatility: `0.12925`
- oos.sharpe_ratio: `-0.0249`
- oos.max_drawdown: `-0.198147`
- oos.annual_turnover: `12.3435`
- oos.excess_cagr: `-0.17732`
- oos.ir: `-1.0364`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.004851221892999651`
- is.combo.ic_tstat: `-0.07511394031952305`
- is.combo.top_bottom_spread: `-0.002342105944308864`
- is.combo.top_decile_excess_return: `-0.0024855374170990644`
- is.combo.ic_decay: `{'5': -0.004851221892999651, '21': -0.13228714226624902, '63': -0.02013562906775915}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.028683436611411067`
- oos.combo.ic_tstat: `-0.7593871333332237`
- oos.combo.top_bottom_spread: `0.0018813040876876825`
- oos.combo.top_decile_excess_return: `-0.0009504512855074619`
- oos.combo.ic_decay: `{'5': -0.028683436611411067, '21': -0.010401451572818916, '63': 0.04675981661512717}`

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
        "confirm_mode": "recent_peak_soft",
        "holding_window_days": 20,
        "pmarp_ema_period": 20,
        "pmarp_lookback": 150,
        "recent_peak_threshold": 2.0,
        "recent_peak_window": 7,
        "regime_fast_ema": 120,
        "regime_mode": "benchmark_ema",
        "regime_slope_lookback": 20,
        "regime_slow_ema": 144,
        "regime_symbol": "SPY",
        "rvol_lookback": 120,
        "rvol_threshold": 2.0,
        "score_mode": "signal_rvol",
        "trigger_threshold": 2.0
      },
      "transform": "raw",
      "weight": 1.0
    }
  ],
  "notes": "Long-split experiment on 2026-04-12: IS 2021-07-01..2023-12-31, OOS 2024-01-01..2026-04-10. PMARP upcross 2% + prior-7-day RVOL peak >=2.0 + current RVOL floor 0.5 + SPY EMA120/144 regime, carried for 20 trading days.",
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
  "spec_id": "pipeline_pmarp_rebound_v1_peak7_longsplit",
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
