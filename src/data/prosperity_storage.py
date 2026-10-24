"""M8 archive contract. Kernel objects stay unchanged; SQL reals have a canonical form."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Tuple

from terminal.prosperity.factors import FactorRow
from terminal.prosperity.kernel import BoardResult, params_from_dict, params_to_dict
from terminal.prosperity.packet import packet_leaks
from terminal.prosperity.schemes import FACTOR_SPECS, FactorSpec, Scheme, scheme_hash
from terminal.prosperity.types import InputPacket

SCORE_REALS = ('coverage', 'composite', 'base', 'trend_adj', 'score', 'within')
SCORE_MAPS = ('values', 'missing', 'z', 'weights', 'contributions')
QUARTER_KEYS = ('fiscal_date', 'fiscal_year', 'period', 'period_days', 'reported_currency',
                'available_on', 'availability_basis', 'observed_on', 'labels', 'nulled')
STOCK_KEYS = ('symbol', 'as_of', 'membership_basis', 'sector', 'industry', 'current_fiscal',
              'quarter_bucket', 'data_age_days', 'reported_this_week', 'price_asof', 'price_date',
              'market_cap_asof', 'beta', 'pit', 'pit_basis', 'flags', 'listing_date')
ARCHIVE_KEYS = {'eps_window', 'pre_announce', 'ntm', 'ttm', 'revision', 'unit_factor',
                'market_cap_date', 'statement_conflicts'}


@dataclass(frozen=True)
class BoardKey:
    board_type: str
    as_of: str
    scheme_hash: str
    code_version: str


@dataclass(frozen=True)
class BoardMeta:
    board_type: str
    quarter_label: Optional[str]
    frozen_at: Optional[str]
    computed_at: str
    git_sha: str
    mode: str
    members: Tuple[str, ...]
    errors: Mapping[str, str]
    source_evidence: Mapping[str, Any]


@dataclass(frozen=True)
class StoredBoard:
    meta: BoardMeta
    board: BoardResult
    factors: Tuple[FactorRow, ...]
    stocks: Tuple[Mapping[str, Any], ...]
    scheme: Scheme
    factor_specs: Mapping[str, FactorSpec]


@dataclass(frozen=True)
class PublicationState:
    computed: bool
    published_at: Optional[str]


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def day(value):
    require(isinstance(value, str), 'invalid_date')
    require(date.fromisoformat(value).isoformat() == value, 'invalid_date')
    return value


def utc(value):
    require(isinstance(value, str), 'invalid_timestamp')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(dt.tzinfo is not None and dt.utcoffset() is not None, 'timezone_required')
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def instant(value):
    return datetime.fromisoformat(utc(value).replace('Z', '+00:00'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def json_copy(value):
    return json.loads(canonical(value))


def real(value, *, nullable=True):
    if value is None and nullable:
        return None
    require(type(value) in (int, float), 'invalid_number')
    try:
        v = float(value)
    except OverflowError:
        raise ValueError('invalid_number') from None
    require(math.isfinite(v), 'invalid_number')
    return 0.0 if v == 0 else v


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, 'invalid_integer')


def finite_tree(value):
    if isinstance(value, float):
        require(math.isfinite(value), 'nonfinite_number')
    elif isinstance(value, Mapping):
        require(all(isinstance(k, str) for k in value), 'nonstring_json_key')
        for v in value.values():
            finite_tree(v)
    elif isinstance(value, (tuple, list)):
        for v in value:
            finite_tree(v)
    else:
        require(value is None or type(value) in (str, int, bool), 'invalid_json_value')


def key_for(stored):
    b = stored.board
    return BoardKey(stored.meta.board_type, b.as_of, b.scheme_hash, b.code_version)


def check_key(key):
    require(key.board_type in ('weekly', 'quarter_final'), 'invalid_board_type')
    day(key.as_of)
    require(bool(key.scheme_hash) and bool(key.code_version), 'empty_version')


def normalize_board(stored: StoredBoard) -> StoredBoard:
    """Deep-copy and normalize only fields persisted in SQL REAL columns.

    Params/scheme/JSON retain their numeric representation: their existing identity
    hashes must never be recomputed from a float-coerced copy.
    """
    s = copy.deepcopy(stored)
    evidence = dict(s.meta.source_evidence)
    for k in ('observed_at', 'profile_observed_at'):
        if evidence.get(k) is not None:
            evidence[k] = utc(evidence[k])
    meta = replace(s.meta, members=tuple(sorted(s.meta.members)),
                   computed_at=utc(s.meta.computed_at),
                   frozen_at=None if s.meta.frozen_at is None else utc(s.meta.frozen_at),
                   source_evidence=evidence)
    factors = tuple(replace(f, values={k: real(v) for k, v in f.values.items()})
                    for f in sorted(s.factors, key=lambda f: f.symbol))
    rows = tuple(replace(r, **{k: real(getattr(r, k), nullable=k != 'coverage') for k in SCORE_REALS},
                         values={k: real(v) for k, v in r.values.items()},
                         **{k: {f: real(v, nullable=False) for f, v in getattr(r, k).items()}
                            for k in ('z', 'weights', 'contributions')}) for r in s.board.rows)
    return replace(s, meta=meta, factors=factors, board=replace(s.board, rows=rows),
                   stocks=tuple(json_copy(p) for p in sorted(s.stocks, key=lambda p: p['symbol'])))


def _archive_for(packet):
    c = packet.consensus
    return {'eps_window': [[q.fiscal_date, q.announce_date, q.eps_actual, list(q.labels), q.statement_fiscal]
                           for q in packet.eps],
            'pre_announce': asdict(c.pre_announce), 'ntm': asdict(c.ntm),
            'ttm': {'eps': c.ttm_eps, 'quarters': list(c.ttm_quarters)},
            'revision': asdict(c.revision), 'unit_factor': c.unit_factor}


def prepare_board(meta: BoardMeta, board: BoardResult, scheme: Scheme,
                  packets: Sequence[InputPacket], factors: Sequence[FactorRow], *,
                  names: Mapping[str, Optional[str]]) -> StoredBoard:
    symbols = [p.symbol for p in packets]
    require(len(set(symbols)) == len(symbols), 'duplicate_packet')
    require(set(names) == set(symbols), 'name_members_mismatch')
    stocks = []
    for p in packets:
        require(not packet_leaks(p), 'packet_leak')
        require(set(p.archive) == ARCHIVE_KEYS, 'archive_keys_mismatch')
        for k, v in _archive_for(p).items():
            require(canonical(p.archive[k]) == canonical(v), 'archive_packet_mismatch:' + k)
        projection = {k: getattr(p, k) for k in STOCK_KEYS}
        projection.update(name=names[p.symbol], quarters=[{k: getattr(q, k) for k in QUARTER_KEYS}
                                                        for q in p.quarters],
                          archive=p.archive, consensus_missing_reasons=p.consensus.missing_reasons)
        stocks.append(projection)
    stored = normalize_board(StoredBoard(meta, board, tuple(factors), tuple(stocks), scheme,
                                        {f: FACTOR_SPECS[f] for f, _ in scheme.weights}))
    validate_board(stored)
    return stored


def _strings(items):
    require(isinstance(items, (tuple, list)) and all(isinstance(x, str) for x in items), 'invalid_strings')


def _missing(mapping):
    require(all(isinstance(k, str) and isinstance(v, str) and v for k, v in mapping.items()),
            'invalid_missing_reason')


def _check_stock(p, bound):
    require(set(p) == set(STOCK_KEYS) | {'name', 'quarters', 'archive', 'consensus_missing_reasons'},
            'stock_projection_mismatch')
    require(p['as_of'] == bound, 'stock_date_mismatch')
    require(p['name'] is None or isinstance(p['name'], str), 'invalid_name')
    require(set(p['pit']) == {'statements', 'street_eps', 'consensus'}, 'pit_sources_mismatch')
    pit_rank = {'approximate': 0, 'strict': 1, 'live': 2}
    require(all(v in pit_rank for v in p['pit'].values()), 'invalid_pit')
    require(p['pit_basis'] == min(p['pit'].values(), key=pit_rank.__getitem__), 'pit_basis_mismatch')
    require(type(p['reported_this_week']) is bool, 'invalid_boolean')
    if p['data_age_days'] is not None:
        integer(p['data_age_days'])
    for field in ('price_asof', 'market_cap_asof', 'beta'):
        real(p[field])
    _strings(p['flags'])
    _missing(p['consensus_missing_reasons'])

    def past(v):
        if v is not None:
            require(day(v) <= bound, 'input_from_future')

    past(p['current_fiscal'])
    past(p['price_date'])
    for q in p['quarters']:
        require(set(q) == set(QUARTER_KEYS), 'quarter_projection_mismatch')
        for field in ('fiscal_date', 'available_on', 'observed_on'):
            past(q[field])
        if q['period_days'] is not None:
            integer(q['period_days'], 1)
        _strings(q['labels'])
        _strings(q['nulled'])
    a = p['archive']
    require(set(a) == ARCHIVE_KEYS, 'archive_keys_mismatch')
    for e in a['eps_window']:
        require(isinstance(e, list) and len(e) == 5, 'invalid_eps_archive')
        day(e[0])
        past(e[1])
        real(e[2])
        _strings(e[3])
        if e[4] is not None:
            day(e[4])
    past(a['market_cap_date'])
    shapes = {'pre_announce': {'value', 'source', 'snapshot_date', 'price_pre_announce', 'missing_reason'},
              'ntm': {'value', 'basis', 'quarters', 'snapshot_date', 'missing_reason'},
              'ttm': {'eps', 'quarters'},
              'revision': {'delta_eps', 'window_weeks', 'quarters', 'base_snapshot', 'current_snapshot', 'missing_reason'}}
    for name, keys in shapes.items():
        require(set(a[name]) == keys, 'archive_shape_mismatch:' + name)
    for name, fields in {'pre_announce': ('value', 'price_pre_announce'), 'ntm': ('value',),
                         'ttm': ('eps',), 'revision': ('delta_eps',)}.items():
        for field in fields:
            real(a[name][field])
    real(a['unit_factor'])
    integer(a['revision']['window_weeks'], 1)
    for name in ('pre_announce', 'ntm'):
        past(a[name]['snapshot_date'])
    for name in ('base_snapshot', 'current_snapshot'):
        past(a['revision'][name])
    pre = a['pre_announce']['snapshot_date']
    if pre and a['eps_window']:
        require(pre < a['eps_window'][-1][1], 'pre_announce_leak')
    for q in a['ntm']['quarters']:
        require(len(q) == 3, 'invalid_ntm_quarter')
        day(q[0])
        real(q[1], nullable=False)
        if q[2] is not None:
            integer(q[2])
    for k in ('ttm', 'revision'):
        for q in a[k]['quarters']:
            day(q)
    _strings(a['statement_conflicts'])


def _scheme_types(s):
    # Validate archived configuration, never substitute the current registry.
    scheme = s.scheme
    require(type(scheme.pe_redflag_demotes) is bool, 'invalid_boolean')
    for field in ('min_quarters', 'new_listing_days', 'group_min_members'):
        integer(getattr(scheme, field))
    for field in ('min_coverage', 'winsor_k', 'z_clip', 'composite_clip', 'grade_strict', 'grade_full',
                  'new_listing_min_revenue_yoy', 'new_listing_min_gm', 'new_listing_min_score', 'pe_redflag_pct'):
        real(getattr(scheme, field), nullable=False)
    for _, weight in scheme.weights:
        real(weight, nullable=False)
    for percentile, bonus in scheme.slope_tiers:
        real(percentile, nullable=False)
        real(bonus, nullable=False)
    for spec in s.factor_specs.values():
        integer(spec.min_quarters)
        require(spec.params_timing in ('quarterly', 'weekly'), 'invalid_parameter_timing')


def validate_board(s: StoredBoard) -> None:
    """Validate detached DTOs too; prepare_board is not a trusted write bypass."""
    m, b = s.meta, s.board
    finite_tree(asdict(s))
    _scheme_types(s)
    check_key(key_for(s))
    require(m.mode in ('live', 'replay'), 'invalid_mode')
    utc(m.computed_at)
    require(re.fullmatch(r'[0-9a-fA-F]{40}', m.git_sha) is not None, 'invalid_git_sha')
    if m.quarter_label is not None:
        require(re.fullmatch(r'\d{4}Q[1-4]', m.quarter_label) is not None, 'invalid_quarter_label')
    if m.board_type == 'quarter_final':
        require(m.quarter_label is not None and m.frozen_at is not None, 'missing_freeze_metadata')
        require(b.as_of <= utc(m.frozen_at)[:10], 'freeze_before_asof')
        require(instant(m.frozen_at) <= instant(m.computed_at), 'freeze_after_computation')
        require(b.frozen_as_of is None or (b.frozen_as_of == b.as_of and
                b.frozen_params_version == b.params.params_version), 'old_params_on_freeze')
    else:
        require(m.frozen_at is None, 'weekly_frozen_at')
    evidence = m.source_evidence
    require(set(evidence) == {'source_id', 'observed_at', 'snapshot_sha256', 'profile_observed_at'},
            'source_evidence_shape')
    require(isinstance(evidence['source_id'], str) and bool(evidence['source_id'].strip()), 'empty_source_id')
    for k in ('observed_at', 'profile_observed_at'):
        if evidence[k] is not None:
            utc(evidence[k])
    if evidence['snapshot_sha256'] is not None:
        require(re.fullmatch(r'[0-9a-fA-F]{64}', evidence['snapshot_sha256']) is not None, 'invalid_snapshot_hash')
    if m.mode == 'live':
        require(evidence['observed_at'] is not None and utc(evidence['observed_at'])[:10] <= b.as_of,
                'live_observation_after_asof')
    _strings(m.members)
    require(all(m.members) and len(set(m.members)) == len(m.members), 'invalid_members')
    _missing(m.errors)
    sets = []
    for entries, getter in ((s.stocks, lambda x: x['symbol']), (s.factors, lambda x: x.symbol),
                            (b.rows, lambda x: x.symbol)):
        symbols = [getter(e) for e in entries]
        require(all(isinstance(x, str) and x for x in symbols) and len(set(symbols)) == len(symbols),
                'duplicate_or_invalid_symbol')
        sets.append(set(symbols))
    require(sets[0] == sets[1] == sets[2], 'stock_factor_score_mismatch')
    require(not sets[0] & set(m.errors) and sets[0] | set(m.errors) == set(m.members), 'member_reconciliation')
    require(b.scheme_id == s.scheme.scheme_id and b.scheme_hash == scheme_hash(s.scheme, s.factor_specs),
            'scheme_identity_mismatch')
    require(set(s.factor_specs) == {f for f, _ in s.scheme.weights}, 'factor_specs_mismatch')
    param = params_from_dict(params_to_dict(b.params))
    require((param.as_of, param.scheme_id, param.scheme_hash, param.code_version) ==
            (b.as_of, b.scheme_id, b.scheme_hash, b.code_version), 'parameter_identity_mismatch')
    integer(param.n_rankable)
    for p in param.winsor.values():
        for value in asdict(p).values():
            real(value, nullable=False)
        require(p.lo <= p.hi and p.sigma > 0, 'invalid_winsor')
    for ref in param.reference.values():
        require(len(ref) >= 2 and tuple(sorted(ref)) == ref, 'invalid_reference')
        for value in ref:
            real(value, nullable=False)
    if param.slope_bounds is not None:
        for value in param.slope_bounds:
            real(value, nullable=False)
    require(type(b.params_bootstrap) is bool, 'invalid_boolean')
    require((b.frozen_as_of is None) == (b.frozen_params_version is None) == b.params_bootstrap,
            'frozen_origin_mismatch')
    if b.frozen_as_of is not None:
        require(day(b.frozen_as_of) <= b.as_of, 'frozen_from_future')
    stocks = {p['symbol']: p for p in s.stocks}
    for f in s.factors:
        p = stocks[f.symbol]
        _check_stock(p, b.as_of)
        require(f.as_of == b.as_of, 'factor_date_mismatch')
        for field in ('sector', 'industry', 'current_fiscal', 'pit_basis'):
            require(getattr(f, field) == p[field], 'factor_packet_mismatch:' + field)
        integer(f.disclosed_quarters)
        if f.listing_days is not None:
            # Raw age can be negative: profile IPO metadata is not sliced by as_of.
            require(type(f.listing_days) is int, 'invalid_listing_age')
        for value in (*f.values.values(), *f.aux.values()):
            real(value)
        _missing(f.missing)
        for seq in (f.labels, f.packet_flags):
            _strings(seq)
    ranks = []
    for r in b.rows:
        require(r.status in ('ranked', 'observe', 'excluded'), 'invalid_status')
        require(r.grade is None or r.grade in ('STRICT', 'FULL', 'BELOW'), 'invalid_grade')
        if r.status == 'ranked':
            integer(r.rank, 1)
            require(r.grade is not None and r.score is not None, 'incomplete_ranked_row')
            ranks.append(r.rank)
        else:
            require(r.rank is None, 'nonranked_rank')
        for field in SCORE_REALS:
            real(getattr(r, field), nullable=field != 'coverage')
        for value in (*r.values.values(), *r.family_scores.values()):
            real(value)
        for mapping in (r.z, r.weights, r.contributions):
            for value in mapping.values():
                real(value, nullable=False)
        require(set(r.z) == set(r.weights) == set(r.contributions) and set(r.z) <= set(r.values),
                'score_factor_keys_mismatch')
        require(set(r.values) == {f for f, _ in s.scheme.weights}, 'scheme_values_mismatch')
        _missing(r.missing)
        require(set(r.missing) <= set(r.values), 'unknown_missing_factor')
        _strings(r.demotions)
        _strings(r.badges)
    require(sorted(ranks) == list(range(1, len(ranks) + 1)), 'noncontiguous_ranks')
    require(set(b.counts) == {'ranked', 'observe', 'excluded'}, 'counts_keys_mismatch')
    for status, count in b.counts.items():
        integer(count)
        require(count == sum(r.status == status for r in b.rows), 'counts_mismatch')


def content_body(s):
    d = asdict(normalize_board(s))
    for k in ('computed_at', 'git_sha'):
        del d['meta'][k]
    for k in ('description', 'changes'):
        del d['scheme'][k]
    return d


def content_hash(s):
    return hashlib.sha256(canonical(content_body(s)).encode('utf-8')).hexdigest()


def input_records(s):
    """(symbol, input_json, factor_meta_json) per stock: the scheme-independent part shared by a week's boards."""
    meta = {}
    for f in s.factors:
        d = asdict(f)
        del d['values'], d['missing']
        meta[f.symbol] = canonical(d)
    return [(p['symbol'], canonical(p), meta[p['symbol']]) for p in s.stocks]


