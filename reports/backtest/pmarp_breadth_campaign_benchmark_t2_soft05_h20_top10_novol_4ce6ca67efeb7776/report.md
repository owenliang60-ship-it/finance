# Backtest Pipeline Report — pmarp_breadth_campaign_benchmark_t2_soft05_h20_top10_novol

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.4988, 'pass': False}`
- oos_sharpe_positive: `{'value': 0.9423, 'pass': True}`
- oos_ic_positive: `{'value': 0.00464168445743318, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -1.8891339214113874, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 22.6292, 'threshold': 20.0, 'pass': False}`

## Strategy Metrics
### IS
- is.cagr: `-0.074011`
- is.annual_volatility: `0.148368`
- is.sharpe_ratio: `-0.4988`
- is.max_drawdown: `-0.272651`
- is.annual_turnover: `10.4294`
- is.excess_cagr: `-0.114547`
- is.ir: `-0.6776`
### OOS
- oos.cagr: `0.193484`
- oos.annual_volatility: `0.205325`
- oos.sharpe_ratio: `0.9423`
- oos.max_drawdown: `-0.198453`
- oos.annual_turnover: `22.6292`
- oos.excess_cagr: `0.01938099999999998`
- oos.ir: `-0.082`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.08560104044856476`
- is.combo.ic_tstat: `-2.6209866529200743`
- is.combo.top_bottom_spread: `-0.010514215219466017`
- is.combo.top_decile_excess_return: `-0.007398926283033039`
- is.combo.ic_decay: `{'5': -0.08560104044856476, '21': -0.1575283339116294, '63': -0.0679196847880527}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.00464168445743318`
- oos.combo.ic_tstat: `0.2898667633734703`
- oos.combo.top_bottom_spread: `0.0005467051023180658`
- oos.combo.top_decile_excess_return: `-0.000616462834868152`
- oos.combo.ic_decay: `{'5': 0.00464168445743318, '21': 0.003792639976233042, '63': 0.008017672636238605}`

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
        "recent_peak_threshold": 2.0,
        "recent_peak_window": 0,
        "regime_breadth_threshold": 0.5,
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
  "notes": "Use the known soft05 10B benchmark EMA control as the first sanity baseline.",
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
  "spec_id": "pmarp_breadth_campaign_benchmark_t2_soft05_h20_top10_novol",
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
