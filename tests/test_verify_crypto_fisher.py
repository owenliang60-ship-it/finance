import json
import pandas as pd
import pytest
from scripts.verify_crypto_fisher import verify_strategy, verify_regimes, settlement_price


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture_files(tmp_path):
    raw=tmp_path/'raw'; strategy=tmp_path/'strategy'; strategy.mkdir()
    times=pd.date_range('2025-01-01',periods=3,freq='4h',tz='UTC')
    put(raw/'four_hour'/'X.json', {'rows':[[int(t.timestamp()*1000),p,p,p,p] for t,p in zip(times,[10,12,11])]})
    put(raw/'funding'/'X.json', {'rows':[{'fundingTime':int(times[1].timestamp()*1000)+1,'fundingRate':'.01','markPrice':'12'}]})
    pd.DataFrame([dict(time=times[0],symbol='X',delta_units=.1,price=10,notional=1,cost=.001,units_after=.1),dict(time=times[1],symbol='X',delta_units=-.1,price=12,notional=1.2,cost=.0012,units_after=0)]).to_csv(strategy/'ledger.csv',index=False)
    pd.DataFrame([dict(time=times[1],symbol='X',units=.1,rate=.01,mark=12,cashflow=-.012)]).to_csv(strategy/'funding.csv',index=False)
    pd.DataFrame({'time':times,'nav':[.999,1.1858,1.1858],'gross':[1/.999,0,0],'net':[1/.999,0,0],'funding':[0,-.012,0],'turnover':[1,1.2,0]}).to_csv(strategy/'nav.csv',index=False)
    return raw,strategy


def test_cash_reconstruction_and_funding_before_same_bar_exit(tmp_path):
    raw,strategy=fixture_files(tmp_path)
    result=verify_strategy(raw,strategy)
    assert result['status']=='pass',result
    assert result['counts']['nav']==3
    assert result['counts']['raw_funding_held']==1
    assert result['max_errors']['nav']<1e-12


@pytest.mark.parametrize('file,column', [('ledger.csv','price'),('ledger.csv','cost'),('funding.csv','cashflow'),('nav.csv','nav')])
def test_tampering_fails_independent_source_check(tmp_path,file,column):
    raw,strategy=fixture_files(tmp_path)
    frame=pd.read_csv(strategy/file);frame[column]=frame[column].astype(float);frame.loc[0,column]+=.1;frame.to_csv(strategy/file,index=False)
    assert verify_strategy(raw,strategy)['status']=='fail'


def test_omitted_funding_fails_even_when_accounting_matches(tmp_path):
    raw,strategy=fixture_files(tmp_path)
    funds=pd.read_csv(strategy/'funding.csv');funds.iloc[:0].to_csv(strategy/'funding.csv',index=False)
    nav=pd.read_csv(strategy/'nav.csv');nav.loc[1:,'nav']+=.012;nav.loc[1,'funding']=0;nav.to_csv(strategy/'nav.csv',index=False)
    assert verify_strategy(raw,strategy)['status']=='fail'


def test_settlement_uses_historical_window_and_booking(tmp_path):
    delivery=pd.Timestamp('2024-03-26 09:00',tz='UTC')
    rows=[[int(t.timestamp()*1000),10,12,8,10] for t in pd.date_range(delivery-pd.Timedelta(hours=1),periods=60,freq='min')]
    put(tmp_path/'X.json',dict(delivery=int(delivery.timestamp()*1000),rows=rows))
    assert settlement_price(tmp_path,'X',delivery.ceil('4h'))==10
    assert settlement_price(tmp_path,'X',delivery.floor('4h')) is None
    put(tmp_path/'X.json',dict(delivery=int(delivery.timestamp()*1000),rows=rows[30:]))
    with pytest.raises(ValueError,match='window'):
        settlement_price(tmp_path,'X',delivery.ceil('4h'))


def test_independent_fisher_and_future_week_causality(tmp_path):
    starts=pd.date_range('2024-01-01',periods=10,freq='7D',tz='UTC')
    rows=[[int(t.timestamp()*1000),i+1,i+2,i+1,i+1] for i,t in enumerate(starts)]
    put(tmp_path/'btc_weekly.json',dict(rows=rows))
    first=.5*__import__('math').log(1.33/.67)
    date=starts[8]+pd.Timedelta(days=7,hours=4)
    frame=pd.DataFrame([dict(time=date,fisher=first,trigger=0.,regime=1,week_end=starts[9],available_at=date)]).set_index('time')
    target=tmp_path/'regimes.parquet';frame.to_parquet(target)
    assert verify_regimes(tmp_path,target)['status']=='pass'
    rows[-1][2]=1000000
    put(tmp_path/'btc_weekly.json',dict(rows=rows))
    assert verify_regimes(tmp_path,target)['status']=='pass'
    frame['fisher']=0;frame.to_parquet(target)
    assert verify_regimes(tmp_path,target)['status']=='fail'


