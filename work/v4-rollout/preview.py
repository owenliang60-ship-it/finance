"""Re-score a saved real daily snapshot; independent percentile/order check."""
import argparse,copy,json
from pathlib import Path
import pandas as pd
from scripts.crypto_trend_rankings import (score_rows,direction_eligible,message,V4_WEIGHTS,
                                         CURRENT_SCORING_VERSION,LONG_DRAWDOWN_LIMIT)


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();original=json.loads(a.input.read_text());report=copy.deepcopy(original)
    report.update(schema_version=5,scoring_version=CURRENT_SCORING_VERSION,weights=V4_WEIGHTS,
                  long_drawdown_limit=LONG_DRAWDOWN_LIMIT,
                  direction_gate='return > 0 and log_price_slope > 0 and drawdown <= 0.30; AFTER pool percentiles')
    checked=0;error=0.
    for name,period in report['periods'].items():
        rows=period['rows']
        for r in rows:r.pop('rank',None)
        score_rows(rows,quote_turnover={r['symbol']:r.get('quote_volume') for r in rows})
        valid=[r for r in rows if r['status']=='ok']
        for r in valid:
            expected=0.
            for key,w in [('return',45),('er',12.5),('r_squared',12.5),('drawdown',5)]:
                value=(lambda x:abs(x[key])) if key=='return' else (lambda x:-x[key]) if key=='drawdown' else (lambda x:x[key])
                expected+=w*sum(value(q)<=value(r) for q in valid)/len(valid)
            expected+=25*r['quote_volume']/(r['quote_volume']+1e9)
            error=max(error,abs(r['score']-expected));assert abs(r['score']-expected)<1e-10
            r['eligible']=direction_eligible(r)
            r['drawdown_excluded']=r['return']>0 and r['slope']>0 and r['drawdown']>.30
            assert r['eligible']==(r['return']>0 and r['slope']>0 and r['drawdown']<=.30)
            checked+=1
        ranked=sorted((r for r in valid if r['eligible']),key=lambda r:(-r['score'],r['symbol']))
        for i,r in enumerate(ranked,1):r['rank']=i
        period.update(ranked=ranked,top10=ranked[:10],uptrend_count=len(ranked),
                      drawdown_watch=sorted((r for r in valid if r['drawdown_excluded']),key=lambda r:(-r['score'],r['symbol'])))
        assert all(r['drawdown']<=.3 for r in period['top10'])
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/'preview.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    for h in report.get('published_period_days',[10,14]):
        (a.out/f'preview-{h}d.md').write_text(message(report,h)+'\n')
    proof=dict(status='pass',valid_windows=checked,max_score_error=error,input_as_of=report['as_of'],
               note='Saved real factor snapshot re-scored; no API request or message sent',
               top10={k:[r['symbol'] for r in v['top10']] for k,v in report['periods'].items()})
    (a.out/'verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    print(json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':main()
