# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.4623, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.1831, 'pass': True}`
- oos_ic_positive: `{'value': 0.02582046433121, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 2.5591607181483886, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 6.3992, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.033428`
- is.annual_volatility: `0.072311`
- is.sharpe_ratio: `0.4623`
- is.max_drawdown: `-0.079311`
- is.annual_turnover: `2.4799`
- is.excess_cagr: `-0.007108000000000003`
- is.ir: `-0.1074`
### OOS
- oos.cagr: `0.075004`
- oos.annual_volatility: `0.063397`
- oos.sharpe_ratio: `1.1831`
- oos.max_drawdown: `-0.064298`
- oos.annual_turnover: `6.3992`
- oos.excess_cagr: `-0.099099`
- oos.ir: `-0.56`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.03373722408701703`
- is.combo.ic_tstat: `0.4198692141631531`
- is.combo.top_bottom_spread: `-0.0022756040247346216`
- is.combo.top_decile_excess_return: `0.0016365399585900387`
- is.combo.ic_decay: `{'5': 0.03373722408701703, '21': -0.0019406457782246219, '63': 0.018301609089159256}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.02582046433121`
- oos.combo.ic_tstat: `0.5890984014467054`
- oos.combo.top_bottom_spread: `0.003182777772904507`
- oos.combo.top_decile_excess_return: `0.0007375931949666097`
- oos.combo.ic_decay: `{'5': 0.02582046433121, '21': 0.04920660803714749, '63': -0.07580774931535503}`

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
  "notes": "1% trigger + breadth 50% + soft confirmation is the clean base case.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_vol20",
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
