"""Replay frozen real inputs twice; all network and notification calls forbidden."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd

from scripts.crypto_pmarp_breadth import BreadthMarket, MANIFEST_PATH, BUNDLED_RETIRED_DIR, build_report
from scripts.crypto_trend_market import InactiveSymbolError

root=Path.cwd()
original=root/'reports/crypto-pmarp-breadth-2026-09-28'
cache=root/'reports/crypto-pmarp-breadth-review-2026-09-28/cache'
output=root/'reports/crypto-pmarp-breadth-review-2026-09-28'
expected=json.loads((original/'crypto_pmarp_breadth_2026-09-27.json').read_text())
namespace={'pd':pd}
exec((output/'scanner_kernel.txt').read_text(),namespace)

def convert(raw):
    return pd.DataFrame({'timestamp':pd.to_datetime([r[0] for r in raw],unit='ms',utc=True),
                         'close':[float(r[4]) for r in raw]})
scanner=SimpleNamespace(calculate_pmarp=namespace['calculate_pmarp'],klines_to_dataframe=convert)
manifest=json.loads(MANIFEST_PATH.read_text())
# Work from frozen observed rows, not supplements masquerading as exchange data.
observed=[m for m in json.loads((cache/'catalog_2026-09-27.json').read_text())['symbols']
          if 'sources' not in m and m.get('_catalog_source')!='supplement']
results=[]
first_phase = 'existing_rolling' if (cache/'rolling_1d').exists() else 'legacy_import'
for phase in [first_phase,'rolling_replay']:
    market=BreadthMarket(scanner,cache,pd.Timestamp('2026-09-27',tz='UTC'),
                         history_days=366,manifest=manifest,retired_dir=BUNDLED_RETIRED_DIR)
    market._json=lambda endpoint,params=None:{'symbols':observed} if endpoint=='/fapi/v1/exchangeInfo' else None
    price_calls=[]
    def fetch(symbol,*a,**kw):
        price_calls.append(symbol)
        if symbol=='GAIBUSDT':raise InactiveSymbolError('frozen observed exchange -1122')
        raise AssertionError('unexpected price download: '+symbol)
    market.fetch_daily_raw=fetch
    with patch('scripts.crypto_trend_market.requests.get',side_effect=AssertionError('network forbidden')):
        report=build_report(market,pd.Timestamp('2026-09-27',tz='UTC'))
    keys=['history','current','constituents','confirmed_unopened','strong_percentile','weak_percentile']
    assert all(report[k]==expected[k] for k in keys)
    assert price_calls==['GAIBUSDT']
    (output/(phase+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    results.append({'phase':phase,'status':'PASS','identical_fields':keys,
                    'price_network_requests':0,'confirmed_pending_probe':price_calls})
files=list((cache/'rolling_1d').glob('*.json'))
summary={'status':'PASS','checks':results,'rolling_files':len(files),
         'rolling_bytes':sum(p.stat().st_size for p in files),
         'max_rows_per_contract':max(len(json.loads(p.read_text())['rows']) for p in files)}
(output/'replay_verification.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary))
