# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_soft00_h20_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.0021, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.3207, 'pass': True}`
- oos_ic_positive: `{'value': 0.033522076761397804, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 628.9047619047619, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 9.2948, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.000115`
- is.annual_volatility: `0.055709`
- is.sharpe_ratio: `0.0021`
- is.max_drawdown: `-0.061755`
- is.annual_turnover: `3.324`
- is.excess_cagr: `-0.040421000000000006`
- is.ir: `-0.3141`
### OOS
- oos.cagr: `0.095169`
- oos.annual_volatility: `0.072062`
- oos.sharpe_ratio: `1.3207`
- oos.max_drawdown: `-0.054793`
- oos.annual_turnover: `9.2948`
- oos.excess_cagr: `-0.078934`
- oos.ir: `-0.4687`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.006582009301639168`
- is.combo.ic_tstat: `0.12478160342175816`
- is.combo.top_bottom_spread: `0.00022886102586919062`
- is.combo.top_decile_excess_return: `0.0009189673807901807`
- is.combo.ic_decay: `{'5': 0.006582009301639168, '21': 0.04484750971106824, '63': 0.1811017406656269}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.033522076761397804`
- oos.combo.ic_tstat: `0.8647250962866846`
- oos.combo.top_bottom_spread: `0.000385917675750485`
- oos.combo.top_decile_excess_return: `0.0011329374058987516`
- oos.combo.ic_decay: `{'5': 0.033522076761397804, '21': 0.04209134612278654, '63': -0.08617521429783578}`

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
        "confirm_floor": 0.0,
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
        "trigger_threshold": 1.0,
        "vol_lookback": 60
      },
      "transform": "raw",
      "weight": 1.0
    }
  ],
  "notes": "If RVOL is mostly noise, removing the soft floor may help breadth do the filtering.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_soft00_h20_top10_vol20",
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
