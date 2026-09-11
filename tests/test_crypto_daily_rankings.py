import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest

from scripts import crypto_daily_rankings as daily


def report():
    return dict(as_of='2026-09-06', selected_count=50, valid_count=50,
                momentum_target_count=49, momentum_valid_count=49,
                rows=[dict(symbol=f'C{i:02}USDT', beta=3-i*.1, status='ok') for i in range(25)],
                momentum_rows=[dict(symbol=f'C{i:02}USDT', score=3-i*.1, rs=.1, status='ok')
                               for i in range(5,25)])


def test_two_top10_with_overlap_shown_three_times():
    data = report()
    beta, rs = daily.top_lists(data)
    assert len(beta) == len(rs) == 10
    assert [r['symbol'] for r in beta] == [f'C{i:02}USDT' for i in range(10)]
    text = daily.message(data)
    assert '双榜同时入选：5 个' in text
    assert 'C05' in text.split('①')[0]
    assert text.count('C05') == 3
    assert 'C24' not in text
    assert len(text) < 4000


def test_beta_threshold_and_rs_is_not_gated_by_beta():
    data=report()
    data['rows']=[dict(symbol='EDGEUSDT',beta=1.,status='ok'),
                  dict(symbol='MISSINGUSDT',beta=None,status='unavailable')]
    beta, rs=daily.top_lists(data)
    assert beta==[] and len(rs)==10
    assert '无共同币种' in daily.message(data)


def test_sparse_results_are_not_padded():
    data=report()
    data['momentum_rows']=data['momentum_rows'][:2]
    data['momentum_valid_count']=2
    text=daily.message(data)
    assert 'RS 4h前十*（实际2个）' in text
    assert '有效2/49' in text
    assert '部分历史不足或数据不可用' in text


def test_symbol_markdown_escape():
    assert daily.symbol_label('A_BUSDT') == 'A\\_B'


def test_4h_history_and_independent_beta_eligibility(tmp_path):
    dates=pd.date_range(end='2026-09-07',periods=188,freq='4h',tz='UTC')
    t=np.arange(188)
    class Market:
        def fetch(self,symbol,limit,interval):
            assert limit==188 and interval=='4h'
            if symbol=='NEWUSDT':
                return pd.DataFrame({'timestamp':dates[-10:],'close':100.})
            closes=100*np.exp(.001*t+.01*np.sin(t))
            if symbol!='BTCUSDT':
                closes*=np.exp(.001*t+.01*np.cos(t))
            return pd.DataFrame({'timestamp':dates,'close':closes})
    data=dict(as_of='2026-09-06', rows=[dict(symbol='BTCUSDT'),dict(symbol='ALTUSDT'),dict(symbol='NEWUSDT')])
    daily.add_momentum(data, Market(), tmp_path)
    assert data['momentum_valid_count']==1
    assert data['momentum_target_count']==2
    assert data['momentum_rows'][0]['observations']==180
    assert data['momentum_rows'][1]['status']=='unavailable'


@pytest.mark.parametrize('dry_run', [True,False])
def test_run_sends_single_message_or_none(monkeypatch,tmp_path,dry_run):
    data=report();sent=[]
    scanner=SimpleNamespace(send_telegram_alert=lambda msg: sent.append(msg) or True)
    monkeypatch.setitem(sys.modules,'binance_pmarp_scanner',scanner)
    monkeypatch.setattr(daily,'scan',lambda *args:data)
    monkeypatch.setattr(daily,'add_momentum',lambda *args:data)
    daily.run(tmp_path,tmp_path,dry_run=dry_run)
    assert len(sent)==(0 if dry_run else 1)
    saved=json.loads(next(tmp_path.glob('*.json')).read_text())
    assert len(saved['overlap'])==5


def test_send_failure_propagates(monkeypatch,tmp_path):
    data=report()
    monkeypatch.setitem(sys.modules,'binance_pmarp_scanner',SimpleNamespace(send_telegram_alert=lambda msg:False))
    monkeypatch.setattr(daily,'scan',lambda *args:data)
    monkeypatch.setattr(daily,'add_momentum',lambda *args:data)
    with pytest.raises(RuntimeError,match='发送失败'):
        daily.run(tmp_path,tmp_path)
