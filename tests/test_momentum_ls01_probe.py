import json
from decimal import Decimal
import threading

import pytest

from trading.momentum_ls01.binance import Credentials, Endpoints, ExchangeError
from trading.momentum_ls01.probe import run_probe


class Demo:
    env = 'demo'
    credentials = Credentials('fake-key', 'fake-secret')
    allow_demo_writes = True

    def __init__(self):
        self.positions = {'BTCUSDT': Decimal(0), 'ETHUSDT': Decimal(0)}
        self.orders = {}; self.algos = {}; self.calls = []; self.lock = threading.RLock()
        self.output = None; self.unknown = False; self.unresolvable = False
        self.preexisting = False; self.fail_disk = False

    def sync_clock(self):
        return 0

    def request(self, method, path, params=None, **kwargs):
        p = params or {}
        with self.lock:
            self.calls.append((method, path, p))
            if method != 'GET':
                files = list(self.output.glob('*/g*.json'))
                saved = json.loads(files[0].read_text())
                assert any(i['method'] == method and i['path'] == path and i['params'] == p
                           and i['state'] == 'SENDING' for i in saved['requests'])
            if path == Endpoints.EXCHANGE_INFO:
                return {'symbols': [self.info(x) for x in self.positions]}
            if path == Endpoints.ACCOUNT:
                return {'availableBalance': '10000', 'totalWalletBalance': '10000'}
            if path == Endpoints.POSITION_MODE:
                return {'dualSidePosition': False}
            if path == Endpoints.MULTI_ASSETS_MODE:
                return {'multiAssetsMargin': False}
            if path == Endpoints.SYMBOL_CONFIG:
                return [{'symbol': x, 'leverage': 2, 'marginType': 'CROSSED'} for x in self.positions]
            if path in (Endpoints.LEVERAGE, Endpoints.MARGIN_TYPE):
                return {'leverage': 2, 'code': 200}
            if path == Endpoints.TICKER_PRICE:
                return {'symbol': p['symbol'], 'price': '100', 'time': 1}
            if path == Endpoints.MARK_PRICE:
                return {'symbol': p['symbol'], 'markPrice': '100'}
            if path == Endpoints.POSITION_RISK_V2:
                return [{'symbol': x, 'positionAmt': str(q), 'entryPrice': '100'} for x,q in self.positions.items()]
            if path == Endpoints.OPEN_ORDERS:
                return [{'orderId':999}] if self.preexisting else []
            if path == Endpoints.OPEN_ALGO_ORDERS:
                return [a for a in self.algos.values() if a['algoStatus'] == 'NEW']
            if path == Endpoints.ORDER:
                if method == 'POST':
                    q = Decimal(p['quantity']); symbol=p['symbol']; side=p['side']
                    self.positions[symbol] += q if side == 'BUY' else -q
                    result = {**p, 'clientOrderId':p['newClientOrderId'], 'orderId':len(self.orders)+1, 'status':'FILLED', 'executedQty':str(q), 'avgPrice':'100'}
                    self.orders[p['newClientOrderId']] = result
                    if self.unknown:
                        self.unknown = False
                        raise ExchangeError('UNKNOWN','lost response')
                    return result
                if self.unresolvable:
                    raise ExchangeError('REJECTED','not found',code=-2013)
                return next(o for o in self.orders.values() if o.get('newClientOrderId') == p.get('origClientOrderId') or str(o['orderId']) == str(p.get('orderId')))
            if path == Endpoints.ALGO_ORDER:
                key=p.get('clientAlgoId')
                if method == 'POST':
                    self.algos[key] = {**p, 'algoId':len(self.algos)+1,'algoStatus':'NEW','actualOrderId':'','orderType':p['type'],'closePosition':False}
                if method == 'DELETE':
                    self.algos[key]['algoStatus']='CANCELED'
                return self.algos[key]
            if path == Endpoints.USER_TRADES:
                return [{'orderId':o['orderId'],'qty':o['executedQty'],'price':'100','side':o['side']} for o in self.orders.values() if o['symbol']==p['symbol']]
            if path == Endpoints.INCOME:
                return []
            raise AssertionError((method,path,p))

    @staticmethod
    def info(symbol):
        return {'symbol':symbol,'status':'TRADING','contractType':'PERPETUAL','quoteAsset':'USDT','deliveryDate':9999999999999,'filters':[
            {'filterType':'PRICE_FILTER','minPrice':'0.1','maxPrice':'1000000','tickSize':'0.1'},
            {'filterType':'LOT_SIZE','minQty':'0.01','maxQty':'100','stepSize':'0.01'},
            {'filterType':'MARKET_LOT_SIZE','minQty':'0.01','maxQty':'100','stepSize':'0.01'},
            {'filterType':'MIN_NOTIONAL','notional':'5'},
            {'filterType':'PERCENT_PRICE','multiplierUp':'1.1','multiplierDown':'0.9'}]}


