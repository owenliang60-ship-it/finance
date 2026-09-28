"""Exercise next-day cache updates using real frozen bars, with no network."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import pandas as pd
from scripts.crypto_breadth_cache import daily_rows, DAY

root=Path.cwd();original=root/'reports/crypto-pmarp-breadth-2026-09-28'
output=root/'reports/crypto-pmarp-breadth-review-2026-09-28'
report=json.loads((original/'crypto_pmarp_breadth_2026-09-27.json').read_text())
end=pd.Timestamp(report['data_cutoff_utc']);start=pd.Timestamp(report['parameters']['price_start'],tz='UTC')
previous=end-pd.Timedelta(days=1)
manifest=json.loads((root/'config/crypto_pmarp_breadth_sources.json').read_text())
known={}
for symbol in report['constituents']:
    if symbol in manifest['retired_prices']:
        path=original/'breadth_cache/retired_prices'/(symbol+'.json')
    else:
        path=next((original/'breadth_cache/daily/2026-09-27').glob(symbol+'_1d_*.json'))
    known[symbol]=json.loads(path.read_text())
with TemporaryDirectory(prefix='incremental-check-',dir=output) as directory:
    calls=[]
    def fetch(symbol,lower,upper,limit):
        calls.append((symbol,str(lower),str(upper),limit))
        a=int(lower.timestamp()*1000);b=int(upper.timestamp()*1000)
        return [r for r in known[symbol] if a<=r[0]<b]
    market=SimpleNamespace(cache_dir=Path(directory),fetch_daily_raw=fetch)
    for symbol,life in report['constituents'].items():
        meta=dict(life,symbol=symbol,status='TRADING')
        if max(int(start.timestamp()*1000),meta['onboardDate']//DAY*DAY)<min(int(previous.timestamp()*1000),meta['deliveryDate']//DAY*DAY):
            daily_rows(market,symbol,start,previous,meta)
    calls.clear()
    for symbol,life in report['constituents'].items():
        daily_rows(market,symbol,start,end,dict(life,symbol=symbol,status='TRADING'))
    assert len(calls)==report['current']['eligible_count']==525
    assert all(pd.Timestamp(a)==previous and pd.Timestamp(b)==end and count==1 for _,a,b,count in calls)
    result={'status':'PASS','historical_contracts':len(known),'next_day_requests':len(calls),
            'bars_per_request':1,'total_new_bars':525,'network_requests':0,
            'retired_contracts_reused_without_download':len(known)-len(calls)}
(output/'next_day_verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
