# Backtest Pipeline Report — pmarp_breadth_campaign_benchmark_t1_recent5_h20_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.5321, 'pass': True}`
- oos_sharpe_positive: `{'value': 0.5985, 'pass': True}`
- oos_ic_positive: `{'value': 0.051554185025259415, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 1.1247885735763954, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 4.5343, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.031782`
- is.annual_volatility: `0.059729`
- is.sharpe_ratio: `0.5321`
- is.max_drawdown: `-0.064954`
- is.annual_turnover: `2.0036`
- is.excess_cagr: `-0.008754000000000005`
- is.ir: `-0.1263`
### OOS
- oos.cagr: `0.031735`
- oos.annual_volatility: `0.053028`
- oos.sharpe_ratio: `0.5985`
- oos.max_drawdown: `-0.06584`
- oos.annual_turnover: `4.5343`
- oos.excess_cagr: `-0.142368`
- oos.ir: `-0.8163`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.013056452803413046`
- is.combo.ic_tstat: `0.1536462238439528`
- is.combo.top_bottom_spread: `-0.0035056709532225315`
- is.combo.top_decile_excess_return: `-0.0030596716495176375`
- is.combo.ic_decay: `{'5': 0.013056452803413046, '21': -0.26101196152954287, '63': -0.3782707913190742}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.051554185025259415`
- oos.combo.ic_tstat: `1.0018829576227204`
- oos.combo.top_bottom_spread: `0.0005987427373769726`
- oos.combo.top_decile_excess_return: `0.0011147023868887688`
- oos.combo.ic_decay: `{'5': 0.051554185025259415, '21': 0.20064921304590724, '63': 0.08234598734598733}`

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
        "recent_peak_window": 5,
        "regime_breadth_threshold": 0.5,
        "regime_fast_ema": 120,
        "regime_mode": "benchmark_ema",
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
  "notes": "Control run: benchmark EMA plus recent peak confirmation.",
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
  "spec_id": "pmarp_breadth_campaign_benchmark_t1_recent5_h20_top10_vol20",
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
