"""Reviewed corrections must remain narrower than the known vendor error."""
import copy
import json
from pathlib import Path

import pytest

from src.data.security_source_corrections import (
    load_security_source_corrections, apply_security_source_corrections,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def records():
    return load_security_source_corrections(ROOT / 'config/baskets')


@pytest.fixture
def source_rows():
    return json.loads((ROOT / 'tests/fixtures/index_pe_source_errors_20260926.json').read_text())


def test_exact_source_corrections_preserve_raw_rows(records, source_rows):
    original = copy.deepcopy(source_rows)
    result = apply_security_source_corrections(source_rows, records)
    assert source_rows == original
    assert len(result) == len(original) == 2
    for before, after in zip(original, result):
        for field in ('raw_payload_json', 'raw_symbol', 'cusip', 'isin',
                      'issuer_lei', 'asset_category', 'weight_pct', 'market_value'):
            assert before[field] == after[field]
    cvr = next(row for row in result if row['basket_symbol'] == 'SPY')
    nxp = next(row for row in result if row['basket_symbol'] == 'SOXX')
    assert cvr['included'] == 0
    assert cvr['symbol'] is None and cvr['covered_by'] is None
    assert cvr['filter_reason'] == 'reviewed_cvr'
    assert nxp['effective_security']['cusip'] == 'N6596X109'
    assert nxp['cusip'] == 'F2933A109'


@pytest.mark.parametrize('field,value', [
    ('basket_symbol', 'QQQ'), ('source_kind', 'disclosure'),
    ('holding_date', '2026-09-27'), ('holding_date', '2026-09-24'),
])
def test_other_source_scope_never_matches(records, source_rows, field, value):
    for row in source_rows:
        row[field] = value
    assert all('correction_id' not in row for row in
               apply_security_source_corrections(source_rows, records))


@pytest.mark.parametrize('field,value,column', [
    ('asset', 'NXPI', 'raw_symbol'), ('name', 'OTHER COMPANY', 'name'),
    ('securityCusip', 'WRONG1234', 'cusip'), ('isin', 'US0000000000', 'isin'),
])
def test_changed_identifiers_never_match(records, source_rows, field, value, column):
    for row in source_rows:
        raw = json.loads(row['raw_payload_json'])
        raw[field] = value
        row['raw_payload_json'] = json.dumps(raw)
        row[column] = value
    with pytest.raises(ValueError, match='reviewed snapshot'):
        apply_security_source_corrections(source_rows, records)


@pytest.mark.parametrize('column,value', [('cusip','N6596X109'),('raw_symbol','FAKE'),
                                         ('isin','US0000000000'),('issuer_lei','FAKE'),
                                         ('weight_pct', 0), ('market_value', 0)])
def test_normalized_tampering_is_not_hidden(records, source_rows, column, value):
    source_rows[1][column] = value
    with pytest.raises(ValueError, match='raw source'):
        apply_security_source_corrections(source_rows, records)


def test_dual_cusip_conflict_is_not_corrected(records, source_rows):
    raw = json.loads(source_rows[1]['raw_payload_json'])
    raw['cusip'] = 'OTHER1234'
    source_rows[1]['raw_payload_json'] = json.dumps(raw)
    with pytest.raises(ValueError, match='CUSIP conflict'):
        apply_security_source_corrections(source_rows, records)


def test_duplicate_corrections_fail(records, source_rows):
    with pytest.raises(ValueError, match='multiple'):
        apply_security_source_corrections(source_rows, records + records)


def test_correction_marker_cannot_be_injected(source_rows):
    source_rows[1]['effective_security'] = {'cusip':'N6596X109'}
    source_rows[1]['correction_id'] = 'invented'
    result = apply_security_source_corrections(source_rows, [])
    assert 'effective_security' not in result[1]
    assert 'correction_id' not in result[1]


def test_persisted_cvr_marker_cannot_bypass_expired_review(records, source_rows):
    row = source_rows[0]
    row.update(holding_date='2026-09-27', included=0, symbol=None,
               filter_reason='reviewed_cvr', covered_by=None)
    with pytest.raises(ValueError, match='physical.*correction'):
        apply_security_source_corrections([row], records)


def test_missing_config_has_no_exceptions(tmp_path):
    assert load_security_source_corrections(tmp_path) == []


def test_evidence_hash_is_verified(records, tmp_path):
    root = tmp_path / 'config/baskets'
    root.mkdir(parents=True)
    (root/'security_source_corrections.json').write_text(json.dumps(records))
    with pytest.raises(ValueError, match='evidence'):
        load_security_source_corrections(root, evidence_root=tmp_path)


def test_issuer_gate_uses_effective_cusip_but_checks_raw(records, source_rows):
    from src.data.fund_issuer_identity import load_issuer_overrides, audit_snapshot_identities
    view = apply_security_source_corrections(source_rows, records)
    result = audit_snapshot_identities(view, load_issuer_overrides(ROOT/'config/baskets'),
                                       corrections=records)
    assert result['errors'] == []
    assert [r['symbol'] for r in result['resolved']] == ['NXPI']


def test_injected_effective_cusip_does_not_bypass_issuer_gate(source_rows):
    from src.data.fund_issuer_identity import load_issuer_overrides, resolve_issuer_identity
    row = next(r for r in source_rows if r['basket_symbol'] == 'SOXX')
    row['effective_security'] = {'cusip':'N6596X109'}
    assert resolve_issuer_identity(row, load_issuer_overrides(ROOT/'config/baskets'))[0] is None


def test_pit_cvr_requires_exact_same_day_source(records, source_rows, tmp_path):
    from src.data.market_store import MarketStore
    from src.data.security_source_corrections import apply_pit_security_corrections
    row = next(r for r in source_rows if r['basket_symbol'] == 'SPY')
    store = MarketStore(tmp_path/'market.db')
    store.replace_fund_disclosure_snapshot('SPY', row['holding_date'], 'live', [row],
        rebalance_close_date=row['rebalance_close_date'],
        composition_effective_date=row['composition_effective_date'],
        composition_available_date=row['composition_available_date'], fetched_at=row['fetched_at'])
    raw = json.loads(row['raw_payload_json'])
    holding = dict(raw_asset=raw['asset'], name=raw['name'], weight_pct=raw['weightPercentage'],
                   market_value=raw['marketValue'], updated_at=raw['updatedAt'],
                   included=1, symbol=raw['asset'], covered_by=None, filter_reason=None)
    original = dict(holding)
    view = apply_pit_security_corrections(store._get_conn(), 'SPY', '2026-09-26', [holding], records)
    assert view[0]['filter_reason'] == 'reviewed_cvr' and view[0]['included'] == 0
    assert holding == original
    for field in ('weight_pct','market_value','updated_at'):
        bad = {**holding, field: None}
        with pytest.raises(ValueError, match='PIT'):
            apply_pit_security_corrections(store._get_conn(), 'SPY', '2026-09-26', [bad], records)
    with pytest.raises(ValueError, match='PIT'):
        apply_pit_security_corrections(store._get_conn(), 'SPY', '2026-09-26', [], records)
    # No later physical evidence can repair older source vintages.
    assert apply_pit_security_corrections(store._get_conn(), 'SPY', '2026-09-19', [holding], records) == [holding]
    store.close()
