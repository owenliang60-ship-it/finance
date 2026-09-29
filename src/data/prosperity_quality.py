"""Read-only input guards; unknowns never become fabricated dates or EPS values.

Split checks inspect the *stored* (possibly restated) series. Their evidence is
retrospective, not a claim that a historical investor knew future split metadata.
"""
from __future__ import annotations

import math
import statistics
from datetime import date

SAME_QUARTER_DAYS = 20


def _date(value):
    try:
        return date.fromisoformat(value[:10]) if isinstance(value, str) else None
    except ValueError:
        return None


def _earliest_earnings_day(fiscal: date, earnings_rows: list[dict]) -> date | None:
    days = []
    for r in earnings_rows:
        f, a, eps = _date(r.get('fiscal_date')), _date(r.get('announce_date')), r.get('eps_actual')
        if (f and a and a > f and abs((f - fiscal).days) <= SAME_QUARTER_DAYS
                and isinstance(eps, (int, float)) and not isinstance(eps, bool) and math.isfinite(eps)):
            days.append(a)
    return min(days) if days else None


def statement_availability(row: dict, *, earnings_rows: list[dict] | None = None) -> dict:
    """Validated public date, optionally floored at the earliest reported EPS.

    This is a conservative historical lower bound using the stored evidence,
    not proof that every filing legally follows a separate earnings release.
    An announcement never fills an unknown public date.
    """
    fiscal = _date(row.get('date'))
    rejected = []
    if fiscal:
        for key in ('accepted_date', 'filing_date'):
            day = _date(row.get(key))
            if day and day > fiscal:
                floor = _earliest_earnings_day(fiscal, earnings_rows or [])
                available = max(day, floor) if floor else day
                if available > day:
                    rejected.append('statement_date_before_earnings')
                return {'public_available_at': available.isoformat(), 'source': key,
                        'reported_public_date': day.isoformat(),
                        'earnings_floor': floor.isoformat() if floor else None,
                        'issues': rejected}
            if row.get(key):
                rejected.append(key + '_invalid_or_placeholder')
    return {'public_available_at': None, 'source': None,
            'issues': rejected + ['statement_availability_unknown']}


def statement_known_on(row: dict, *, observed_at: str | None = None,
                       earnings_rows: list[dict] | None = None) -> str | None:
    # An observed archive is independent evidence: do not delay it to a
    # possibly later stored earnings event. The floor is historical-only.
    public = statement_availability(row, earnings_rows=earnings_rows if observed_at is None else None)['public_available_at']
    if observed_at is None:
        return public
    observed, fiscal = _date(observed_at), _date(row.get('date'))
    if observed is None:
        raise ValueError('invalid snapshot observation date')
    if fiscal is None or fiscal >= observed:
        return None
    # A verified archive proves known-by, never the original publication time.
    return min(public, observed.isoformat()) if public else observed.isoformat()


def resolve_eps_quarters(rows: list[dict], as_of: str) -> dict:
    bound = _date(as_of)
    if bound is None:
        raise ValueError('invalid as_of')
    mapped = [r for r in rows if r.get('eps_actual') is not None
              and _date(r.get('announce_date')) is not None
              and _date(r['announce_date']) <= bound and _date(r.get('fiscal_date')) is not None]
    mapped.sort(key=lambda r: (r['fiscal_date'][:10], r['announce_date']), reverse=True)
    groups = []
    for row in mapped:
        # Compare with the first (newest) fiscal date, never chain neighbours.
        if groups and (_date(groups[-1][0]['fiscal_date']) - _date(row['fiscal_date'])).days <= SAME_QUARTER_DAYS:
            groups[-1].append(row)
        else:
            groups.append([row])
    quarters, issues, duplicates = [], [], []
    for group in groups:
        fiscal = group[0]['fiscal_date'][:10]
        reasons = []
        values = [r['eps_actual'] for r in group]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in values):
            reasons.append('eps_invalid_actual')
        elif not all(math.isclose(v, values[0], rel_tol=1e-9, abs_tol=1e-12) for v in values):
            reasons.append('eps_conflicting_quarter')
        if any(_date(r['fiscal_date']) >= _date(r['announce_date']) for r in group):
            reasons.append('eps_invalid_announcement_time')
        sources = [{k: r.get(k) for k in ('fiscal_date', 'announce_date', 'eps_actual', 'match_method')}
                   for r in group]
        for reason in reasons:
            issues.append({'reason': reason, 'fiscal_date': fiscal, 'sources': sources})
        if len(group) > 1:
            duplicates.append(fiscal)
        quarters.append({'fiscal_date': fiscal, 'eps_actual': None if reasons else values[0],
                         'announce_date': min(r['announce_date'] for r in group),
                         'issues': reasons, 'sources': sources})
    return {'quarters': quarters, 'issues': issues, 'dup_fiscal': sorted(duplicates)}


