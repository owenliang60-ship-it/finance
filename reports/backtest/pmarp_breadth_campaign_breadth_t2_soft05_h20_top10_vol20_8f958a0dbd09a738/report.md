# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t2_soft05_h20_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.2921, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.009, 'pass': True}`
- oos_ic_positive: `{'value': 0.023267055492772016, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 3.4542964738103383, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 9.0173, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.02145`
- is.annual_volatility: `0.073441`
- is.sharpe_ratio: `0.2921`
- is.max_drawdown: `-0.079311`
- is.annual_turnover: `2.7503`
- is.excess_cagr: `-0.019086000000000002`
- is.ir: `-0.1798`
### OOS
- oos.cagr: `0.06851`
- oos.annual_volatility: `0.067897`
- oos.sharpe_ratio: `1.009`
- oos.max_drawdown: `-0.07249`
- oos.annual_turnover: `9.0173`
- oos.excess_cagr: `-0.105593`
- oos.ir: `-0.59`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.045642078676281034`
- is.combo.ic_tstat: `0.7152917637623628`
- is.combo.top_bottom_spread: `0.002492047526298711`
- is.combo.top_decile_excess_return: `0.004755152746269288`
- is.combo.ic_decay: `{'5': 0.045642078676281034, '21': 0.033269231651755854, '63': -0.11967283278734163}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.023267055492772016`
- oos.combo.ic_tstat: `0.5002197738692752`
- oos.combo.top_bottom_spread: `0.001222943057786748`
- oos.combo.top_decile_excess_return: `0.0014019753713794996`
- oos.combo.ic_decay: `{'5': 0.023267055492772016, '21': -0.027259413876566916, '63': 0.032339342809621544}`

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
        "recent_peak_threshold": 2.0,
        "recent_peak_window": 0,
        "regime_breadth_threshold": 0.5,
        "regime_fast_ema": 120,
        "regime_mode": "universe_breadth",
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
  "notes": "Check whether 2% trigger becomes better once breadth cleans the regime.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t2_soft05_h20_top10_vol20",
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
