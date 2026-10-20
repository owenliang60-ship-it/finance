"""Read-only frozen research evidence. This module never imports frozen code."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

STRATEGY_ID = 'momentum-ls-01'
RULE_VERSION = '2026-10-19.v2'


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_bundle(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root/'manifest.json').read_text())
    if (manifest.get('strategy_id'), manifest.get('rule_version')) != (STRATEGY_ID, RULE_VERSION):
        raise ValueError('frozen identity mismatch')
    hashes = manifest.get('sha256')
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('empty frozen hash manifest')
    for name, expected in hashes.items():
        path = (root/name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('manifest path outside frozen bundle')
        if not path.is_file():
            raise ValueError('missing frozen artifact: '+name)
        if sha256(path) != expected:
            raise ValueError('frozen hash mismatch: '+name)
    return dict(checked=len(hashes), manifest_sha256=sha256(root/'manifest.json'), sha256=hashes)


def records(frame):
    # pandas handles NaN -> JSON null and timezone-aware dates consistently.
    return json.loads(frame.to_json(orient='records', date_format='iso', double_precision=15))


def load_evidence(directory):
    root = Path(directory)
    provenance = verify_bundle(root)
    return dict(
        provenance=provenance,
        prices=pd.read_parquet(root/'inputs/opens.parquet'),
        signals=pd.read_parquet(root/'inputs/signals.parquet'),
        regimes=pd.read_parquet(root/'inputs/regimes.parquet'),
        lifetimes=json.loads((root/'inputs/lifetimes.json').read_text()),
        ledger=pd.read_csv(root/'result/ledger.csv', parse_dates=['time']),
        funding=pd.read_csv(root/'result/funding.csv', parse_dates=['time']),
        stops=pd.read_csv(root/'result/stops.csv', parse_dates=['time', 'first_touch_minute']),
        open_positions=pd.read_csv(root/'result/open_positions.csv', index_col=0, parse_dates=True),
    )


def anchor_cases(evidence):
    """Reconstruct pre-policy equity/inventory, including funding and gap stops.

    Cash is the frozen research signed-inventory cash, not a futures wallet.
    Expected holdings come from the separately persisted open-position panel.
    """
    e = evidence
    ledger, funding, prices = e['ledger'], e['funding'], e['prices']
    clock = prices.index
    anchors = clock[(clock.dayofweek == 0) & (clock.hour == 4)]
    result = []
    for t in anchors:
        prior = ledger[(ledger.time < t) | ((ledger.time == t) & (ledger.reason == 'stop_gap'))]
        inventory = prior.groupby('symbol').delta_units.sum()
        inventory = inventory[inventory.abs() > 1e-12]
        snapshot = prices.loc[t].dropna()
        if not set(inventory.index) <= set(snapshot.index):
            raise ValueError('missing held price at '+str(t))
        cash = 1 + float((-prior.delta_units*prior.price-prior.cost).sum())
        cash += float(funding.loc[funding.time <= t, 'cashflow'].sum())
        equity = cash + float((inventory*snapshot.reindex(inventory.index)).sum())
        if not np.isfinite(equity) or equity <= 0:
            raise ValueError('invalid reconstructed research equity')
        expected = e['open_positions'].loc[t]
        expected = expected[expected.abs() > 1e-12].to_dict()
        stops = e['stops'][e['stops'].time <= t]
        cooldown = {s: str(g.iloc[-1].first_touch_minute if pd.notna(g.iloc[-1].first_touch_minute)
                           else g.iloc[-1].time) for s, g in stops.groupby('symbol', sort=True)}
        # Earlier regime exits have expired at this strictly later Monday.
        day = t.normalize()-pd.Timedelta(days=1)
        signals = e['signals'][pd.to_datetime(e['signals'].date, utc=True) == day]
        if signals.empty:
            raise ValueError('missing anchor signal '+str(day))
        result.append(dict(anchor=str(t), equity=equity, positions=inventory.to_dict(),
                           prices=snapshot.to_dict(), expected=expected, cooldown=cooldown,
                           signals=records(signals), regime=records(e['regimes'].loc[[t]])[0],
                           ledger=records(ledger[ledger.time == t]), stops=records(stops)))
    return result


def export_fixtures(directory, destination):
    e = load_evidence(directory)
    cases = anchor_cases(e)
    choices = [0]
    for direction in (1, -1):
        choices.append(next(i for i, c in enumerate(cases) if i not in choices and c['regime']['regime'] == direction))
    choices.append(next(i for i, c in enumerate(cases) if i not in choices and c['stops']))
    choices.append(next(i for i in range(1, len(cases)) if i not in choices and cases[i]['regime']['regime'] != cases[i-1]['regime']['regime']))
    choices.append(len(cases)-1)
    payload = dict(strategy_id=STRATEGY_ID, rule_version=RULE_VERSION,
                   provenance=e['provenance'], lifetimes=e['lifetimes'],
                   cases=[cases[i] for i in choices], stops=records(e['stops']))
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n'
    if len(raw.encode()) >= 1_000_000:
        raise ValueError('fixture exceeds 1 MB')
    path = target/'anchors.json'
    if path.exists():
        raise ValueError('refusing to overwrite existing fixtures')
    path.write_text(raw)
    return dict(path=str(path), bytes=len(raw.encode()), anchors=[c['anchor'] for c in payload['cases']],
                checked=e['provenance']['checked'])
