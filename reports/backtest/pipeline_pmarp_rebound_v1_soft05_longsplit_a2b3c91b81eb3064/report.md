# Backtest Pipeline Report — pipeline_pmarp_rebound_v1_soft05_longsplit

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.5696, 'pass': False}`
- oos_sharpe_positive: `{'value': -0.007, 'pass': False}`
- oos_ic_positive: `{'value': 0.002128396749504034, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 0.01228932584269663, 'threshold': 0.5, 'pass': False}`
- annual_turnover_within_limit: `{'value': 15.1564, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.074016`
- is.annual_volatility: `0.129939`
- is.sharpe_ratio: `-0.5696`
- is.max_drawdown: `-0.260379`
- is.annual_turnover: `6.2143`
- is.excess_cagr: `-0.114552`
- is.ir: `-0.703`
### OOS
- oos.cagr: `-0.001115`
- oos.annual_volatility: `0.158685`
- oos.sharpe_ratio: `-0.007`
- oos.max_drawdown: `-0.222027`
- oos.annual_turnover: `15.1564`
- oos.excess_cagr: `-0.175218`
- oos.ir: `-0.9492`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.08455811225197174`
- is.combo.ic_tstat: `-2.0220712975083033`
- is.combo.top_bottom_spread: `-0.005810331633692503`
- is.combo.top_decile_excess_return: `-0.006018298995903775`
- is.combo.ic_decay: `{'5': -0.08455811225197174, '21': -0.2509961134062424, '63': -0.053401759704878655}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.002128396749504034`
- oos.combo.ic_tstat: `0.0700349269995866`
- oos.combo.top_bottom_spread: `0.0069455504080086295`
- oos.combo.top_decile_excess_return: `0.0028814459089443`
- oos.combo.ic_decay: `{'5': 0.002128396749504034, '21': 0.0007872112324067364, '63': -0.08537306332562078}`

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
        "trigger_threshold": 2.0
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
  "spec_id": "pipeline_pmarp_rebound_v1_soft05_longsplit",
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
