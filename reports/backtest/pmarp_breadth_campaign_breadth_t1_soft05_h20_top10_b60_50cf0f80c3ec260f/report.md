# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_b60

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.0322, 'pass': False}`
- oos_sharpe_positive: `{'value': -0.0853, 'pass': False}`
- oos_ic_positive: `{'value': -0.05427555846395557, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 2.6490683229813667, 'threshold': 0.5, 'pass': False}`
- annual_turnover_within_limit: `{'value': 3.7949, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.002104`
- is.annual_volatility: `0.065317`
- is.sharpe_ratio: `-0.0322`
- is.max_drawdown: `-0.079306`
- is.annual_turnover: `1.0746`
- is.excess_cagr: `-0.042640000000000004`
- is.ir: `-0.3205`
### OOS
- oos.cagr: `-0.003983`
- oos.annual_volatility: `0.046713`
- oos.sharpe_ratio: `-0.0853`
- oos.max_drawdown: `-0.061346`
- oos.annual_turnover: `3.7949`
- oos.excess_cagr: `-0.17808600000000002`
- oos.ir: `-1.0381`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.06783982301223677`
- is.combo.ic_tstat: `0.5222607579800771`
- is.combo.top_bottom_spread: `-0.003998757395053029`
- is.combo.top_decile_excess_return: `0.0004583713887020739`
- is.combo.ic_decay: `{'5': 0.06783982301223677, '21': 0.07254010357458632, '63': 0.12014537186950981}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.05427555846395557`
- oos.combo.ic_tstat: `-0.8743788235158442`
- oos.combo.top_bottom_spread: `-0.00042898904735536096`
- oos.combo.top_decile_excess_return: `-0.0023520453500577225`
- oos.combo.ic_decay: `{'5': -0.05427555846395557, '21': 0.03824877379901437, '63': 0.026656847016659744}`

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
        "regime_breadth_threshold": 0.6,
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
  "notes": "Push breadth to 60% and see whether quality beats sample size.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_b60",
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
