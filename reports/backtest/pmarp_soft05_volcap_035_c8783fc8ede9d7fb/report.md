# Backtest Pipeline Report — pmarp_soft05_volcap_035

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.4515, 'pass': False}`
- oos_sharpe_positive: `{'value': -0.0511, 'pass': False}`
- oos_ic_positive: `{'value': -0.022679273203188917, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 0.11317829457364341, 'threshold': 0.5, 'pass': False}`
- annual_turnover_within_limit: `{'value': 11.3135, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.045184`
- is.annual_volatility: `0.10007`
- is.sharpe_ratio: `-0.4515`
- is.max_drawdown: `-0.170981`
- is.annual_turnover: `5.3103`
- is.excess_cagr: `-0.08572`
- is.ir: `-0.5606`
### OOS
- oos.cagr: `-0.00569`
- oos.annual_volatility: `0.111237`
- oos.sharpe_ratio: `-0.0511`
- oos.max_drawdown: `-0.130892`
- oos.annual_turnover: `11.3135`
- oos.excess_cagr: `-0.179793`
- oos.ir: `-1.0485`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.08400846376639967`
- is.combo.ic_tstat: `-1.9666268014705095`
- is.combo.top_bottom_spread: `-0.003823419689610475`
- is.combo.top_decile_excess_return: `-0.00418330562667129`
- is.combo.ic_decay: `{'5': -0.08400846376639967, '21': -0.26815963681882493, '63': -0.06481579818077836}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.022679273203188917`
- oos.combo.ic_tstat: `-0.6500062108754171`
- oos.combo.top_bottom_spread: `0.0066129296014158295`
- oos.combo.top_decile_excess_return: `0.0011638625816162212`
- oos.combo.ic_decay: `{'5': -0.022679273203188917, '21': 0.001055976314084715, '63': -0.05339972056132015}`

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
        "max_trailing_volatility": 0.35,
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
  "spec_id": "pmarp_soft05_volcap_035",
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
