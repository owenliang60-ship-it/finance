# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_soft05_h40_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.2814, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.4052, 'pass': True}`
- oos_ic_positive: `{'value': -0.015815530882248647, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 4.99360341151386, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 6.2132, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.021978`
- is.annual_volatility: `0.078111`
- is.sharpe_ratio: `0.2814`
- is.max_drawdown: `-0.078324`
- is.annual_turnover: `2.3857`
- is.excess_cagr: `-0.018558`
- is.ir: `-0.1704`
### OOS
- oos.cagr: `0.107471`
- oos.annual_volatility: `0.076482`
- oos.sharpe_ratio: `1.4052`
- oos.max_drawdown: `-0.071114`
- oos.annual_turnover: `6.2132`
- oos.excess_cagr: `-0.06663200000000001`
- oos.ir: `-0.3637`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.013259375173063387`
- is.combo.ic_tstat: `0.2553330207258056`
- is.combo.top_bottom_spread: `0.001198587001068851`
- is.combo.top_decile_excess_return: `0.003342631903010165`
- is.combo.ic_decay: `{'5': 0.013259375173063387, '21': 0.010802514217172062, '63': -0.045912728361298547}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.015815530882248647`
- oos.combo.ic_tstat: `-0.5414494959071255`
- oos.combo.top_bottom_spread: `-0.0033398438650912533`
- oos.combo.top_decile_excess_return: `-0.0011526633376118657`
- oos.combo.ic_decay: `{'5': -0.015815530882248647, '21': -0.01421759736705628, '63': -0.0865801247312102}`

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
        "holding_window_days": 40,
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
  "notes": "The edge may still need longer carry even inside breadth-on regimes.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_soft05_h40_top10_vol20",
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
