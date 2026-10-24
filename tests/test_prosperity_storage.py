from dataclasses import replace
import math

import pytest

from src.data.prosperity_storage import content_hash, normalize_board, prepare_board, validate_board
from tests.prosperity_fixtures import storage_batch, storage_inputs


def test_archive_retains_inputs_but_omits_statement_numbers():
    batch = storage_batch()
    stock = batch[0].stocks[0]
    assert {'eps_window', 'ntm', 'unit_factor', 'revision'} <= stock['archive'].keys()
    assert 'consensus_missing_reasons' in stock
    assert all(not {'revenue', 'gross_profit', 'net_income', 'free_cash_flow'} & q.keys()
               for q in stock['quarters'])
    assert stock['archive']['eps_window'][0][4] is not None
    assert len(batch) == 4
    assert all(validate_board(b) is None for b in batch)


@pytest.mark.parametrize('zero', [0, 0.0, -0.0])
def test_sql_numbers_canonicalize_without_changing_existing_hashes(zero):
    b = storage_batch()[0]
    row = replace(b.board.rows[0], trend_adj=zero)
    changed = replace(b, board=replace(b.board, rows=(row,) + b.board.rows[1:]))
    normalized = normalize_board(changed)
    assert type(normalized.board.rows[0].trend_adj) is float
    assert math.copysign(1, normalized.board.rows[0].trend_adj) == 1
    assert type(normalized.board.rows[0].rank) is int
    assert type(normalized.board.params_bootstrap) is bool
    assert normalized.board.params.params_version == b.board.params.params_version
    assert normalized.board.scheme_hash == b.board.scheme_hash
    ref = replace(b, board=replace(b.board, rows=(replace(row, trend_adj=0.0),) + b.board.rows[1:]))
    assert content_hash(changed) == content_hash(ref)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True])
def test_reject_invalid_sql_numbers(bad):
    b = storage_batch()[0]
    changed = replace(b, board=replace(b.board, rows=(replace(b.board.rows[0], score=bad),) + b.board.rows[1:]))
    with pytest.raises(ValueError):
        normalize_board(changed)


def test_prepare_deep_copies_and_preserves_missing_reasons():
    packets, factors, results, meta = storage_inputs()
    from terminal.prosperity.schemes import SCHEMES
    names = {p.symbol: p.symbol for p in packets}
    b = prepare_board(meta, results[0], SCHEMES['F1'], packets, factors, names=names)
    h = content_hash(b)
    packets[0].archive['eps_window'][0][2] = 9999
    factors[0].values['revenue_yoy'] = 9999
    assert content_hash(b) == h
    assert b.stocks[-1]['consensus_missing_reasons'] == {'ntm': 'no_current_snapshot'}


@pytest.mark.parametrize('case', ['archive', 'leak', 'duplicate', 'names', 'members', 'archive_mismatch'])
def test_prepare_rejects_incomplete_or_inconsistent_input(case):
    packets, factors, results, meta = storage_inputs()
    from terminal.prosperity.schemes import SCHEMES
    names = {p.symbol: p.symbol for p in packets}
    if case == 'archive':
        packets[0] = replace(packets[0], archive={})
    elif case == 'archive_mismatch':
        packets[0].archive['unit_factor'] = 2.0
    elif case == 'leak':
        packets[0] = replace(packets[0], price_date='2027-01-01')
    elif case == 'duplicate':
        packets.append(packets[0])
    elif case == 'names':
        names.pop(packets[0].symbol)
    elif case == 'members':
        meta = replace(meta, members=meta.members + ('LOST',))
    with pytest.raises(ValueError):
        prepare_board(meta, results[0], SCHEMES['F1'], packets, factors, names=names)


@pytest.mark.parametrize('change', [
    lambda b: replace(b, meta=replace(b.meta, computed_at='2026-10-23T08:00:00')),
    lambda b: replace(b, meta=replace(b.meta, members=b.meta.members + b.meta.members[:1])),
    lambda b: replace(b, board=replace(b.board, counts={'ranked': 99})),
    lambda b: replace(b, board=replace(b.board, params_bootstrap=1)),
])
def test_reject_invalid_contract(change):
    with pytest.raises(ValueError):
        validate_board(change(storage_batch()[0]))


def test_real_packet_with_negative_raw_listing_age_roundtrips(tmp_path):
    from tests.prosperity_fixtures import hist
    from terminal.prosperity.packet import build_packet
    from terminal.prosperity.factors import compute_factor_row
    from terminal.prosperity.kernel import score_board
    from terminal.prosperity.schemes import SCHEMES
    from src.data.prosperity_storage import BoardMeta
    from src.data.prosperity_store import ProsperityStore
    history = hist()
    history.profile = {'ipoDate': '2026-10-01'}
    p = build_packet(history, '2026-09-26', mode='replay', membership_basis='explicit',
                     benchmark_closes=[], with_beta=False)
    f = compute_factor_row(p)
    assert f.listing_days == -5
    scheme = SCHEMES['F1']
    result = score_board([f], scheme, frozen=None, as_of=p.as_of, code_version='test-v1')
    meta = BoardMeta('weekly', None, None, '2026-10-23T09:00:00Z', '0'*40, 'replay', (p.symbol,), {},
                     {'source_id': 'synthetic-real-packet', 'observed_at': None,
                      'snapshot_sha256': None, 'profile_observed_at': None})
    b = prepare_board(meta, result, scheme, [p], [f], names={p.symbol: None})
    with ProsperityStore(tmp_path / 'negative-age.db', read_only=False) as store:
        key = store.save_batch([b])[0]
        assert store.get_board(key) == b


@pytest.mark.parametrize('case', ['threshold', 'weight', 'factor_spec'])
def test_boolean_cannot_impersonate_numeric_configuration(case, monkeypatch):
    from terminal.prosperity.schemes import SCHEMES, FACTOR_SPECS
    from terminal.prosperity.kernel import score_board
    packets, factors, _, meta = storage_inputs()
    scheme = SCHEMES['F1']
    if case == 'threshold':
        scheme = replace(scheme, grade_strict=True)
    elif case == 'weight':
        scheme = replace(scheme, weights=(('revenue_yoy', True),))
    else:
        monkeypatch.setitem(FACTOR_SPECS, 'revenue_yoy', replace(FACTOR_SPECS['revenue_yoy'], min_quarters=True))
    result = score_board(factors, scheme, frozen=None, as_of='2026-09-26', code_version='test-v1')
    with pytest.raises(ValueError):
        prepare_board(meta, result, scheme, packets, factors, names={p.symbol: None for p in packets})


@pytest.mark.parametrize('bad', [10**400, -(10**400)])
def test_oversized_integer_is_a_validation_error_not_overflow(bad):
    """Boss 2026-10-23 #4: every validation failure is ValueError."""
    from src.data.prosperity_storage import real
    with pytest.raises(ValueError):
        real(bad)
    b = storage_batch()[0]
    changed = replace(b, board=replace(b.board, rows=(replace(b.board.rows[0], score=bad),) + b.board.rows[1:]))
    with pytest.raises(ValueError):
        normalize_board(changed)
