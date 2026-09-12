"""Actual provider ticker collisions must not select another issuer's income."""
import json
from pathlib import Path
import pytest
from src.data.fmp_forward_ingestion import load_soxx_symbol_aliases, resolve_disclosure_symbol
from scripts.backfill_soxx_historical_pe import _snapshot_universe

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('MOB', 'MNST', '61174X109', 'US61174X1090'),
    ('GNE', 'GE', '369604301', 'US3696043013'),
    ('SYM', 'GEN', '668771108', 'US6687711084'),
    ('OCN', 'OMC', '681919106', 'US6819191064'),
    ('PLL', 'PRU', '744320102', 'US7443201022'),
    ('PARA', 'VIAC', '92556H206', 'US92556H2067'),
    ('VIA', 'VTRS', '92556V106', 'US92556V1061'),
    ('OEUR', 'O', '756109104', 'US7561091049'),
    ('FB', 'META', '30303M102', 'US30303M1027'),
]


@pytest.mark.parametrize('raw,target,cusip,isin', CASES)
def test_reviewed_security_selects_only_the_correct_financial_endpoint(raw,target,cusip,isin):
    aliases = load_soxx_symbol_aliases(ROOT/'config/soxx_symbol_aliases.json')
    resolved, evidence = resolve_disclosure_symbol(raw, '0000884394', aliases, cusip=cusip, isin=isin)
    assert resolved == target
    assert evidence['mode'] == 'authoritative'
    row = {'raw_symbol': raw, 'symbol': raw, 'included': 1, 'cusip':cusip,'isin':isin,
           'alias_symbol':target,'alias_mode':'authoritative'}
    assert _snapshot_universe([row]) == [target]
    with pytest.raises(ValueError, match='CUSIP'):
        resolve_disclosure_symbol(raw,'0000884394',aliases,cusip='WRONG',isin=isin)


def test_stale_persisted_alias_fails_before_a_financial_lookup():
    from src.data.fmp_forward_ingestion import validate_disclosure_alias_bindings
    aliases = load_soxx_symbol_aliases(ROOT/'config/soxx_symbol_aliases.json')
    row = {'raw_symbol':'MOB','symbol':'MOB','included':1,
           'cusip':'61174X109','isin':'US61174X1090','alias_symbol':None,'alias_mode':None}
    with pytest.raises(ValueError, match='stale'):
        validate_disclosure_alias_bindings([row], aliases)


def test_correct_alias_cannot_consume_a_different_issuers_financials():
    from src.data.fmp_forward_ingestion import validate_disclosure_alias_bindings
    aliases = load_soxx_symbol_aliases(ROOT/'config/soxx_symbol_aliases.json')
    row = {'raw_symbol':'MOB','symbol':'MOB','included':1,
           'cusip':'61174X109','isin':'US61174X1090','alias_symbol':'MNST','alias_mode':'authoritative'}
    validate_disclosure_alias_bindings([row],aliases,{'MNST':[{'cik':'0000865752'}]})
    with pytest.raises(ValueError, match='income issuer'):
        validate_disclosure_alias_bindings([row],aliases,{'MNST':[{'cik':'0001898643'}]})


def test_aliases_have_verifiable_primary_source_links():
    raw=json.loads((ROOT/'config/soxx_symbol_aliases.json').read_text())
    for symbol, *_ in CASES:
        assert raw[symbol]['source_url'].startswith('https://www.sec.gov/')
        assert len(raw[symbol]['issuer_cik']) == 10


def test_independent_verifier_rejects_wrong_issuer_and_stale_alias(tmp_path):
    from scripts.verify_index_pe_history import _source_alias_binding_errors
    from src.data.fmp_forward_ingestion import normalize_fund_disclosure_snapshot
    from src.data.market_store import MarketStore
    aliases=load_soxx_symbol_aliases(ROOT/'config/soxx_symbol_aliases.json')
    raw={'symbol':'MOB','name':'Monster Beverage Corp','title':'Monster Beverage Corp',
         'date':'2021-03-31','acceptedDate':'2021-05-28 15:06:35','pctVal':1,'valUsd':100,
         'cik':'0000884394','cusip':'61174X109','isin':'US61174X1090','assetCat':'EC'}
    rows,meta=normalize_fund_disclosure_snapshot('SPY',[raw],'disclosure','2026-09-11T07:00:00Z',
        ['2021-03-19','2021-03-22','2021-03-31'],{},{},aliases)
    store=MarketStore(tmp_path/'market.db')
    try:
        store.replace_fund_disclosure_snapshot('SPY',meta['holding_date'],'disclosure',rows,
            **{k:meta[k] for k in ('rebalance_close_date','composition_effective_date',
                'composition_available_date','fetched_at')})
        store.upsert_income('MNST',[{'date':'2026-06-30','period':'Q2','cik':'0001898643'}])
        assert any('income_issuer' in e for e in _source_alias_binding_errors(store._get_conn(),'SPY',aliases))
        store.upsert_income('MNST',[{'date':'2026-06-30','period':'Q2','cik':'0000865752'}])
        assert _source_alias_binding_errors(store._get_conn(),'SPY',aliases)==[]
        store._get_conn().execute("UPDATE fmp_fund_disclosure_holdings SET alias_symbol=NULL,alias_mode=NULL")
        assert any('stale_alias' in e for e in _source_alias_binding_errors(store._get_conn(),'SPY',aliases))
    finally:
        store.close()
