# Backtest Pipeline Report — pipeline_pmarp_rebound_v1_hold20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.5117, 'pass': False}`
- oos_sharpe_positive: `{'value': 1.8178, 'pass': True}`
- oos_ic_positive: `{'value': 0.022314921365554283, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -3.552472151651358, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 4.5847, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.045199`
- is.annual_volatility: `0.088323`
- is.sharpe_ratio: `-0.5117`
- is.max_drawdown: `-0.106473`
- is.annual_turnover: `4.7729`
- is.excess_cagr: `-0.285186`
- is.ir: `-1.7298`
### OOS
- oos.cagr: `0.125158`
- oos.annual_volatility: `0.068852`
- oos.sharpe_ratio: `1.8178`
- oos.max_drawdown: `-0.04819`
- oos.annual_turnover: `4.5847`
- oos.excess_cagr: `-0.042672000000000015`
- oos.ir: `-0.2403`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.0497464844523668`
- is.combo.ic_tstat: `0.5975481609776703`
- is.combo.top_bottom_spread: `0.0040502371270410035`
- is.combo.top_decile_excess_return: `-0.0048651254311791415`
- is.combo.ic_decay: `{'5': 0.0497464844523668, '21': 0.23778083230637975, '63': 0.3686606322969959}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.022314921365554283`
- oos.combo.ic_tstat: `0.24324712091024933`
- oos.combo.top_bottom_spread: `0.0029790587933383513`
- oos.combo.top_decile_excess_return: `0.0041911184527425645`
- oos.combo.ic_decay: `{'5': 0.022314921365554283, '21': -0.057212775997221806, '63': -0.027948824630746823}`

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
        "holding_window_days": 20,
        "pmarp_ema_period": 20,
        "pmarp_lookback": 150,
        "regime_fast_ema": 120,
        "regime_slope_lookback": 20,
        "regime_slow_ema": 144,
        "regime_symbol": "SPY",
        "rvol_lookback": 120,
        "rvol_threshold": 2.0,
        "score_mode": "signal_rvol",
        "trigger_threshold": 2.0
      },
      "transform": "raw",
      "weight": 1.0
    }
  ],
  "notes": "Quick-sweep winner on 2026-04-12: PMARP upcross 2% trigger + RVOL>2 confirmation + SPY EMA120/144 regime, carried for 20 trading days.",
  "period": {
    "start": "2024-01-02",
    "test_end": "2025-12-31",
    "train_end": "2024-12-31"
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
  "spec_id": "pipeline_pmarp_rebound_v1_hold20",
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