def input_hash(records):
    return hashlib.sha256(canonical(records).encode('utf-8')).hexdigest()


def validate_batch(boards):
    require(bool(boards), 'empty_batch')
    keys = [key_for(b) for b in boards]
    require(len(set(keys)) == len(keys), 'duplicate_board_key')
    first = boards[0]
    first_inputs = None
    for s in boards:
        validate_board(s)
        require((s.board.as_of, s.board.code_version, s.meta.mode, s.meta.members, s.meta.errors,
                 s.meta.source_evidence) ==
                (first.board.as_of, first.board.code_version, first.meta.mode, first.meta.members,
                 first.meta.errors, first.meta.source_evidence), 'inconsistent_batch')
        # Every scheme of one week is scored on the same packets and raw factor rows.
        inputs = canonical([input_records(s), [(f.symbol, canonical(f.values), canonical(f.missing))
                                               for f in s.factors]])
        first_inputs = first_inputs or inputs
        require(inputs == first_inputs, 'batch_inputs_mismatch')
        if s.meta.board_type == 'quarter_final':
            weekly_key = replace(key_for(s), board_type='weekly')
            require(weekly_key in keys, 'freeze_requires_weekly_in_batch')
            w = boards[keys.index(weekly_key)]
            a, b = content_body(s), content_body(w)
            for body in (a, b):
                for field in ('board_type', 'quarter_label', 'frozen_at'):
                    del body['meta'][field]
            require(canonical(a) == canonical(b), 'freeze_weekly_content_mismatch')
