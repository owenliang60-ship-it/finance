"""Stage-1 entry points. No production mutation command is exposed."""
import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.crypto_trend_rankings import atomic_text
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
    return p


def write_result(path,result):
    path.parent.mkdir(parents=True,exist_ok=True)
    atomic_text(path,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False,default=str)+'\n')


def main(argv=None):
    args=parser().parse_args(argv)
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
