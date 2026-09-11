import numpy as np
import pandas as pd
import pytest

from scripts.compare_crypto_relative_momentum import window, score, overlap

END = pd.Timestamp('2026-09-07', tz='UTC')


def test_180_returns_need_181_closed_prices():
    dates = pd.date_range(end=END, periods=225, freq='4h')
    s = pd.Series(np.arange(225)+100., index=dates)
    actual = window(s, END, '4h')
    assert len(actual) == 181
    assert actual.index[-1] == END-pd.Timedelta(hours=4)
    assert actual.index[-1]-actual.index[0] == pd.Timedelta(days=30)
    with pytest.raises(ValueError):
        window(s.drop(actual.index[10]), END, '4h')


def test_daily_and_4h_same_endpoints_give_same_rs():
    dates = pd.date_range(end=END, periods=225, freq='4h')
    t=np.arange(225)
    btc=pd.Series(100*np.exp(.001*t+.01*np.sin(t)),index=dates)
    coin=pd.Series(100*np.exp(.002*t+.02*np.cos(t)),index=dates)
    a=score(window(coin,END,'4h'),window(btc,END,'4h'))
    def daily(series):
        s=series[series.index.hour==20].copy()
        s.index=s.index.normalize()
        return s
    b=score(window(daily(coin),END,'1d'),window(daily(btc),END,'1d'))
    assert a['rs'] == pytest.approx(b['rs'])
    assert a['return'] == pytest.approx(b['return'])


def test_score_matches_explicit_excess_return_formula():
    x=np.array([.01,-.02,.03,-.01]*8)[:30]
    active=np.array([.02,.01,.04,-.02]*8)[:30]
    dates=pd.date_range(end=END,periods=31)
    btc=pd.Series(100*np.cumprod(np.r_[1,1+x]),index=dates)
    coin=pd.Series(100*np.cumprod(np.r_[1,1+x+active]),index=dates)
    assert score(coin,btc)['score'] == pytest.approx(active.mean()/active.std(ddof=1))
    with pytest.raises(ValueError,match='为零'):
        score(btc,btc)


def test_invalid_price_and_duplicate_bar_rejected():
    dates=pd.date_range(end=END-pd.Timedelta(days=1),periods=31)
    s=pd.Series(100.,index=dates)
    with pytest.raises(ValueError):
        window(pd.concat([s,s.iloc[[5]]]).sort_index(),END,'1d')
    s.iloc[5]=0
    with pytest.raises(ValueError):
        window(s,END,'1d')
