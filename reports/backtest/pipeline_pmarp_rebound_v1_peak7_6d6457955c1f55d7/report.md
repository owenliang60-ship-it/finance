# Backtest Pipeline Report — pipeline_pmarp_rebound_v1_peak7

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -1.1345, 'pass': False}`
- oos_sharpe_positive: `{'value': 1.048, 'pass': True}`
- oos_ic_positive: `{'value': 0.024413438544354054, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -0.9237549581313353, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 11.4667, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.134053`
- is.annual_volatility: `0.118158`
- is.sharpe_ratio: `-1.1345`
- is.max_drawdown: `-0.146452`
- is.annual_turnover: `11.1416`
- is.excess_cagr: `-0.37404000000000004`
- is.ir: `-2.1226`
### OOS
- oos.cagr: `0.120999`
- oos.annual_volatility: `0.115458`
- oos.sharpe_ratio: `1.048`
- oos.max_drawdown: `-0.131422`
- oos.annual_turnover: `11.4667`
- oos.excess_cagr: `-0.04683100000000001`
- oos.ir: `-0.2104`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `-0.06264804882052255`
- is.combo.ic_tstat: `-1.1154413295222299`
- is.combo.top_bottom_spread: `0.004049397635173208`
- is.combo.top_decile_excess_return: `-5.3417391491879944e-05`
- is.combo.ic_decay: `{'5': -0.06264804882052255, '21': -0.015310760044546902, '63': 0.0044255447993089125}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.024413438544354054`
- oos.combo.ic_tstat: `0.3956178728160243`
- oos.combo.top_bottom_spread: `-0.0003447884678043496`
- oos.combo.top_decile_excess_return: `-0.0009542932454402815`
- oos.combo.ic_decay: `{'5': 0.024413438544354054, '21': 0.007395735364887522, '63': 0.0532600464052077}`

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
        "pmarp_ema_period": 20,
        "pmarp_lookback": 150,
        "recent_peak_threshold": 2.0,
        "recent_peak_window": 7,
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
  "notes": "Experiment on 2026-04-12: PMARP upcross 2% trigger + recent RVOL peak within prior 7 trading days (>=2.0) + current RVOL floor 0.5 + SPY EMA120/144 regime, carried for 20 trading days.",
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
  "spec_id": "pipeline_pmarp_rebound_v1_peak7",
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