def execute(tmp_path, group='g1', **kwargs):
    c=Demo(); c.output=tmp_path
    result=run_probe(group,c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200',replacement_trigger_price='210',**kwargs)
    return c,result


def test_prod_never_makes_even_read_request(tmp_path):
    c=Demo(); c.env='prod'
    with pytest.raises(ValueError,match='demo'):
        run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1')
    assert not c.calls


def test_preexisting_exposure_blocks_without_touching_it(tmp_path):
    c=Demo(); c.output=tmp_path;c.positions['ETHUSDT']=Decimal('1')
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['status']=='BLOCKED'
    assert not any(m!='GET' for m,_,_ in c.calls)
    assert c.positions['ETHUSDT']==1


def test_preexisting_orders_block_without_cancel_all(tmp_path):
    c=Demo(); c.output=tmp_path;c.preexisting=True
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['status']=='BLOCKED'
    assert not any(m!='GET' for m,_,_ in c.calls)


@pytest.mark.parametrize('group',['g1','g2','g3','g4'])
def test_bounded_probe_cleans_only_owned_exposure_and_never_claims_full_pass(tmp_path,group):
    c,result=execute(tmp_path,group)
    assert result['status']=='PARTIAL'
    assert result['cleanup']['status']=='PASS'
    assert all(q==0 for q in c.positions.values())
    assert all(a['algoStatus']=='CANCELED' for a in c.algos.values())
    assert any(v['status']=='NOT_OBSERVED' for v in result['checks'].values())
    saved=json.loads(next(tmp_path.glob('*/g*.json')).read_text())
    assert saved==result
    assert 'fake-secret' not in json.dumps(saved) and 'fake-key' not in json.dumps(saved)


def test_unknown_accepted_order_is_queried_then_cleaned_not_reopened(tmp_path):
    c=Demo();c.output=tmp_path;c.unknown=True
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    writes=[p for m,path,p in c.calls if m=='POST' and path==Endpoints.ORDER]
    assert len(writes)==2 and writes[0]['side']=='SELL' and writes[1]['reduceOnly'] is True
    assert result['status']=='BLOCKED' and result['cleanup']['status']=='PASS'


def test_unknown_unresolved_effect_never_blindly_flattens(tmp_path):
    c=Demo();c.output=tmp_path;c.unknown=True;c.unresolvable=True
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    writes=[p for m,path,p in c.calls if m=='POST' and path==Endpoints.ORDER]
    assert len(writes)==1
    assert result['cleanup']['status']=='BLOCKED'
    assert c.positions['BTCUSDT']==-1


def test_g5_requires_two_symbols_before_any_write(tmp_path):
    c,result=execute(tmp_path,'g5')
    assert result['status']=='BLOCKED'
    assert not any(m!='GET' for m,_,_ in c.calls)


def test_g5_two_symbol_waves_and_cleanup(tmp_path):
    c=Demo();c.output=tmp_path
    result=run_probe('g5',c,tmp_path,symbol='BTCUSDT',quantity='1',second_symbol='ETHUSDT',second_quantity='1',worker_factory=lambda:c)
    assert result['status']=='PARTIAL'
    assert result['checks']['two_waves']['status']=='PASS'
    assert all(q==0 for q in c.positions.values())


def test_persistence_failure_prevents_first_write(tmp_path,monkeypatch):
    import trading.momentum_ls01.probe as probe
    original=probe._persist
    def fail_intent(path,evidence):
        if evidence['requests']:
            raise OSError('disk unavailable')
        original(path,evidence)
    monkeypatch.setattr(probe,'_persist',fail_intent)
    c=Demo();c.output=tmp_path
    with pytest.raises(OSError):
        run_probe('g4',c,tmp_path,symbol='BTCUSDT',quantity='1')
    assert not any(m!='GET' for m,_,_ in c.calls)


def test_crash_leaves_durable_sending_identity_without_replay(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    def crash(method,path,params=None,**kwargs):
        if method=='POST' and path==Endpoints.ORDER:
            raise KeyboardInterrupt()
        return original(method,path,params,**kwargs)
    c.request=crash
    with pytest.raises(KeyboardInterrupt):
        run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    saved=json.loads(next(tmp_path.glob('*/g*.json')).read_text())
    last=saved['requests'][-1]
    assert last['state']=='SENDING' and last['params']['newClientOrderId']
    assert saved['status']=='RUNNING'


def test_nonterminal_trigger_child_prevents_additional_market_close(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    triggered=[False]
    def dispatch(method,path,params=None,**kwargs):
        p=params or {}
        if path==Endpoints.ALGO_ORDER and method=='GET' and c.algos:
            a=next(iter(c.algos.values()));a.update(algoStatus='FINISHED',actualOrderId='999')
            triggered[0]=True
            return dict(a)
        if path==Endpoints.ORDER and method=='GET' and p.get('orderId')=='999':
            return {'symbol':'BTCUSDT','side':'BUY','orderId':999,'status':'PARTIALLY_FILLED','executedQty':'0.1'}
        return original(method,path,params,**kwargs)
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert triggered[0] and result['cleanup']['status']=='BLOCKED'
    assert len([1 for m,p,_ in c.calls if m=='POST' and p==Endpoints.ORDER])==1


def test_external_roundtrip_trade_blocks_owned_position_cleanup(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    def dispatch(method,path,params=None,**kwargs):
        if path==Endpoints.USER_TRADES:
            return [{'orderId':9999,'qty':'1','side':'BUY'}]
        return original(method,path,params,**kwargs)
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['cleanup']['status']=='BLOCKED'
    assert c.positions['BTCUSDT']==-1
    assert all(a['algoStatus']=='NEW' for a in c.algos.values())
    assert not any(m=='DELETE' for m,_,_ in c.calls)


def test_wrong_order_identity_blocks_followup_and_cleanup(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    def dispatch(method,path,params=None,**kwargs):
        reply=original(method,path,params,**kwargs)
        if method=='GET' and path==Endpoints.ORDER:
            return {**reply,'clientOrderId':'foreign'}
        return reply
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['cleanup']['status']=='BLOCKED'
    assert not c.algos
    assert c.positions['BTCUSDT']==-1


def test_g5_batch_intents_all_durable_and_network_calls_overlap(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    barrier=threading.Barrier(2)
    visits=[]
    def dispatch(method,path,params=None,**kwargs):
        if method=='POST' and path==Endpoints.ORDER and not params['reduceOnly'] and len(c.orders)<2:
            saved=json.loads(next(tmp_path.glob('*/g5.json')).read_text())
            seed=[x for x in saved['requests'] if x['purpose']=='seed paired positions']
            assert len(seed)==2 and all(x['state']=='SENDING' for x in seed)
            visits.append(params['symbol'])
            barrier.wait(timeout=2)
        return original(method,path,params,**kwargs)
    c.request=dispatch
    result=run_probe('g5',c,tmp_path,symbol='BTCUSDT',quantity='1',second_symbol='ETHUSDT',second_quantity='1',worker_factory=lambda:c)
    assert set(visits)=={'BTCUSDT','ETHUSDT'}
    assert result['status']=='PARTIAL'


def test_trade_history_timeout_preserves_owned_protection(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    def dispatch(method,path,params=None,**kwargs):
        if path==Endpoints.USER_TRADES:
            raise ExchangeError('UNKNOWN','read timeout')
        return original(method,path,params,**kwargs)
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['cleanup']['status']=='BLOCKED'
    assert c.positions['BTCUSDT']==-1
    assert all(a['algoStatus']=='NEW' for a in c.algos.values())
    assert not any(m=='DELETE' for m,_,_ in c.calls)


@pytest.mark.parametrize('outcome',['REJECTED','UNKNOWN'])
def test_unconfirmed_reduce_only_close_keeps_stop(tmp_path,outcome):
    c=Demo();c.output=tmp_path
    original=c.request
    def dispatch(method,path,params=None,**kwargs):
        if method=='POST' and path==Endpoints.ORDER and params.get('reduceOnly'):
            raise ExchangeError(outcome,'close conflict or response lost',code=-2022 if outcome=='REJECTED' else None)
        return original(method,path,params,**kwargs)
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['cleanup']['status']=='BLOCKED'
    assert c.positions['BTCUSDT']==-1
    assert all(a['algoStatus']=='NEW' for a in c.algos.values())


def test_successful_cleanup_cancels_protection_only_after_explicit_zero(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    zero_observed=[False]
    def dispatch(method,path,params=None,**kwargs):
        if path==Endpoints.POSITION_RISK_V2 and c.orders and c.positions['BTCUSDT']==0:
            zero_observed[0]=True
        if method=='DELETE' and path==Endpoints.ALGO_ORDER:
            assert zero_observed[0]
            assert c.positions['BTCUSDT']==0
        return original(method,path,params,**kwargs)
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['cleanup']['status']=='PASS'


def test_unowned_conditional_during_cleanup_blocks_close_and_preserves_owned_stop(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    def dispatch(method,path,params=None,**kwargs):
        reply=original(method,path,params,**kwargs)
        if path==Endpoints.OPEN_ALGO_ORDERS and c.algos:
            return reply+[{'clientAlgoId':'foreign','symbol':'BTCUSDT','algoStatus':'NEW','actualOrderId':''}]
        return reply
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['cleanup']['status']=='BLOCKED'
    assert c.positions['BTCUSDT']==-1
    assert all(a['algoStatus']=='NEW' for a in c.algos.values())


def test_stop_triggering_during_attribution_prevents_extra_close(tmp_path):
    c=Demo();c.output=tmp_path
    original=c.request
    queried=[0]
    def dispatch(method,path,params=None,**kwargs):
        reply=original(method,path,params,**kwargs)
        if method=='GET' and path==Endpoints.ALGO_ORDER:
            queried[0]+=1
            if queried[0]>=3:  # setup, cleanup snapshot, pre-close recheck
                reply.update(algoStatus='FINISHED',actualOrderId='999')
        return reply
    c.request=dispatch
    result=run_probe('g1',c,tmp_path,symbol='BTCUSDT',quantity='1',trigger_price='200')
    assert result['cleanup']['status']=='BLOCKED'
    assert c.positions['BTCUSDT']==-1
    assert len([1 for m,p,_ in c.calls if m=='POST' and p==Endpoints.ORDER])==1
