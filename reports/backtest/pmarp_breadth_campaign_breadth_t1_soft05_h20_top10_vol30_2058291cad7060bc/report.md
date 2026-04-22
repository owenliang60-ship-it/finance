# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_vol30

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.2063, 'pass': False}`
- oos_sharpe_positive: `{'value': 0.8174, 'pass': True}`
- oos_ic_positive: `{'value': 0.008091926471566667, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -3.9621909840038776, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 16.3458, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.015574`
- is.annual_volatility: `0.075499`
- is.sharpe_ratio: `-0.2063`
- is.max_drawdown: `-0.094798`
- is.annual_turnover: `5.6761`
- is.excess_cagr: `-0.05611`
- is.ir: `-0.4115`
### OOS
- oos.cagr: `0.099645`
- oos.annual_volatility: `0.121906`
- oos.sharpe_ratio: `0.8174`
- oos.max_drawdown: `-0.093056`
- oos.annual_turnover: `16.3458`
- oos.excess_cagr: `-0.07445800000000001`
- oos.ir: `-0.4635`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.05564446382864038`
- is.combo.ic_tstat: `1.5744615036176257`
- is.combo.top_bottom_spread: `0.00013503132864732228`
- is.combo.top_decile_excess_return: `-0.0011937430108128544`
- is.combo.ic_decay: `{'5': 0.05564446382864038, '21': 0.03200824023966257, '63': -0.007897460819671472}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.008091926471566667`
- oos.combo.ic_tstat: `0.29524265704512787`
- oos.combo.top_bottom_spread: `0.000629839331747461`
- oos.combo.top_decile_excess_return: `-0.0004723914384333531`
- oos.combo.ic_decay: `{'5': 0.008091926471566667, '21': 0.10450847695388372, '63': 0.07451256649860191}`

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
        "max_trailing_volatility": 0.3,
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
        "trigger_threshold": 1.0,
        "vol_lookback": 60
      },
      "transform": "raw",
      "weight": 1.0
    }
  ],
  "notes": "Let more high-vol names in and see if breadth can contain the damage.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_vol30",
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
