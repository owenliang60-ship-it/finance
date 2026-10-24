"""Weekly orchestration and resumable quarter freezes over M4/M6/M8.

The caller holds the job/resource locks. market.db is read-only. Each result
database owns its adjacent .state directory; no global research/production state.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.prosperity_gates import CST, fundamental_refreshed, forward_state, input_health, integrity, instant
from src.data.prosperity_storage import BoardKey, BoardMeta, canonical, content_hash, key_for, prepare_board, validate_batch
from terminal.prosperity.factors import compute_factor_row
from terminal.prosperity.freeze import candidate_saturdays, due_quarter, freeze_coverage, freeze_decision, quarter_labels, saturday as parse_saturday
from terminal.prosperity.inputs import build_packets
from terminal.prosperity.kernel import rankable, score_board
from terminal.prosperity.schemes import SCHEMES, scheme_hash
from terminal.prosperity.version import code_version


def utcnow():
    return datetime.now(timezone.utc)


def stamp(at):
    return instant(at).isoformat().replace('+00:00', 'Z')


def this_saturday(now):
    d = instant(now).astimezone(CST).date()
    return d + timedelta(days=5-d.weekday())


def store_path(store):
    return Path(store._conn.execute('PRAGMA database_list').fetchone()[2]).resolve()


def state_path(store):
    return Path(str(store_path(store)) + '.state')


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    keys: tuple = ()
    error: str = ''
    detail: dict = None


ChainOutcome = RunOutcome


class InputHealthFailure(ValueError):
    pass


def _keys(as_of, version, board_type='weekly'):
    return tuple(BoardKey(board_type, as_of, scheme_hash(s), version) for s in SCHEMES.values())


def _existing(store, as_of, version):
    keys = _keys(as_of, version)
    boards = [store.get_board(k) for k in keys]
    if any(boards) and not all(boards):
        raise ValueError('partial_week')
    return tuple(boards) if all(boards) else ()


def _frozen(store, version):
    by_scheme = []
    for s in SCHEMES.values():
        by_scheme.append({store.get_board(k).meta.quarter_label: k.as_of for k in store.list_boards(
            scheme_hash=scheme_hash(s), code_version=version, board_type='quarter_final')})
    if any(x != by_scheme[0] for x in by_scheme):
        raise ValueError('partial_freeze_chain')
    return by_scheme[0]


def _previous(store, as_of, version=None):
    sql = "SELECT board_type,as_of,scheme_hash,code_version FROM board WHERE scheme_id='F1' AND as_of<?"
    args = [as_of]
    if version is not None:
        sql += " AND board_type='quarter_final' AND code_version=?"
        args.append(version)
    row = store._conn.execute(sql + ' ORDER BY as_of DESC,computed_at DESC,board_id DESC LIMIT 1', args).fetchone()
    return None if row is None else store.get_board(BoardKey(*tuple(row))).board.counts['ranked']


def _identity(as_of, quarter, version):
    return {'as_of': as_of, 'quarter_label': quarter, 'code_version': version,
            'schemes': {sid: scheme_hash(s) for sid, s in SCHEMES.items()}}


def _receipt_path(store, identity):
    digest = hashlib.sha256(canonical(identity).encode()).hexdigest()
    return state_path(store) / 'candidates' / (identity['as_of'] + '-' + digest + '.json')


def _read_receipt(store, identity):
    path = _receipt_path(store, identity)
    if not path.exists():
        return None
    obj = json.loads(path.read_text())
    if set(obj) != {'payload', 'sha256'} or hashlib.sha256(canonical(obj['payload']).encode()).hexdigest() != obj['sha256']:
        raise ValueError('candidate_checksum_mismatch')
    p = obj['payload']
    if (p['schema_version'] != 1 or p['identity'] != identity or p['health']['status'] != 'pass'
            or p['meta']['mode'] not in ('live', 'replay')):
        raise ValueError('candidate_identity_or_health_invalid')
    rows = p['evidence']
    symbols = [r[0] for r in rows]
    numerator = sum(bool(r[1] and r[1] >= identity['quarter_label']) for r in rows)
    if not rows or len(set(symbols)) != len(symbols) or p['coverage'] != numerator / len(rows):
        raise ValueError('candidate_coverage_invalid')
    if freeze_decision(identity['as_of'], identity['quarter_label'], p['coverage']) != p['decision']:
        raise ValueError('candidate_decision_invalid')
    if bool(p['hashes']) != (p['decision'] is not None):
        raise ValueError('candidate_hashes_invalid')
    return p


def _write_receipt(store, payload):
    path = _receipt_path(store, payload['identity'])
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {'payload': payload, 'sha256': hashlib.sha256(canonical(payload).encode()).hexdigest()}
    fd, tmp = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(canonical(obj))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)   # publish without replacing a completed record
        except FileExistsError:
            if canonical(_read_receipt(store, payload['identity'])) != canonical(payload):
                raise ValueError('candidate_content_conflict')
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(tmp)


def _hashes(boards):
    return {canonical(asdict(key_for(b))): content_hash(b) for b in boards}


def _data(market, as_of, mode, cutoff, builder, cache):
    packets, meta = builder(market, as_of, mode=mode,
                            observed_at=stamp(cutoff) if mode == 'live' else None,
                            cutoff=cutoff, cache=cache)
    errors = dict(meta['errors'])
    good, factors = [], []
    for packet in packets:
        try:
            factors.append(compute_factor_row(packet))
            good.append(packet)
        except Exception as exc:
            errors[packet.symbol] = f'factor: {type(exc).__name__}: {exc}'
    names = {r['symbol']: r['company_name'] for r in market._get_conn().execute(
        'SELECT symbol,company_name FROM security_master')}
    return good, factors, {**meta, 'errors': errors}, {p.symbol: names.get(p.symbol) for p in good}


def _meta(market, as_of, mode, cutoff, data, now, git_sha, snapshot_sha256=None):
    packets, _, info, _ = data
    members = tuple(sorted({p.symbol for p in packets} | set(info['errors'])))
    if len(members) != info['members_resolved']:
        raise ValueError('resolved_member_reconciliation')
    return BoardMeta('weekly', None, None, stamp(now()), git_sha, mode, members, info['errors'],
        {'source_id': str(market.db_path.resolve()), 'observed_at': stamp(cutoff) if mode == 'live' else None,
         'snapshot_sha256': snapshot_sha256, 'profile_observed_at': stamp(now())})


def _prepare(store, as_of, version, data, meta, cutoff, quarter=None):
    packets, rows, _, names = data
    boards = []
    for s in SCHEMES.values():
        frozen = None if quarter else store.get_frozen_params(scheme_hash=scheme_hash(s),
                    code_version=version, as_of=as_of, effective_at=stamp(cutoff))
        b = score_board(rows, s, frozen=frozen, as_of=as_of, code_version=version)
        boards.append(prepare_board(replace(meta, quarter_label=quarter), b, s, packets, rows, names=names))
    if quarter:
        boards.extend(replace(b, meta=replace(b.meta, board_type='quarter_final', frozen_at=stamp(cutoff)))
                      for b in tuple(boards))
    validate_batch(boards)
    return tuple(boards)


def _health(data, previous, ranked=None):
    _, rows, meta, _ = data
    count = sum(rankable(r, SCHEMES['F1'])[0] for r in rows) if ranked is None else ranked
    return input_health(meta, rows, count, previous)


def _space(store):
    from src.data.prosperity_backup import check_space
    check_space(store_path(store))


def _evaluate(market, store, as_of, quarter, version, mode, cutoff, builder, cache, now, git_sha,
              previous, data=None, snapshot_sha256=None):
    identity = _identity(as_of, quarter, version)
    receipt = _read_receipt(store, identity)
    if receipt and receipt['decision'] is None:
        return (), receipt, data
    if receipt:
        meta = BoardMeta(**{**receipt['meta'], 'members': tuple(receipt['meta']['members']),
                            'computed_at': stamp(now()), 'git_sha': git_sha})
        cutoff = instant(receipt['cutoff'])
        # Pending work must be recreated from the same mode and semantic inputs.
        data = _data(market, as_of, meta.mode, cutoff, builder, {})
        boards = _prepare(store, as_of, version, data, meta, cutoff, quarter)
        if _hashes(boards) != receipt['hashes']:
            raise ValueError('recovery_input_changed')
    else:
        data = data if data is not None else _data(market, as_of, mode, cutoff, builder, cache)
        health = _health(data, previous)
        if health.status != 'pass':
            raise InputHealthFailure('input_health:' + canonical(asdict(health)))
        packets, rows, _, _ = data
        by_symbol = {p.symbol: p for p in packets}
        coverage, _, _ = freeze_coverage(rows, by_symbol, SCHEMES['F1'], quarter)
        decision = freeze_decision(as_of, quarter, coverage)
        if decision:
            # Packets own the selected values. Whole histories are only useful
            # while probing the next candidate of this quarter, not while M8
            # normalizes/serializes eight boards or while loading another quarter.
            cache.clear()
        meta = _meta(market, as_of, mode, cutoff, data, now, git_sha, snapshot_sha256)
        boards = _prepare(store, as_of, version, data, meta, cutoff, quarter) if decision else ()
        if boards:
            health = _health(data, previous, next(b.board.counts['ranked'] for b in boards if b.scheme.scheme_id == 'F1'))
            if health.status != 'pass':
                raise InputHealthFailure('input_health:' + canonical(asdict(health)))
        receipt = {'schema_version': 1, 'identity': identity, 'cutoff': stamp(cutoff), 'meta': asdict(meta),
                   'health': asdict(health), 'coverage': coverage, 'decision': decision,
                   'evidence': [[r.symbol, by_symbol[r.symbol].quarter_bucket] for r in rows
                                if rankable(r, SCHEMES['F1'])[0]], 'hashes': _hashes(boards)}
        _space(store)
        _write_receipt(store, receipt)
    if not receipt['decision']:
        return (), receipt, data
    _space(store)
    return store.save_batch(boards), receipt, data


def _record(store, label, started, out, version, now):
    path = state_path(store) / 'runs'
    try:
        path.mkdir(parents=True, exist_ok=True)
        payload = {**asdict(out), 'as_of': label, 'code_version': version, 'at': stamp(now()),
                   'seconds': round(time.monotonic()-started, 3)}
        (path / f'{label}-{uuid.uuid4().hex}.json').write_text(canonical(payload))
        return out
    except Exception as exc:
        return replace(out, exit_code=6, error=out.error + '; run_record_failed:' + str(exc))


def _rebuild(market, store, through, start_quarter, version, builder, now, git_sha, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    end = parse_saturday(through)
    labels, cache, keys, evidence = _frozen(store, version), {}, [], []
    for quarter in quarter_labels(start_quarter, f'{end.year:04d}Q{(end.month-1)//3+1}'):
        if time.monotonic() >= deadline:
            raise TimeoutError('freeze_chain_timeout')
        if quarter in labels:
            continue
        for day in candidate_saturdays(quarter):
            if day > through:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('freeze_chain_timeout')
            receipt = _read_receipt(store, _identity(day, quarter, version))
            existing = _existing(store, day, version)
            if existing:
                if receipt and receipt['decision'] is None:
                    evidence.append(receipt)
                    continue
                if all(b.meta.mode == 'live' for b in existing) and receipt is None:
                    continue
                raise ValueError('replay_week_exists_without_freeze')
            cutoff = datetime.combine(parse_saturday(day)+timedelta(days=1), datetime.min.time(), timezone.utc)
            added, item, _ = _evaluate(market, store, day, quarter, version, 'replay', cutoff,
                                       builder, cache, now, git_sha, _previous(store, day, version))
            evidence.append(item)
            keys.extend(added)
            if time.monotonic() >= deadline:
                raise TimeoutError('freeze_chain_timeout')
            if added:
                break
    return ChainOutcome(0, tuple(keys), detail={'candidates': evidence})


def rebuild_freeze_chain(*, market, store, through, start_quarter='2021Q2', now=utcnow,
                         version=None, packet_builder=None, git_sha='0'*40, timeout_seconds=7200):
    started, version = time.monotonic(), version or code_version()
    try:
        day = parse_saturday(through)
        if day > this_saturday(now()) - timedelta(days=7):
            return _record(store, through, started, ChainOutcome(4, error='replay_current_or_future_week'), version, now)
        gate = integrity(market.db_path)
        if gate.status != 'pass':
            raise ValueError('market_integrity:' + canonical(asdict(gate)))
        _space(store)
        out = _rebuild(market, store, through, start_quarter, version, packet_builder or build_packets,
                       now, git_sha, timeout_seconds)
    except Exception as exc:
        out = ChainOutcome(6, error=f'{type(exc).__name__}: {exc}')
    return _record(store, through, started, out, version, now)


def _publish(store, boards, publishers, now):
    keys = tuple(key_for(b) for b in boards)
    if all(store.publication_state(k).published_at for k in keys):
        return RunOutcome(0, keys, detail={'publication': 'already_published'})
    if not publishers:
        return RunOutcome(0, keys, detail={'publication': 'publication_not_configured'})
    try:
        for publish in publishers:
            publish(keys)
        store.mark_published(keys, published_at=stamp(now()))
        return RunOutcome(0, keys, detail={'publication': 'published'})
    except Exception as exc:
        return RunOutcome(5, keys, error=f'{type(exc).__name__}: {exc}')


def run_week(*, saturday, mode, market, store, quality_dir, publishers=(), now=utcnow,
             version=None, packet_builder=None, start_quarter='2021Q2', git_sha='0'*40,
             observed_at=None, snapshot_sha256=None, timeout_seconds=7200):
    started, version = time.monotonic(), version or code_version()
    gates = []
    try:
        day, current = parse_saturday(saturday), this_saturday(now())
        if mode not in ('live', 'replay'):
            raise ValueError('invalid_mode')
        if mode == 'live':
            cutoff = instant(observed_at or now())
            if day != current or instant(now()).astimezone(CST).date() != day or cutoff > instant(now()) or cutoff.astimezone(CST).date() != day:
                raise ValueError('live_requires_current_saturday')
        else:
            cutoff = datetime.combine(day+timedelta(days=1), datetime.min.time(), timezone.utc)
            if day > current-timedelta(days=7) or cutoff > instant(now()) or observed_at is not None:
                raise ValueError('replay_current_or_future_week')
    except (ValueError, TypeError) as exc:
        return _record(store, str(saturday), started, RunOutcome(4, error=str(exc)), version, now)
    try:
        existing = _existing(store, saturday, version)
        if existing:
            if mode == 'live' and any(b.meta.mode != 'live' for b in existing):
                raise ValueError('live_key_owned_by_replay')
            freeze = tuple(store.get_board(k) for k in _keys(saturday, version, 'quarter_final'))
            if any(freeze) and not all(freeze):
                raise ValueError('partial_freeze')
            boards = existing + (freeze if all(freeze) else ())
            out = _publish(store, boards, publishers, now) if mode == 'live' else RunOutcome(0, tuple(key_for(b) for b in boards))
            return _record(store, saturday, started, out, version, now)
        gates.append(integrity(market.db_path))
        if mode == 'live':
            gates.append(fundamental_refreshed(quality_dir, saturday, cutoff))
        if any(g.status != 'pass' for g in gates):
            return _record(store, saturday, started, RunOutcome(3, detail={'gates': [asdict(g) for g in gates]}), version, now)
        builder = packet_builder or build_packets
        chain = rebuild_freeze_chain(market=market, store=store, through=(day-timedelta(days=7)).isoformat(),
                    start_quarter=start_quarter, now=now, version=version, packet_builder=builder,
                    git_sha=git_sha, timeout_seconds=timeout_seconds)
        if chain.exit_code:
            return _record(store, saturday, started, chain, version, now)
        _space(store)
        data = _data(market, saturday, mode, cutoff, builder, {})
        previous = _previous(store, saturday)
        health = _health(data, previous)
        gates.extend((health, forward_state(market, saturday)))
        if health.status != 'pass':
            return _record(store, saturday, started, RunOutcome(3, detail={'gates': [asdict(g) for g in gates]}), version, now)
        quarter = due_quarter(saturday, _frozen(store, version), start_quarter)
        keys = ()
        if quarter:
            keys, _, data = _evaluate(market, store, saturday, quarter, version, mode, cutoff, builder, {},
                                     now, git_sha, previous, data, snapshot_sha256)
        if not keys:
            meta = _meta(market, saturday, mode, cutoff, data, now, git_sha, snapshot_sha256)
            boards = _prepare(store, saturday, version, data, meta, cutoff)
            health = _health(data, previous, next(b.board.counts['ranked'] for b in boards if b.scheme.scheme_id == 'F1'))
            if health.status != 'pass':
                return _record(store, saturday, started, RunOutcome(3, detail={'gates': [asdict(health)]}), version, now)
            _space(store)
            keys = store.save_batch(boards)
        boards = tuple(store.get_board(k) for k in keys)
        out = _publish(store, boards, publishers, now) if mode == 'live' else RunOutcome(0, keys)
        out = replace(out, detail={**(out.detail or {}), 'gates': [asdict(g) for g in gates],
                                  'chain_keys': [asdict(k) for k in chain.keys]})
    except Exception as exc:
        out = RunOutcome(3 if isinstance(exc, InputHealthFailure) else 6,
                         error=f'{type(exc).__name__}: {exc}', detail={'gates': [asdict(g) for g in gates]})
    return _record(store, saturday, started, out, version, now)
