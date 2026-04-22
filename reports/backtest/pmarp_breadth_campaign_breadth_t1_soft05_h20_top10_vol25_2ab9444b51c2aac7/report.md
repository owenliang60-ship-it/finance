# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_vol25

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': -0.0801, 'pass': False}`
- oos_sharpe_positive: `{'value': 0.334, 'pass': True}`
- oos_ic_positive: `{'value': 0.010980348942252001, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': -4.169787765293384, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 13.4898, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `-0.005623`
- is.annual_volatility: `0.070231`
- is.sharpe_ratio: `-0.0801`
- is.max_drawdown: `-0.079095`
- is.annual_turnover: `4.711`
- is.excess_cagr: `-0.046159000000000006`
- is.ir: `-0.3446`
### OOS
- oos.cagr: `0.034591`
- oos.annual_volatility: `0.103559`
- oos.sharpe_ratio: `0.334`
- oos.max_drawdown: `-0.108016`
- oos.annual_turnover: `13.4898`
- oos.excess_cagr: `-0.13951200000000002`
- oos.ir: `-0.8402`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.06356276940306661`
- is.combo.ic_tstat: `1.4841423322398606`
- is.combo.top_bottom_spread: `0.0006210015861745146`
- is.combo.top_decile_excess_return: `0.0009974097668383212`
- is.combo.ic_decay: `{'5': 0.06356276940306661, '21': 0.1270540639781797, '63': 0.026618935871857992}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.010980348942252001`
- oos.combo.ic_tstat: `0.3197259242552339`
- oos.combo.top_bottom_spread: `-9.461351097781586e-05`
- oos.combo.top_decile_excess_return: `0.00018535801961540142`
- oos.combo.ic_decay: `{'5': 0.010980348942252001, '21': 0.09470695854012547, '63': 0.06029794530456148}`

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
        "max_trailing_volatility": 0.25,
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
  "notes": "Slightly looser vol cap may recover alpha lost to over-filtering.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_soft05_h20_top10_vol25",
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