def test_short_funding_receipt_and_negative_inventory(tmp_path):
    raw,strategy=fixture_files(tmp_path)
    trades=pd.read_csv(strategy/'ledger.csv')
    trades['delta_units']*=-1;trades['units_after']*=-1;trades.to_csv(strategy/'ledger.csv',index=False)
    funds=pd.read_csv(strategy/'funding.csv');funds['units']*=-1;funds['cashflow']*=-1;funds.to_csv(strategy/'funding.csv',index=False)
    nav=pd.read_csv(strategy/'nav.csv');nav['nav']=[.999,.8098,.8098];nav['net']*=-1;nav['funding']*=-1;nav.to_csv(strategy/'nav.csv',index=False)
    assert verify_strategy(raw,strategy)['status']=='pass'


def test_settlement_replaces_missing_terminal_raw_open(tmp_path):
    raw,strategy=fixture_files(tmp_path)
    times=pd.date_range('2025-01-01',periods=3,freq='4h',tz='UTC')
    # First mark 10, delivery at 01:00, booking 04:00 at bounded mean 12.
    put(raw/'four_hour'/'X.json',{'rows':[[int(times[0].timestamp()*1000),10,10,10,10]]})
    settlement=tmp_path/'settlement'
    delivery=times[0]+pd.Timedelta(hours=1)
    source=[[int(t.timestamp()*1000),12,13,11,12] for t in pd.date_range(delivery-pd.Timedelta(minutes=30),periods=30,freq='min')]
    put(settlement/'X.json',dict(delivery=int(delivery.timestamp()*1000),rows=source))
    # Raw post-delivery funding padding is excluded, as the contract is dead.
    funds=pd.read_csv(strategy/'funding.csv');funds.iloc[:0].to_csv(strategy/'funding.csv',index=False)
    nav=pd.read_csv(strategy/'nav.csv');nav.loc[1:,'nav']+=.012;nav.loc[1,'funding']=0;nav.to_csv(strategy/'nav.csv',index=False)
    assert verify_strategy(raw,strategy,settlements=settlement)['status']=='pass'
    assert verify_strategy(raw,strategy)['status']=='fail'


def test_missing_held_intermediate_open_fails(tmp_path):
    raw,strategy=fixture_files(tmp_path)
    prices=json.loads((raw/'four_hour'/'X.json').read_text());prices['rows']=prices['rows'][:1]
    put(raw/'four_hour'/'X.json',prices)
    assert verify_strategy(raw,strategy)['status']=='fail'


def test_explicit_no_funding_verifies_without_any_funding_source(tmp_path):
    raw,strategy=fixture_files(tmp_path)
    assert verify_strategy(raw,strategy,funding_mode='exclude')['status']=='fail'
    funds=pd.read_csv(strategy/'funding.csv');funds.iloc[:0].to_csv(strategy/'funding.csv',index=False)
    nav=pd.read_csv(strategy/'nav.csv');nav.loc[1:,'nav']+=.012;nav.loc[1,'funding']=0;nav.to_csv(strategy/'nav.csv',index=False)
    (raw/'funding'/'X.json').unlink()
    assert verify_strategy(raw,strategy,funding_mode='exclude')['status']=='pass'


def test_csv_roundoff_close_does_not_require_post_delisting_prices(tmp_path):
    raw=tmp_path/'raw'; strategy=tmp_path/'strategy';strategy.mkdir()
    times=pd.date_range('2025-01-01',periods=5,freq='4h',tz='UTC')
    delta=[-103.53163020800989, -5.63531801371343, 0.6895250772603276, 108.477423144463]
    positions=[-103.53163020800989, -109.16694822172332, -108.477423144463, 0.0]
    put(raw/'four_hour'/'X.json',{'rows':[[int(t.timestamp()*1000),.000225] for t in times[:4]]})
    pd.DataFrame([dict(time=t,symbol='X',delta_units=d,price=.000225,notional=abs(d)*.000225,cost=0.,units_after=q) for t,d,q in zip(times,delta,positions)]).to_csv(strategy/'ledger.csv',index=False)
    pd.DataFrame(columns=['time','symbol','units','rate','mark','cashflow']).to_csv(strategy/'funding.csv',index=False)
    pd.DataFrame({'time':times,'nav':[1.]*5,'funding':[0.]*5}).to_csv(strategy/'nav.csv',index=False)
    result=verify_strategy(raw,strategy,cost=0.,funding_mode='exclude')
    assert result['status']=='pass',result
