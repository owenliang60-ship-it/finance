"""Bounded demo experiments. Full protocol gates require additional observations.

Every mutation has a durable intent. Uncertain effects are queried, never replayed.
No run adopts preexisting exposure. No cancel-all endpoint is used.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from .binance import BinanceClient, Endpoints as E, ExchangeError
from .exchange_rules import SymbolRules, RuleViolation


TERMINAL = {'FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH', 'REJECTED'}
ALGO_TERMINAL = {'CANCELED', 'FINISHED', 'EXPIRED', 'REJECTED'}


def _persist(path, evidence):
    """Atomic replacement plus file and directory fsync, before returning."""
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(evidence, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except Exception:
        raise _Stop('BLOCKED', 'invalid numeric exchange field') from None


class _Stop(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


class _Probe:
    def __init__(self, group, client, output_dir, symbols, quantities, worker_factory):
        self.client = client
        self.symbols = symbols
        self.quantities = quantities
        self.worker_factory = worker_factory
        self.rules = {}
        self.lock = threading.RLock()
        self.run_id = uuid.uuid4().hex
        self.path = Path(output_dir) / self.run_id / f'{group}.json'
        self.path.parent.mkdir(parents=True, mode=0o700)
        credentials = getattr(client, 'credentials', None)
        self.secrets = [credentials.api_key, credentials.api_secret] if credentials else []
        fingerprint = hashlib.sha256(credentials.api_key.encode()).hexdigest() if credentials else None
        self.evidence = dict(schema=1, run_id=self.run_id, group=group, env='demo',
                             started_at=datetime.now(timezone.utc).isoformat(), started_ms=int(datetime.now(timezone.utc).timestamp()*1000), status='RUNNING',
                             credential_fingerprint=fingerprint, symbols=symbols,
                             requests=[], reads=[], checks={}, cleanup={'status':'NOT_STARTED'})
        self.save()

    def scrub(self, value):
        if isinstance(value, dict):
            return {k: self.scrub(v) for k,v in value.items()
                    if k.lower() not in {'signature', 'api_key', 'api_secret', 'apikey', 'secret', 'x-mbx-apikey'}}
        if isinstance(value, list):
            return [self.scrub(x) for x in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, '<redacted>')
        return value

    def save(self):
        _persist(self.path, self.evidence)

    def check(self, name, status, detail):
        self.evidence['checks'][name] = {'status':status, 'detail':detail}
        self.save()

    def read(self, path, params=None, client=None):
        target = client or self.client
        signed = path not in {E.EXCHANGE_INFO, E.TICKER_PRICE, E.MARK_PRICE}
        if signed:
            target.sync_clock()
        result = target.request('GET', path, params, signed=signed, protective=True)
        self.evidence['reads'].append({'path':path, 'params':params or {}, 'response':self.scrub(result)})
        self.save()
        return result

    def plan(self, method, path, params, purpose):
        with self.lock:
            event = {'sequence':len(self.evidence['requests'])+1,'method':method,'path':path,
                     'params':params, 'purpose':purpose, 'state':'SENDING'}
            self.evidence['requests'].append(event)
            self.save()  # If this fails, no HTTP mutation follows.
            return event

    def send(self, event, client=None):
        target = client or self.client
        try:
            target.sync_clock()
            response = target.request(event['method'], event['path'], event['params'], signed=True,
                                      protective=event['purpose'].startswith('cleanup') or event['params'].get('reduceOnly', False))
        except ExchangeError as exc:
            with self.lock:
                event.update(state=exc.outcome, error={'code':exc.code,'http_status':exc.http_status})
                self.save()
            raise
        with self.lock:
            event.update(state='ACKED', response=self.scrub(response))
            self.save()
        return response

    def write(self, method, path, params, purpose):
        return self.send(self.plan(method, path, params, purpose))

    def order_params(self, symbol, side, quantity, reduce_only=False):
        return {'symbol':symbol, 'side':side, 'type':'MARKET', 'positionSide':'BOTH',
                'quantity':str(quantity), 'reduceOnly':reduce_only,
                'newClientOrderId':f'mlspr-{self.run_id[:12]}-{uuid.uuid4().hex[:8]}'}

    def order(self, symbol, side, quantity, reduce_only=False, purpose='experiment'):
        params = self.order_params(symbol, side, quantity, reduce_only)
        self.write('POST', E.ORDER, params, purpose)
        result = self.read(E.ORDER, {'symbol':symbol,'origClientOrderId':params['newClientOrderId']})
        self.require_order_identity(result,params)
        if result.get('status') != 'FILLED' or _decimal(result.get('executedQty')) != quantity:
            raise _Stop('BLOCKED', 'market order not fully confirmed; cleanup must reconcile')
        return result

    @staticmethod
    def require_order_identity(reply, params):
        if (reply.get('symbol')!=params['symbol']
                or reply.get('clientOrderId')!=params['newClientOrderId']):
            raise _Stop('BLOCKED','order identity mismatch')

    def protection(self, symbol, quantity, trigger):
        params = {'symbol':symbol,'side':'BUY','positionSide':'BOTH','algoType':'CONDITIONAL',
                  'type':'STOP_MARKET','quantity':str(quantity),'triggerPrice':str(trigger),
                  'reduceOnly':True,'workingType':'CONTRACT_PRICE','priceProtect':False,
                  'clientAlgoId':f'mlspr-{self.run_id[:12]}-{uuid.uuid4().hex[:8]}'}
        self.write('POST', E.ALGO_ORDER, params, 'protection')
        actual = self.read(E.ALGO_ORDER, {'clientAlgoId':params['clientAlgoId']})
        if actual.get('clientAlgoId')!=params['clientAlgoId']:
            raise _Stop('BLOCKED','conditional order identity mismatch')
        exact = ('symbol','side','positionSide','quantity','triggerPrice','workingType')
        if any(str(actual.get(k)) != str(params[k]) for k in exact):
            # Decimal strings may have differing trailing zeroes.
            if any(str(actual.get(k)) != str(params[k]) for k in exact if k not in {'quantity','triggerPrice'}) or any(
                _decimal(actual.get(k)) != _decimal(params[k]) for k in ('quantity','triggerPrice')):
                raise _Stop('FAIL', 'protection readback differs from request')
        if (actual.get('reduceOnly') is not True or actual.get('priceProtect') is not False
                or actual.get('closePosition') is not False or actual.get('algoType')!='CONDITIONAL'
                or actual.get('orderType')!='STOP_MARKET'):
            raise _Stop('FAIL', 'protection flags not confirmed')
        if actual.get('algoStatus') != 'NEW' or actual.get('actualOrderId') not in ('', None, 0, '0'):
            raise _Stop('BLOCKED', 'protection triggered during setup; reconcile child')
        return params['clientAlgoId']

    def positions(self):
        rows = self.read(E.POSITION_RISK_V2)
        if not isinstance(rows, list):
            raise _Stop('BLOCKED', 'position response is not a list')
        result = {}
        for row in rows:
            symbol = row.get('symbol')
            if not symbol or symbol in result:
                raise _Stop('BLOCKED', 'ambiguous position identity')
            result[symbol] = row
            _decimal(row.get('positionAmt'))
        return result

    def preflight(self):
        info = self.read(E.EXCHANGE_INFO)
        for symbol, quantity in zip(self.symbols,self.quantities):
            rows = [r for r in info.get('symbols',[]) if r.get('symbol') == symbol]
            if len(rows) != 1:
                raise _Stop('BLOCKED', 'symbol unavailable or duplicated')
            self.rules[symbol] = SymbolRules.from_exchange_info(rows[0])
            mark = _decimal(self.read(E.MARK_PRICE, {'symbol':symbol})['markPrice'])
            self.rules[symbol].validate_order(quantity, mark_price=mark,
                                               now_ms=int(datetime.now(timezone.utc).timestamp()*1000))
        self.read(E.ACCOUNT)
        if self.read(E.POSITION_MODE).get('dualSidePosition') is not False:
            raise _Stop('BLOCKED','account must use one-way positions')
        if self.read(E.MULTI_ASSETS_MODE).get('multiAssetsMargin') is not False:
            raise _Stop('BLOCKED','account must use single-asset margin')
        initial_positions=self.positions()
        if any(s not in initial_positions for s in self.symbols):
            raise _Stop('BLOCKED','explicit initial zero positions missing')
        if any(_decimal(r['positionAmt']) != 0 for r in initial_positions.values()):
            raise _Stop('BLOCKED','preexisting exposure; no adoption or cleanup')
        if self.read(E.OPEN_ORDERS) != [] or self.read(E.OPEN_ALGO_ORDERS) != []:
            raise _Stop('BLOCKED','preexisting orders; no adoption or cleanup')
        self.check('empty_demo_preflight','PASS','empty positions/orders; one-way, single-asset; key fingerprint recorded (not an exchange UID)')
        for symbol in self.symbols:
            current = self.read(E.SYMBOL_CONFIG, {'symbol':symbol})
            rows = [r for r in current if r.get('symbol')==symbol]
            if len(rows)!=1:
                raise _Stop('BLOCKED','symbol configuration missing')
            self.write('POST',E.LEVERAGE,{'symbol':symbol,'leverage':2},'configuration')
            if rows[0].get('marginType')!='CROSSED':
                self.write('POST',E.MARGIN_TYPE,{'symbol':symbol,'marginType':'CROSSED'},'configuration')
            result = self.read(E.SYMBOL_CONFIG,{'symbol':symbol})
            rows = [r for r in result if r.get('symbol')==symbol]
            if len(rows)!=1 or str(rows[0].get('leverage'))!='2' or rows[0].get('marginType')!='CROSSED':
                raise _Stop('FAIL','2x crossed setup readback failed')
        self.check('account_setup','PASS','2x crossed symbol configuration read back')

    def batch(self, items, purpose):
        events = [self.plan('POST',E.ORDER,self.order_params(*item),purpose) for item in items]
        # All intents are durable before any worker starts.
        def worker(event):
            target = self.worker_factory() if self.worker_factory else BinanceClient(
                env='demo',credentials=self.client.credentials,limiter=self.client.limiter,allow_demo_writes=True)
            if target.env!='demo':
                raise _Stop('BLOCKED','worker environment must be demo')
            self.send(event,target)
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(worker,event) for event in events]
            errors = []
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise errors[0]
        for event in events:
            p=event['params']
            reply=self.read(E.ORDER,{'symbol':p['symbol'],'origClientOrderId':p['newClientOrderId']})
            self.require_order_identity(reply,p)
            if reply.get('status')!='FILLED' or _decimal(reply.get('executedQty'))!=_decimal(p['quantity']):
                raise _Stop('BLOCKED','batch not fully filled; no batch replay')

    def cleanup(self):
        """Reconcile identities first; close only exactly attributed residual exposure."""
        if not any(e['path'] in (E.ORDER,E.ALGO_ORDER) and e['method']=='POST' for e in self.evidence['requests']):
            self.evidence['cleanup']={'status':'PASS','detail':'no probe-owned orders or exposure'}
            self.save(); return
        try:
            net={symbol:Decimal(0) for symbol in self.symbols}
            order_ids=set()
            events=[e for e in self.evidence['requests'] if e['path']==E.ORDER and e['method']=='POST' and e['state'] not in ('NOT_SENT','REJECTED')]
            replies=[]
            for event in events:
                p=event['params']
                reply=self.read(E.ORDER,{'symbol':p['symbol'],'origClientOrderId':p['newClientOrderId']})
                self.require_order_identity(reply,p)
                if reply.get('status') not in TERMINAL:
                    raise _Stop('BLOCKED','owned order unresolved; no extra close')
                event['reconciled_response']=self.scrub(reply)
                replies.append(reply)
            owned_algos=[e for e in self.evidence['requests'] if e['path']==E.ALGO_ORDER and e['method']=='POST' and e['state'] not in ('NOT_SENT','REJECTED')]
            child_orders=[]
            active_owned={}
            observed_algos={}
            for event in owned_algos:
                key=event['params']['clientAlgoId']
                algo=self.read(E.ALGO_ORDER,{'clientAlgoId':key})
                if algo.get('clientAlgoId')!=key or algo.get('symbol')!=event['params']['symbol']:
                    raise _Stop('BLOCKED','conditional order identity mismatch')
                observed_algos[key]=(algo.get('algoStatus'),algo.get('actualOrderId'))
                if algo.get('algoStatus')=='NEW':
                    active_owned[key]=event['params']['symbol']
                elif algo.get('algoStatus') not in ALGO_TERMINAL:
                    raise _Stop('BLOCKED','owned protection state unresolved')
                child=algo.get('actualOrderId')
                if child not in ('',None,0,'0'):
                    reply=self.read(E.ORDER,{'symbol':event['params']['symbol'],'orderId':child})
                    if reply.get('status') not in TERMINAL:
                        raise _Stop('BLOCKED','triggered child unresolved; no extra close')
                    child_orders.append(reply)
            for reply in replies+child_orders:
                symbol=reply.get('symbol'); ident=(symbol,str(reply.get('orderId')))
                if symbol not in net or reply.get('orderId') is None or reply.get('side') not in ('BUY','SELL'):
                    raise _Stop('BLOCKED','order attribution incomplete')
                if ident in order_ids:
                    continue
                order_ids.add(ident)
                net[symbol]+=_decimal(reply.get('executedQty'))*(1 if reply['side']=='BUY' else -1)
            for symbol in self.symbols:
                trades=self.read(E.USER_TRADES,{'symbol':symbol,'startTime':self.evidence['started_ms'],'limit':1000})
                if not isinstance(trades,list) or len(trades)>=1000 or any(
                    (symbol,str(t.get('orderId'))) not in order_ids for t in trades):
                    raise _Stop('BLOCKED','external or incomplete trade history; no extra close')
            # Existing foreign orders or a mismatch mean manual takeover, never flatten.
            if self.read(E.OPEN_ORDERS) != []:
                raise _Stop('BLOCKED','unowned or unresolved ordinary orders; no extra close')
            open_algos=self.read(E.OPEN_ALGO_ORDERS)
            if not isinstance(open_algos,list) or any(
                a.get('clientAlgoId') not in active_owned
                or a.get('symbol')!=active_owned[a['clientAlgoId']]
                or a.get('algoStatus')!='NEW'
                or a.get('actualOrderId') not in ('',None,0,'0') for a in open_algos):
                raise _Stop('BLOCKED','unowned or unresolved open protection; no extra close')
            if {a['clientAlgoId'] for a in open_algos}!=set(active_owned):
                raise _Stop('BLOCKED','protection changed during attribution; no extra close')
            positions=self.positions()
            if any(_decimal(row['positionAmt'])!=net.get(symbol,Decimal(0)) for symbol,row in positions.items()):
                raise _Stop('BLOCKED','position differs from owned executions; no extra close')
            for symbol,amount in net.items():
                if amount and (symbol not in positions or _decimal(positions[symbol]['positionAmt'])!=amount):
                    raise _Stop('BLOCKED','position missing; not zero proof')
            # Keep protection until a reduce-only close and explicit zero are confirmed.
            # A trigger during attribution invalidates the snapshot; never reclose an unresolved child.
            for event in owned_algos:
                key=event['params']['clientAlgoId']
                current=self.read(E.ALGO_ORDER,{'clientAlgoId':key})
                if (current.get('clientAlgoId')!=key or current.get('symbol')!=event['params']['symbol']
                        or (current.get('algoStatus'),current.get('actualOrderId'))!=observed_algos[key]):
                    raise _Stop('BLOCKED','protection changed during attribution; no extra close')
            for symbol,amount in net.items():
                if amount:
                    self.order(symbol,'BUY' if amount<0 else 'SELL',abs(amount),True,'cleanup close owned residual')
            final=self.positions()
            # An explicit zero row is required; omission is not accepted as proof.
            if any(symbol not in final or _decimal(final[symbol]['positionAmt'])!=0 for symbol in self.symbols):
                raise _Stop('BLOCKED','explicit final zero position not available')
            # Only zero exposure permits cancellation of remaining owned protection.
            for event in owned_algos:
                key=event['params']['clientAlgoId']
                algo=self.read(E.ALGO_ORDER,{'clientAlgoId':key})
                if algo.get('clientAlgoId')!=key or algo.get('symbol')!=event['params']['symbol']:
                    raise _Stop('BLOCKED','conditional order identity mismatch')
                if algo.get('algoStatus')=='NEW':
                    self.write('DELETE',E.ALGO_ORDER,{'clientAlgoId':key},'cleanup cancel protection after zero')
                    algo=self.read(E.ALGO_ORDER,{'clientAlgoId':key})
                if algo.get('algoStatus') not in ALGO_TERMINAL:
                    raise _Stop('BLOCKED','owned protection not confirmed terminal after zero')
                child=algo.get('actualOrderId')
                if child not in ('',None,0,'0'):
                    reply=self.read(E.ORDER,{'symbol':event['params']['symbol'],'orderId':child})
                    if reply.get('status') not in TERMINAL:
                        raise _Stop('BLOCKED','triggered child unresolved after close')
            self.evidence['cleanup']={'status':'PASS','detail':'owned orders terminal; explicit zero positions verified'}
        except (ExchangeError,_Stop,RuleViolation,KeyError,TypeError) as exc:
            self.evidence['cleanup']={'status':'BLOCKED','detail':exc.reason if isinstance(exc,_Stop) else 'cleanup query/write failed; manual reconciliation required'}
        self.save()


def run_probe(group, client, output_dir, *, symbol, quantity, trigger_price=None,
              replacement_trigger_price=None, second_symbol=None, second_quantity=None,
              worker_factory=None):
    """Execute one bounded experiment; every full gate remains PARTIAL/BLOCKED/FAIL.

    Inputs are explicit demo quantities and trigger prices, never portfolio sizing.
    A unique output run is created. Never replay an interrupted run automatically.
    """
    if getattr(client,'env',None)!='demo':
        raise ValueError('probes require demo environment')
    if group not in {'g1','g2','g3','g4','g5'}:
        raise ValueError('unknown probe group')
    symbols=[symbol]; quantities=[_decimal(quantity)]
    if group=='g5' and second_symbol and second_quantity is not None:
        symbols.append(second_symbol);quantities.append(_decimal(second_quantity))
    probe=_Probe(group,client,output_dir,symbols,quantities,worker_factory)
    try:
        if any(q<=0 for q in quantities):
            raise _Stop('BLOCKED','quantity must be positive')
        if group=='g5' and (len(symbols)!=2 or symbols[0]==symbols[1]):
            raise _Stop('BLOCKED','g5 requires two distinct symbols and explicit quantities')
        if group in {'g1','g2','g3'} and trigger_price is None:
            raise _Stop('BLOCKED','explicit trigger_price required')
        if group=='g2' and replacement_trigger_price is None:
            raise _Stop('BLOCKED','explicit replacement_trigger_price required')
        probe.preflight()
        q=quantities[0]
        if group in {'g1','g2','g3'}:
            price=_decimal(probe.read(E.TICKER_PRICE,{'symbol':symbol})['price'])
            trigger=probe.rules[symbol].round_price(trigger_price,up=True)
            probe.rules[symbol].price.validate(trigger,'price')
            if trigger<=price:
                raise _Stop('BLOCKED','main protection trigger must be above current contract price')
            replacement=None
            if group=='g2':
                replacement=probe.rules[symbol].round_price(replacement_trigger_price,up=True)
                probe.rules[symbol].price.validate(replacement,'price')
                if replacement<=price or replacement==trigger:
                    raise _Stop('BLOCKED','replacement must differ and remain above contract price')
            probe.order(symbol,'SELL',q)
            old=probe.protection(symbol,q,trigger)
            probe.check('quantity_protection','PASS','quantity/reduceOnly/contract-price/priceProtect read back')
            if group=='g2':
                new=probe.protection(symbol,q,replacement)
                active=probe.read(E.OPEN_ALGO_ORDERS)
                if not {old,new}.issubset({x.get('clientAlgoId') for x in active}):
                    raise _Stop('FAIL','old and new protection do not coexist')
                probe.write('DELETE',E.ALGO_ORDER,{'clientAlgoId':old},'replace confirmed old protection')
                if probe.read(E.ALGO_ORDER,{'clientAlgoId':old}).get('algoStatus')!='CANCELED':
                    raise _Stop('BLOCKED','old protection cancellation not confirmed')
                probe.check('replacement_coexists','PASS','both IDs visible before old cancellation')
            if group=='g3':
                half=probe.rules[symbol].round_quantity(q/2)
                if half<=0 or half>=q:
                    raise _Stop('BLOCKED','quantity too small for partial reduction')
                probe.order(symbol,'BUY',half,True)
                if probe.read(E.ALGO_ORDER,{'clientAlgoId':old}).get('algoStatus')!='NEW':
                    raise _Stop('FAIL','protection did not remain active after reduction')
                probe.check('reduce_with_protection','PASS','ordinary reduceOnly MARKET filled while protection remained active')
        elif group=='g4':
            first=probe.order(symbol,'SELL',q)
            second=probe.order(symbol,'SELL',q)
            position=probe.positions().get(symbol)
            expected=(_decimal(first['avgPrice'])+_decimal(second['avgPrice']))/2
            if position is None or abs(_decimal(position['entryPrice'])-expected)>Decimal('0.00000001'):
                raise _Stop('FAIL','entryPrice differs from filled weighted average')
            probe.order(symbol,'BUY',q,True)
            reduced=probe.positions().get(symbol)
            if reduced is None or _decimal(reduced['entryPrice'])!=_decimal(position['entryPrice']):
                raise _Stop('FAIL','partial reduction changed entryPrice')
            probe.check('cost_add_reduce','PASS','equal-quantity weighted fills and reduction cost readback match')
            probe.read(E.INCOME,{'symbol':symbol,'startTime':probe.evidence['started_ms'],'limit':1000})
            if _decimal(first['avgPrice'])==_decimal(second['avgPrice']):
                probe.check('different_fill_prices','NOT_OBSERVED','two fills used the same price')
        else:
            halves=[probe.rules[s].round_quantity(qty/2) for s,qty in zip(symbols,quantities)]
            if any(h<=0 or h>=qty for h,qty in zip(halves,quantities)):
                raise _Stop('BLOCKED','quantities too small for two waves')
            seed=[(symbols[0],'SELL',quantities[0],False),(symbols[1],'BUY',quantities[1],False)]
            probe.batch(seed,'seed paired positions')
            probe.batch([(symbols[0],'BUY',halves[0],True),(symbols[1],'SELL',halves[1],True)],'wave A reductions')
            account=probe.read(E.ACCOUNT)
            needed=sum(h*_decimal(probe.read(E.MARK_PRICE,{'symbol':s})['markPrice'])/2 for s,h in zip(symbols,halves))
            if _decimal(account.get('availableBalance'))<needed:
                raise _Stop('BLOCKED','wave B margin insufficient; shortage experiment not implemented')
            probe.batch([(symbols[0],'SELL',halves[0],False),(symbols[1],'BUY',halves[1],False)],'wave B additions')
            probe.check('two_waves','PASS','independent paired requests; wave A confirmed before wave B margin check and submission')
        remaining={
            'g1':'natural trigger, no reversal, crossed-price reject and exit',
            'g2':'both replacement directions, trigger during replacement, rejection rollback, shared-cause failure',
            'g3':'partial fill/stop, ADL, excess conditional trigger and auto-cancel at zero',
            'g4':'different-price fills, funding/fees, process restart, full close/reopen, API key permissions',
            'g5':'five-slot saturation, partial success/rejection, response loss, shortage scaling D6',
        }
        probe.check('remaining_matrix','NOT_OBSERVED',remaining[group])
        probe.evidence['status']='PARTIAL'
    except _Stop as exc:
        probe.evidence.update(status=exc.status,reason=exc.reason)
    except ExchangeError as exc:
        probe.evidence.update(status='BLOCKED',reason='exchange error; no resend',error={'outcome':exc.outcome,'code':exc.code,'http_status':exc.http_status})
    except (RuleViolation,KeyError,TypeError,ValueError):
        probe.evidence.update(status='BLOCKED',reason='invalid input or exchange shape')
    # OSError/BaseException deliberately escape: disk failure/crash forbids more writes.
    probe.save()
    probe.cleanup()
    if probe.evidence['cleanup']['status']!='PASS':
        probe.evidence['status']='BLOCKED'
    probe.evidence['finished_at']=datetime.now(timezone.utc).isoformat()
    probe.save()
    return probe.evidence
