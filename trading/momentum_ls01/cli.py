"""LS01 command line: offline checks, the two scheduled jobs and operator commands.

Nothing is sent to the exchange unless the command is given --live (rebalance) or is
the live guard / resume. Every other command is read-only on the exchange.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time

import pandas as pd

from scripts.crypto_trend_rankings import atomic_text
from . import config as C
from .binance import BinanceClient, Endpoints, load_credentials
from .goldcheck import run_goldcheck, check_market_replay
from .market_public import PublicMarket, fetch_weekly
from .prepare import prepare


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    commands=p.add_subparsers(dest='command',required=True)
    gold=commands.add_parser('goldcheck')
    gold.add_argument('--frozen-dir',required=True,type=Path)
    gold.add_argument('--weekly-source',action='append',default=[],type=Path)
    gold.add_argument('--replay-dir',type=Path)
    gold.add_argument('--reports-dir',type=Path)
    gold.add_argument('--out',type=Path,default=Path('work/momentum-ls-01-protocol/goldcheck.json'))
    prep=commands.add_parser('prepare')
    prep.add_argument('--anchor',required=True)
    prep.add_argument('--env',choices=['prod','demo'],default='prod')
    prep.add_argument('--cache-dir',required=True,type=Path)
    prep.add_argument('--out',required=True,type=Path)
    probe=commands.add_parser('probe')
    probe.add_argument('group',choices=['g1','g2','g3','g4','g5'])
    probe.add_argument('--env',choices=['demo'],default='demo')
    probe.add_argument('--credentials',type=Path,default=Path.home()/'.momentum_ls01/demo.json')
    probe.add_argument('--symbol',required=True)
    probe.add_argument('--quantity',required=True)
    probe.add_argument('--trigger-price')
    probe.add_argument('--replacement-trigger-price')
    probe.add_argument('--second-symbol')
    probe.add_argument('--second-quantity')
    probe.add_argument('--output-dir',type=Path,default=Path('work/momentum-ls-01-protocol'))
    prep.add_argument('--state-dir',type=Path,help='write the frozen week into state.db (11:00 job)')
    default_key=Path.home()/'.momentum_ls01/binance.json'
    for name,text in OPS.items():
        op=commands.add_parser(name,help=text)
        op.add_argument('--state-dir',required=True,type=Path)
        op.add_argument('--credentials',type=Path,default=default_key)
        op.add_argument('--release-commit',default=os.environ.get('MLS01_RELEASE_COMMIT','unknown'))
        if name=='rebalance':
            op.add_argument('--live',action='store_true',help='send real orders; without it the run is a dry run')
            op.add_argument('--assume-equity',help='dry run only: simulate an empty account with this equity')
            op.add_argument('--late-approved',action='store_true',help='Boss approved a late run today')
        if name=='guard':
            op.add_argument('--readonly',action='store_true',help='shadow mode: no write permission')
            op.add_argument('--full',action='store_true',help='force the hourly full reconciliation')
        if name=='pause':
            op.add_argument('--reason',required=True)
        if name=='resume':
            op.add_argument('--accept-flow',help='tranId of one external transfer to accept as baseline')
            op.add_argument('--adopt',action='store_true',
                help='after handling the account by hand: the exchange positions and entry prices become the local book')
    return p


OPS={'rebalance':'Monday 04:00 UTC rebalance','guard':'minute guard','watchdog':'heartbeat check, no lock',
     'alive':'daily alive message','backup':'WAL-safe state.db backup','status':'local state summary',
     'pause':'persistent pause','reconcile':'read-only account report','resume':'reconcile, then clear the pause'}
NOT_OK={'no_new','late','blocked','failed','locked','no_prep','no_state'}
clock=time.time


def telegram_plain(text):
    """send_message uses legacy Markdown; an unescaped '_' in a reason code makes Telegram reject the message."""
    for mark in ('_','*','`','['):
        text=text.replace(mark,'\\'+mark)
    return text


def private_sender():
    from src.telegram_bot import send_message
    return lambda text: send_message(telegram_plain(text),channel='private',max_retries=1)


def build_runtime(args,writes,bootstrap=True):
    """Production runtime. ``writes`` is the only switch that lets a mutation leave this process.

    The guard passes ``bootstrap=False``: its own run loads exchangeInfo, so a failed read
    counts toward the guard's failure streak instead of crashing before it.
    """
    from .runtime import Runtime
    client=BinanceClient(env='prod',credentials=load_credentials(args.credentials),allow_prod_writes=writes)
    if bootstrap:
        client.request('GET',Endpoints.EXCHANGE_INFO)   # loads the request-weight limit
    return Runtime(state_dir=args.state_dir,client=client,clock=clock,sleep=time.sleep,send=private_sender(),
                   release_commit=args.release_commit)


def status_report(state_dir):
    from .store import Store
    now=clock()
    try:
        store=Store(state_dir,clock=clock,readonly=True)
        beat=store.get('guard_heartbeat')
        week=store.latest_week()
        return dict(status='ok',paused=store.get('pause'),ip_ban=store.get('ip_ban'),
            heartbeat_age=None if beat is None else now-beat['at'],
            week=None if week is None else {k:week[k] for k in ('anchor_utc','prep_status','status','equity_usdt','deviations')},
            episodes={k.split(':',1)[1]:store.get(k)['qty'] for k in store.keys('episode:')},
            cooldowns=[k.split(':',1)[1] for k in store.keys('cooldown:')],
            unresolved_requests=len(store.actions(state=('UNKNOWN','SENDING')))+len(store.actions(kind='order',state='ACKED')),
            pending_flows=store.get('pending_flows',[]),pending_notifications=len(store.pending_notifications()))
    except sqlite3.Error as error:
        return dict(status='no_state',error=str(error))


def account_report(rt):
    """Read-only facts for the production preflight (Task 3.2). Key permissions are checked there."""
    from .reconcile import Desk
    desk=Desk(rt)
    offset=rt.client.sync_clock()
    account=desk.read(Endpoints.ACCOUNT)
    positions=desk.positions()
    return dict(status='ok',clock_offset_ms=offset,one_way=desk.read(Endpoints.POSITION_MODE).get('dualSidePosition') is False,
        single_asset=desk.read(Endpoints.MULTI_ASSETS_MODE).get('multiAssetsMargin') is False,
        wallet=account.get('totalWalletBalance'),available=account.get('availableBalance'),
        open_positions=sum(1 for q,_ in positions.values() if q!=0),open_orders=len(desk.read(Endpoints.OPEN_ORDERS)),
        open_stops=len(desk.open_stops()),weight_limit=getattr(getattr(rt.client,'limiter',None),'limit',None),
        key_permissions='NOT_CHECKED')


def run_ops(args):
    from . import guard,reconcile
    from .rebalance import rebalance_job
    from .runtime import Runtime
    from .store import Store
    name=args.command
    if name=='status':
        result=status_report(args.state_dir)
    elif name=='watchdog':
        result=guard.watchdog(args.state_dir,clock,private_sender())
    elif name=='alive':
        result=guard.alive(args.state_dir,clock,private_sender())
    elif name=='backup':
        result=dict(status='ok',path=str(Store(args.state_dir,clock=clock).backup(args.state_dir/'backups')))
    elif name=='pause':
        rt=Runtime(state_dir=args.state_dir,client=None,clock=clock,sleep=time.sleep,send=private_sender())
        result=dict(status='ok',paused=reconcile.pause(rt,'manual_pause',args.reason))
    elif name=='guard':
        if args.readonly and (args.state_dir/'drill-stop-guard').exists():
            result=dict(status='drill_skipped')   # shadow drill: prove the watchdog notices a silent guard
        else:
            result=guard.guard_job(build_runtime(args,writes=not args.readonly,bootstrap=False),
                                   full=True if args.full else None)
    elif name=='rebalance':
        live=bool(args.live)
        result=rebalance_job(build_runtime(args,writes=live),dry_run=not live,late_approved=args.late_approved,
                             assume_equity=None if live else args.assume_equity)
    elif name=='reconcile':
        result=account_report(build_runtime(args,writes=False))
    else:
        flow=args.accept_flow
        result=reconcile.resume(build_runtime(args,writes=True),
                                accept_flow=int(flow) if flow and flow.lstrip('-').isdigit() else flow,
                                adopt=args.adopt)
    print(json.dumps(result,ensure_ascii=False,default=str))
    if name=='guard' and result.get('status')=='failed':
        # One failed pass is retried next minute. cron alerts once, when the failures become a pause.
        return 1 if result.get('fail_streak')==C.GUARD_FAIL_LIMIT else 0
    return 1 if result.get('status') in NOT_OK else 0


def run_prepare_job(args,anchor):
    """11:00 job. Any failure while fetching or scoring becomes a recorded failed preparation."""
    from .prepare import prepare_job
    from .runtime import Runtime
    client=BinanceClient(env=args.env)
    def compute():
        client.request('GET',Endpoints.EXCHANGE_INFO)
        weekly=fetch_weekly(client,anchor)
        return prepare(PublicMarket(client,args.cache_dir,anchor.normalize()-pd.Timedelta(days=1)),weekly,anchor)
    rt=Runtime(state_dir=args.state_dir,client=client,clock=clock,sleep=time.sleep,send=private_sender(),
               release_commit=os.environ.get('MLS01_RELEASE_COMMIT','unknown'))
    return prepare_job(rt,anchor,compute)


def write_result(path,result):
    path.parent.mkdir(parents=True,exist_ok=True)
    atomic_text(path,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False,default=str)+'\n')


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command in OPS:
        return run_ops(args)
    if args.command=='probe':
        args.out=args.output_dir/'last-probe.json'
    try:
        if args.command=='probe':
            from .probe import run_probe
            client=BinanceClient(env='demo',credentials=load_credentials(args.credentials),allow_demo_writes=True)
            result=run_probe(args.group,client,args.output_dir,symbol=args.symbol,quantity=args.quantity,
                trigger_price=args.trigger_price,replacement_trigger_price=args.replacement_trigger_price,
                second_symbol=args.second_symbol,second_quantity=args.second_quantity)
        elif args.command=='goldcheck':
            if bool(args.replay_dir)!=bool(args.reports_dir):
                raise ValueError('--replay-dir and --reports-dir must be supplied together')
            result=run_goldcheck(args.frozen_dir,args.weekly_source)
            if args.replay_dir:
                if not args.weekly_source:raise ValueError('--weekly-source required for full replay')
                result['checks']['GC5']=check_market_replay(args.replay_dir,args.reports_dir,args.weekly_source)
                result['status']='PASS' if all(v['status']=='PASS' for v in result['checks'].values()) else 'BLOCKED'
        else:
            anchor=pd.Timestamp(args.anchor)
            if anchor.tzinfo is None:raise ValueError('--anchor must include UTC timezone')
            anchor=anchor.tz_convert('UTC')
            if args.state_dir:
                result=run_prepare_job(args,anchor)
                result['status']='PASS' if result['status']=='ready' else 'FAIL'
                write_result(args.out,result)
                print(json.dumps(dict(status=result['status'],output=str(args.out))))
                return 0 if result['status']=='PASS' else 1
            client=BinanceClient(env=args.env)
            client.request('GET',Endpoints.EXCHANGE_INFO)
            weekly=fetch_weekly(client,anchor)
            market=PublicMarket(client,args.cache_dir,anchor.normalize()-pd.Timedelta(days=1))
            result=prepare(market,weekly,anchor)
            result['status']='PASS'
        write_result(args.out,result)
        print(json.dumps(dict(status=result['status'],output=str(args.out))))
        return 0 if result['status']=='PASS' else 1
    except (ValueError,KeyError,OSError,RuntimeError) as exc:
        result=dict(status='FAIL',error_type=type(exc).__name__,reason=str(exc))
        write_result(args.out,result)
        print(json.dumps(dict(status='FAIL',error_type=type(exc).__name__,output=str(args.out))))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
