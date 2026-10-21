import numpy as np
import pandas as pd
from src.indicators.pmarp import calculate_pmarp,analyze_pmarp
from src.timing.dual_engine import DualEngineConfig
from scripts import crypto_pmarp_breadth as breadth


def prices(n=360):
 return pd.Series(100*np.exp(np.random.default_rng(59).normal(0,.03,n).cumsum()))

def test_default_scores_and_analysis_use_30_120():
 p=prices();expected=calculate_pmarp(p,ema_period=30,lookback=120)
 pd.testing.assert_series_equal(calculate_pmarp(p),expected)
 assert analyze_pmarp(pd.DataFrame({'close':p}))==analyze_pmarp(pd.DataFrame({'close':p}),30,120)
 assert not expected.equals(calculate_pmarp(p,20,150)) # historical explicit override is still possible

def test_default_warmup_is_30_plus_120():
 assert calculate_pmarp(prices(149)).isna().all()
 assert calculate_pmarp(prices(150)).iloc[120:].notna().all()

def test_timing_and_breadth_defaults_agree_without_changing_other_indicators():
 c=DualEngineConfig()
 assert (c.pmarp_ema_period,c.pmarp_lookback)==(30,120)
 assert (c.ema_period,c.bbwp_lookback,c.rvol_window)==(144,252,20)
 assert (breadth.EMA_PERIOD,breadth.PMARP_LOOKBACK)==(30,120)

def test_breadth_message_labels_the_recorded_parameters_even_for_history():
 report={'as_of':'2026-10-20','current':{'valid_count':1,'eligible_count':1,'warmup_count':0,'strong_count':1,'weak_count':0,'strong_pct':100,'weak_pct':0},'strong_percentile':100,'weak_percentile':0,'comparison_start':'2025-10-20','comparison_end':'2026-10-19','parameters':{'ema_period':30,'pmarp_lookback':120}}
 assert 'EMA30 / PMARP120' in breadth.message(report)
 report['parameters']={'ema_period':20,'pmarp_lookback':150}
 assert 'EMA20 / PMARP150' in breadth.message(report)
