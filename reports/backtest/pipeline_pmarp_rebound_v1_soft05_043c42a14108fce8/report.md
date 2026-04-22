# Backtest Pipeline Report — pipeline_pmarp_rebound_v1_soft05

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -1.3776, 'pass': False}`
- oos_sharpe_positive: `{'value': 1.6029, 'pass': True}`
- oos_ic_positive: `{'value': -0.03065619747654443, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -1.1635452961672474, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 11.9367, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.180419`
- is.annual_volatility: `0.130968`
- is.sharpe_ratio: `-1.3776`
- is.max_drawdown: `-0.192332`
- is.annual_turnover: `15.0213`
- is.excess_cagr: `-0.420406`
- is.ir: `-2.2768`
### OOS
- oos.cagr: `0.240832`
- oos.annual_volatility: `0.150246`
- oos.sharpe_ratio: `1.6029`
- oos.max_drawdown: `-0.110093`
- oos.annual_turnover: `11.9367`
- oos.excess_cagr: `0.07300199999999998`
- oos.ir: `0.2199`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.02461405198636432`
- is.combo.ic_tstat: `0.5622029288710592`
- is.combo.top_bottom_spread: `0.00857425537768074`
- is.combo.top_decile_excess_return: `-0.00011275643958352777`
- is.combo.ic_decay: `{'5': 0.02461405198636432, '21': 0.050151179080712, '63': 0.07302957496908818}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.03065619747654443`
- oos.combo.ic_tstat: `-0.5561004319059398`
- oos.combo.top_bottom_spread: `0.005665020507923816`
- oos.combo.top_decile_excess_return: `0.007061103704667739`
- oos.combo.ic_decay: `{'5': -0.03065619747654443, '21': -0.07040078596291606, '63': -0.3725986659802692}`

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
        "regime_fast_ema": 120,
        "regime_mode": "benchmark_ema",
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
  "notes": "Iterated winner on 2026-04-12: PMARP upcross 2% trigger + soft RVOL confirmation (floor 0.5) + SPY EMA120/144 regime, carried for 20 trading days.",
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
  "spec_id": "pipeline_pmarp_rebound_v1_soft05",
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
