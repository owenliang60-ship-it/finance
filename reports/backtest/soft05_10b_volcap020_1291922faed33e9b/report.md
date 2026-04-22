# Backtest Pipeline Report — soft05_10b_volcap020

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.1539, 'pass': True}`
- oos_sharpe_positive: `{'value': 0.5439, 'pass': True}`
- oos_ic_positive: `{'value': 0.020288563294405988, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 3.53411306042885, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 9.6631, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.011565`
- is.annual_volatility: `0.075144`
- is.sharpe_ratio: `0.1539`
- is.max_drawdown: `-0.082356`
- is.annual_turnover: `4.4337`
- is.excess_cagr: `-0.028971000000000004`
- is.ir: `-0.2332`
### OOS
- oos.cagr: `0.041233`
- oos.annual_volatility: `0.075811`
- oos.sharpe_ratio: `0.5439`
- oos.max_drawdown: `-0.104404`
- oos.annual_turnover: `9.6631`
- oos.excess_cagr: `-0.13287000000000002`
- oos.ir: `-0.7162`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.028935798666088933`
- is.combo.ic_tstat: `0.5730647989240409`
- is.combo.top_bottom_spread: `0.005013661455674717`
- is.combo.top_decile_excess_return: `0.004441439360702402`
- is.combo.ic_decay: `{'5': 0.028935798666088933, '21': 0.0058371671970702445, '63': 0.017406460647539607}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.020288563294405988`
- oos.combo.ic_tstat: `0.47051989880959666`
- oos.combo.top_bottom_spread: `0.002249936328310676`
- oos.combo.top_decile_excess_return: `0.001984680568236302`
- oos.combo.ic_decay: `{'5': 0.020288563294405988, '21': -0.021632811661052874, '63': 0.012518753592513301}`

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
        "max_trailing_volatility": 0.2,
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
  "spec_id": "soft05_10b_volcap020",
  "universe": {
    "exclude_sectors": [
      "Utilities",
      "Energy",
      "Real Estate"
    ],
    "include_sectors": [],
    "market_cap_min_usd": 10000000000.0,
    "min_names": 20
  }
}
```
