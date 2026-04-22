# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_recent5_h20_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.6309, 'pass': True}`
- oos_sharpe_positive: `{'value': 0.7988, 'pass': True}`
- oos_ic_positive: `{'value': 0.0166380962033136, 'pass': True}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 1.2661277540022189, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 3.9792, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.037268`
- is.annual_volatility: `0.059067`
- is.sharpe_ratio: `0.6309`
- is.max_drawdown: `-0.064954`
- is.annual_turnover: `1.4899`
- is.excess_cagr: `-0.003268`
- is.ir: `-0.0937`
### OOS
- oos.cagr: `0.037924`
- oos.annual_volatility: `0.047474`
- oos.sharpe_ratio: `0.7988`
- oos.max_drawdown: `-0.053825`
- oos.annual_turnover: `3.9792`
- oos.excess_cagr: `-0.136179`
- oos.ir: `-0.7916`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.163665186354262`
- is.combo.ic_tstat: `1.6609050550066675`
- is.combo.top_bottom_spread: `0.00026981301079997684`
- is.combo.top_decile_excess_return: `0.003291089031111839`
- is.combo.ic_decay: `{'5': 0.163665186354262, '21': -0.290794499618029, '63': -0.3801299540795339}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `0.0166380962033136`
- oos.combo.ic_tstat: `0.29899429336253336`
- oos.combo.top_bottom_spread: `-0.0015671193453113698`
- oos.combo.top_decile_excess_return: `-3.3334712147916324e-05`
- oos.combo.ic_decay: `{'5': 0.0166380962033136, '21': 0.0741714806932198, '63': -0.027558977396110948}`

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
  "notes": "Recent peak RVOL may work better than same-day RVOL inside breadth-on conditions.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_recent5_h20_top10_vol20",
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
