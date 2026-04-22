# Backtest Pipeline Report — pmarp_soft05_volcap_02

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.9445, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.0877, 'pass': True}`
- oos_ic_positive: `{'value': -0.08969400034381984, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 1.1516146109052408, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 2.8329, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.03899`
- is.annual_volatility: `0.04128`
- is.sharpe_ratio: `0.9445`
- is.max_drawdown: `-0.041323`
- is.annual_turnover: `1.532`
- is.excess_cagr: `-0.0015460000000000057`
- is.ir: `-0.0878`
### OOS
- oos.cagr: `0.043475`
- oos.annual_volatility: `0.039969`
- oos.sharpe_ratio: `1.0877`
- oos.max_drawdown: `-0.033795`
- oos.annual_turnover: `2.8329`
- oos.excess_cagr: `-0.13062800000000002`
- oos.ir: `-0.7747`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.06521811521811524`
- is.combo.ic_tstat: `-0.7757244940186907`
- is.combo.top_bottom_spread: `-0.00812377790154468`
- is.combo.top_decile_excess_return: `-0.0018546195708868032`
- is.combo.ic_decay: `{'5': -0.06521811521811524, '21': -0.15859140859140858, '63': -0.17728337236533956}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.08969400034381984`
- oos.combo.ic_tstat: `-1.2745412378642638`
- oos.combo.top_bottom_spread: `0.0008049185781989356`
- oos.combo.top_decile_excess_return: `0.00020524930589961953`
- oos.combo.ic_decay: `{'5': -0.08969400034381984, '21': 0.06973039215686273, '63': 0.0746961546124726}`

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
  "spec_id": "pmarp_soft05_volcap_02",
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
