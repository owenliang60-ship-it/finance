# Backtest Pipeline Report — pmarp_soft05_volcap_03

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.3884, 'pass': False}`
- oos_sharpe_positive: `{'value': 0.3144, 'pass': True}`
- oos_ic_positive: `{'value': -0.026602663084056503, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -0.8094747682801235, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 9.4079, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.03848`
- is.annual_volatility: `0.099083`
- is.sharpe_ratio: `-0.3884`
- is.max_drawdown: `-0.170052`
- is.annual_turnover: `4.7817`
- is.excess_cagr: `-0.079016`
- is.ir: `-0.5176`
### OOS
- oos.cagr: `0.028351`
- oos.annual_volatility: `0.090179`
- oos.sharpe_ratio: `0.3144`
- oos.max_drawdown: `-0.108171`
- oos.annual_turnover: `9.4079`
- oos.excess_cagr: `-0.145752`
- oos.ir: `-0.8829`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.10005105801792766`
- is.combo.ic_tstat: `-2.006719326573872`
- is.combo.top_bottom_spread: `-0.0060874262616525355`
- is.combo.top_decile_excess_return: `-0.005012726156360398`
- is.combo.ic_decay: `{'5': -0.10005105801792766, '21': -0.1956916524967563, '63': -0.052399477495457515}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.026602663084056503`
- oos.combo.ic_tstat: `-0.737956095530279`
- oos.combo.top_bottom_spread: `0.0017084519076992024`
- oos.combo.top_decile_excess_return: `-0.0001598948694925957`
- oos.combo.ic_decay: `{'5': -0.026602663084056503, '21': -0.007924150624211198, '63': -0.07527390475326459}`

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
        "max_trailing_volatility": 0.3,
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
  "spec_id": "pmarp_soft05_volcap_03",
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
