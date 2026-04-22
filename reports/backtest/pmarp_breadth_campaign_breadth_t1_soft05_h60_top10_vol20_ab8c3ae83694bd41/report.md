# Backtest Pipeline Report — pmarp_breadth_campaign_breadth_t1_soft05_h60_top10_vol20

## Summary
- Benchmark: `SPY`
- Rebalance: `daily`
- Factors: PMARP_Rebound_V1
- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)

## Key Gates
- is_sharpe_positive: `{'value': 0.2267, 'pass': True}`
- oos_sharpe_positive: `{'value': 1.3431, 'pass': True}`
- oos_ic_positive: `{'value': -0.037184563664164436, 'pass': False}`
- oos_vs_is_sharpe_ratio_gte_0_5: `{'value': 5.924569916188795, 'threshold': 0.5, 'pass': True}`
- annual_turnover_within_limit: `{'value': 6.0861, 'threshold': 20.0, 'pass': True}`

## Strategy Metrics
### IS
- is.cagr: `0.018275`
- is.annual_volatility: `0.08061`
- is.sharpe_ratio: `0.2267`
- is.max_drawdown: `-0.080383`
- is.annual_turnover: `2.3659`
- is.excess_cagr: `-0.022261000000000003`
- is.ir: `-0.1919`
### OOS
- oos.cagr: `0.120554`
- oos.annual_volatility: `0.089756`
- oos.sharpe_ratio: `1.3431`
- oos.max_drawdown: `-0.084098`
- oos.annual_turnover: `6.0861`
- oos.excess_cagr: `-0.05354900000000001`
- oos.ir: `-0.3461`

## Factor Metrics
### IS Combo
- is.combo.primary_horizon: `5`
- is.combo.ic_mean: `0.006499990468958987`
- is.combo.ic_tstat: `0.15718066715019757`
- is.combo.top_bottom_spread: `-0.0006757316562448055`
- is.combo.top_decile_excess_return: `0.0018867851822985445`
- is.combo.ic_decay: `{'5': 0.006499990468958987, '21': -0.04923054631179238, '63': -0.03409465985789649}`
### OOS Combo
- oos.combo.primary_horizon: `5`
- oos.combo.ic_mean: `-0.037184563664164436`
- oos.combo.ic_tstat: `-1.2485517075766503`
- oos.combo.top_bottom_spread: `-0.00362644362121503`
- oos.combo.top_decile_excess_return: `-0.0034538001280524687`
- oos.combo.ic_decay: `{'5': -0.037184563664164436, '21': -0.050023221288054656, '63': -0.06939505525122243}`

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
        "holding_window_days": 60,
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
  "notes": "Stress the 60-day carry thesis under breadth gating.",
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
  "spec_id": "pmarp_breadth_campaign_breadth_t1_soft05_h60_top10_vol20",
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
