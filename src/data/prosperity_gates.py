"""Read-only weekly gates. Unavailable evidence is never a pass."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping

from terminal.prosperity.kernel import rankable
from terminal.prosperity.schemes import SCHEMES

CST = timezone(timedelta(hours=8))


def instant(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('timezone_required')
    return at.astimezone(timezone.utc)


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    detail: Mapping[str, Any]


def integrity(market_db):
    try:
        with closing(sqlite3.connect(Path(market_db).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            rows = [r[0] for r in conn.execute('PRAGMA quick_check')]
        return GateResult('integrity', 'pass' if rows == ['ok'] else 'fail', {'result': rows})
    except Exception as exc:
        return GateResult('integrity', 'unknown', {'reason': str(exc)})


def fundamental_refreshed(quality_dir, saturday, cutoff):
    try:
        bound = instant(cutoff)
        paths = sorted(Path(quality_dir).glob('fundamentals-*.json'), reverse=True)
        if not paths:
            return GateResult('fundamental_refreshed', 'unknown', {'reason': 'report_missing'})
        failures, broken = [], []
        for path in paths:
            try:
                after = json.loads(path.read_text())['after']
                at = instant(after['as_of'])
                ratio = after['coverage']['fundamental_ready']['ratio']
                if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
                    raise ValueError('invalid_ratio')
                detail = {'path': str(path), 'as_of': at.isoformat(), 'ratio': ratio}
                reason = ('report_wrong_week' if at.astimezone(CST).date().isoformat() != saturday
                          else 'report_after_snapshot' if at > bound
                          else 'coverage_below_floor' if ratio < .90 else None)
                if reason is None:
                    return GateResult('fundamental_refreshed', 'pass', detail)
                failures.append({**detail, 'reason': reason})
            except Exception as exc:
                broken.append({'path': str(path), 'error': str(exc)})
        if broken:
            return GateResult('fundamental_refreshed', 'unknown', {'reason': 'report_invalid', 'errors': broken})
        return GateResult('fundamental_refreshed', 'fail', failures[0])
    except Exception as exc:
        return GateResult('fundamental_refreshed', 'unknown', {'reason': str(exc)})


def forward_state(store, saturday):
    try:
        row = store._get_conn().execute('SELECT status,completed_at FROM fmp_forward_runs '
              "WHERE snapshot_date=? AND run_kind='weekly'", (saturday,)).fetchone()
        detail = dict(row) if row else {'reason': 'run_missing'}
        return GateResult('forward', 'pass' if row and row['status'] == 'complete' else 'unknown', detail)
    except Exception as exc:
        return GateResult('forward', 'unknown', {'reason': str(exc)})


def member_floor(ranked_now, previous_ranked):
    if type(ranked_now) is not int or ranked_now < 0 or (previous_ranked is not None and
            (type(previous_ranked) is not int or previous_ranked < 0)):
        return GateResult('member_floor', 'unknown', {'reason': 'invalid_count'})
    if previous_ranked is None:
        return GateResult('member_floor', 'pass', {'reason': 'no_previous', 'ranked': ranked_now})
    return GateResult('member_floor', 'pass' if ranked_now >= previous_ranked * .90 else 'fail',
                      {'ranked': ranked_now, 'previous_ranked': previous_ranked})


def input_health(meta, rows, f1_ranked, previous_ranked):
    try:
        members = meta['members_resolved']
        errors = meta['errors']
        denominator = sum(rankable(r, SCHEMES['F1'])[0] for r in rows)
        floor = member_floor(f1_ranked, previous_ranked)
        passed = (type(members) is int and members > 0 and isinstance(errors, dict) and
                  len(errors) <= members * .01 and f1_ranked > 0 and denominator > 0 and floor.status == 'pass')
        return GateResult('input_health', 'pass' if passed else 'fail',
                          {'members': members, 'errors': errors, 'ranked': f1_ranked,
                           'denominator': denominator, 'member_floor': dict(floor.detail)})
    except Exception as exc:
        return GateResult('input_health', 'unknown', {'reason': str(exc)})
