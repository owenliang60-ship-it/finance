# Backtest Pipeline Report — pipeline_pmarp_rebound_v1

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.6387, 'pass': False}`
- oos_sharpe_positive: `{'value': 0.8995, 'pass': True}`
- oos_ic_positive: `{'value': -0.0002829505104792202, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -1.408329419132613, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 4.8555, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.070047`
- is.annual_volatility: `0.109666`
- is.sharpe_ratio: `-0.6387`
- is.max_drawdown: `-0.136155`
- is.annual_turnover: `5.0877`
- is.excess_cagr: `-0.31003400000000003`
- is.ir: `-1.7154`
### OOS
- oos.cagr: `0.081562`
- oos.annual_volatility: `0.090675`
- oos.sharpe_ratio: `0.8995`
- oos.max_drawdown: `-0.080195`
- oos.annual_turnover: `4.8555`
- oos.excess_cagr: `-0.08626800000000001`
- oos.ir: `-0.3806`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.09497003937681901`
- is.combo.ic_tstat: `1.36872799540176`
- is.combo.top_bottom_spread: `0.010118287835105523`
- is.combo.top_decile_excess_return: `-0.0013787894065204545`
- is.combo.ic_decay: `{'5': 0.09497003937681901, '21': 0.28680729208679523, '63': 0.35773138707023}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.0002829505104792202`
- oos.combo.ic_tstat: `-0.0037349951400575997`
- oos.combo.top_bottom_spread: `0.0010166764487273205`
- oos.combo.top_decile_excess_return: `0.002099536534154608`
- oos.combo.ic_decay: `{'5': -0.0002829505104792202, '21': -0.09394880481837002, '63': -0.06376024871802162}`

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
        "holding_window_days": 30,
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
  "notes": "PMARP upcross 2% trigger + RVOL>2 confirmation + SPY EMA120/144 regime filter, carried for 30 trading days.",
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
  "spec_id": "pipeline_pmarp_rebound_v1",
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
