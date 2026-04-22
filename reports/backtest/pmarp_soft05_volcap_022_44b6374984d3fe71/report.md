# Backtest Pipeline Report — pmarp_soft05_volcap_022

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.7685, 'pass': True}`
- oos_sharpe_positive: `{'value': 0.6563, 'pass': True}`
- oos_ic_positive: `{'value': -0.010815497907889217, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 0.8540013012361743, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 4.39, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.038651`
- is.annual_volatility: `0.050296`
- is.sharpe_ratio: `0.7685`
- is.max_drawdown: `-0.053555`
- is.annual_turnover: `2.2556`
- is.excess_cagr: `-0.0018850000000000047`
- is.ir: `-0.0873`
### OOS
- oos.cagr: `0.036091`
- oos.annual_volatility: `0.054988`
- oos.sharpe_ratio: `0.6563`
- oos.max_drawdown: `-0.065494`
- oos.annual_turnover: `4.39`
- oos.excess_cagr: `-0.13801200000000002`
- oos.ir: `-0.8269`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.03860364234695785`
- is.combo.ic_tstat: `-0.4910473488110704`
- is.combo.top_bottom_spread: `0.003940242629785058`
- is.combo.top_decile_excess_return: `0.004674223991704376`
- is.combo.ic_decay: `{'5': -0.03860364234695785, '21': -0.009162317183707554, '63': 0.12877102321546766}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.010815497907889217`
- oos.combo.ic_tstat: `-0.21678647415127092`
- oos.combo.top_bottom_spread: `0.0035404924113415226`
- oos.combo.top_decile_excess_return: `-5.5036286759223104e-05`
- oos.combo.ic_decay: `{'5': -0.010815497907889217, '21': 0.06945403081766717, '63': 0.22769223298069455}`

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
        "max_trailing_volatility": 0.22,
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
  "spec_id": "pmarp_soft05_volcap_022",
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
