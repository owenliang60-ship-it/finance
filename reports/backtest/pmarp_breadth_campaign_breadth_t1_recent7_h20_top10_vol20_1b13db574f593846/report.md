# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_recent7_h20_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.6551, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.0355, 'pass': True}`
- oos_ic_positive: `{'value': -0.007002500842042385, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 1.5806747061517326, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 4.1221, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.045543`
- is.annual_volatility: `0.069517`
- is.sharpe_ratio: `0.6551`
- is.max_drawdown: `-0.079306`
- is.annual_turnover: `1.6164`
- is.excess_cagr: `0.0050069999999999976`
- is.ir: `-0.041`
### OOS
- oos.cagr: `0.050514`
- oos.annual_volatility: `0.048783`
- oos.sharpe_ratio: `1.0355`
- oos.max_drawdown: `-0.051001`
- oos.annual_turnover: `4.1221`
- oos.excess_cagr: `-0.123589`
- oos.ir: `-0.7157`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.13821747358332725`
- is.combo.ic_tstat: `1.3772746354695695`
- is.combo.top_bottom_spread: `0.001872791145405183`
- is.combo.top_decile_excess_return: `0.005150327514849346`
- is.combo.ic_decay: `{'5': 0.13821747358332725, '21': -0.22064453162014136, '63': -0.2971879069440045}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.007002500842042385`
- oos.combo.ic_tstat: `-0.13244191107401113`
- oos.combo.top_bottom_spread: `-0.0026543343567007336`
- oos.combo.top_decile_excess_return: `0.00032117736618435846`
- oos.combo.ic_decay: `{'5': -0.007002500842042385, '21': 0.030245900231573586, '63': -0.1072355029750528}`

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
        "max_trailing_volatility": 0.2,
        "pmarp_ema_period": 20,
        "pmarp_lookback": 150,
        "recent_peak_threshold": 2.0,
        "recent_peak_window": 7,
        "regime_breadth_threshold": 0.5,
        "regime_fast_ema": 120,
        "regime_mode": "universe_breadth",
        "regime_slope_lookback": 20,
        "regime_slow_ema": 144,
        "regime_symbol": "SPY",
        "rvol_lookback": 120,
        "rvol_threshold": 2.0,
        "score_mode": "signal_rvol",
        "trigger_threshold": 1.0,
        "vol_lookback": 60
      },
      "transform": "raw",
      "weight": 1.0
    }
  ],
  "notes": "Extend the recent RVOL memory to 7 days for slower repair patterns.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_recent7_h20_top10_vol20",
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