def split_basis_audit(quarters: list[dict], income_rows: list[dict], splits: list[dict]) -> dict:
    """Find a local unit-scale discontinuity corroborated by a known split.

    Three immediately adjacent comparable quarters on each side must form
    stable street/GAAP ratios. GAAP is a unit-scale cross-check, never a
    replacement for street EPS. This heuristic reports suspects, not repairs.
    """
    pairs = []
    for q in sorted(quarters, key=lambda q: q['fiscal_date']):
        eps = q.get('eps_actual')
        matches = [r for r in income_rows if _date(r.get('date')) and
                   abs((_date(r['date']) - _date(q['fiscal_date'])).days) <= SAME_QUARTER_DAYS]
        if len(matches) != 1:
            continue
        gaap = matches[0].get('eps_diluted')
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) > 1e-9
                   for v in (eps, gaap)) or eps * gaap <= 0:
            continue
        pairs.append({'fiscal_date': q['fiscal_date'], 'ratio': eps / gaap,
                      'street_eps': eps, 'gaap_eps_diluted': gaap})
    events, ignored_events = [], []
    for s in splits:
        num, den = s.get('numerator'), s.get('denominator')
        if (_date(s.get('date')) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                       and math.isfinite(v) and 1 <= v <= 20 and v == int(v)
                                       for v in (num, den)) and num != den):
            events.append(dict(s, ratio=num / den))
        else:
            ignored_events.append({'date': s.get('date'), 'numerator': num, 'denominator': den,
                                   'reason': 'not_small_integer_split_ratio'})
    base = {'issues': [], 'paired_quarters': len(pairs), 'total_quarters': len(quarters),
            'eligible_split_events': len(events), 'ignored_split_events': ignored_events,
            'basis': 'retrospective_stored_series_diagnostic'}
    if not events:
        return dict(base, status='no_eligible_split_events' if splits else 'split_metadata_unknown')
    if len(pairs) < 6:
        return dict(base, status='insufficient_basis_pairs')
    windows = 0
    for i in range(3, len(pairs) - 2):
        block = pairs[i-3:i+3]
        # Do not bridge a missing/conflicting EPS quarter to create evidence.
        if any(not 60 <= (_date(b['fiscal_date']) - _date(a['fiscal_date'])).days <= 120
               for a, b in zip(block, block[1:])):
            continue
        left, right = block[:3], block[3:]
        lm = statistics.median(r['ratio'] for r in left)
        rm = statistics.median(r['ratio'] for r in right)
        windows += 1
        if max([abs(r['ratio'] / lm - 1) for r in left] +
               [abs(r['ratio'] / rm - 1) for r in right]) > .35:
            continue
        # A split match must describe a real, separated change, not a flat
        # ratio inside a broad tolerance around a small (e.g. 5:4) split.
        if not (min(r['ratio'] for r in left) > max(r['ratio'] for r in right) or
                max(r['ratio'] for r in left) < min(r['ratio'] for r in right)):
            continue
        change = math.log(lm / rm)
        for event in events:
            # Old splits before the stored series cannot explain a new boundary.
            if event['date'][:10] < pairs[0]['fiscal_date']:
                continue
            ratio = event['ratio']
            event_change = math.log(ratio)
            tolerance = min(math.log(1.25), abs(event_change) * .25)
            if min(abs(change - event_change), abs(change + event_change)) <= tolerance:
                base['issues'].append({'reason': 'eps_split_basis_suspect',
                    'boundary_fiscal': right[0]['fiscal_date'],
                    'fiscal_before': left[-1]['fiscal_date'], 'split_date': event['date'],
                    'split_ratio': ratio, 'left_median_ratio': lm, 'right_median_ratio': rm,
                    'evidence': block})
    return dict(base, status='suspect' if base['issues'] else
                'no_suspect_detected' if windows else 'no_comparable_windows')
