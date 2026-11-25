"""Pure calendar and readiness rules; included in the engine code version."""
from __future__ import annotations

import calendar
import math
import re
from datetime import date, timedelta

from terminal.prosperity.kernel import rankable


def quarter_of(label):
    if not isinstance(label, str) or not re.fullmatch(r'\d{4}Q[1-4]', label):
        raise ValueError('invalid_quarter')
    year, quarter = int(label[:4]), int(label[-1])
    month = quarter * 3
    return date(year, month - 2, 1), date(year, month, calendar.monthrange(year, month)[1])


def quarter_labels(start, end):
    quarter_of(start), quarter_of(end)
    y, q = int(start[:4]), int(start[-1])
    while f'{y:04d}Q{q}' <= end:
        yield f'{y:04d}Q{q}'
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)


def freeze_date(label):
    """Last Saturday of the calendar quarter after `label` (Boss 2026-11-13)."""
    quarter_of(label)
    y, q = int(label[:4]), int(label[-1])
    end = quarter_of(f'{y + (q == 4):04d}Q{1 if q == 4 else q + 1}')[1]
    return (end - timedelta(days=(end.weekday() - 5) % 7)).isoformat()


def candidate_saturdays(label):
    return (freeze_date(label),)


def saturday(value):
    d = date.fromisoformat(value)
    if d.isoformat() != value or d.weekday() != 5:
        raise ValueError('as_of_must_be_saturday')
    return d


def due_quarter(as_of, frozen_labels, start_quarter='2021Q2'):
    d = saturday(as_of)
    end = f'{d.year:04d}Q{(d.month-1)//3+1}'
    return next((q for q in quarter_labels(start_quarter, end)
                 if q not in frozen_labels and freeze_date(q) <= as_of), None)


def freeze_coverage(rows, packets, scheme, label):
    quarter_of(label)
    eligible = [r for r in rows if rankable(r, scheme)[0]]
    if not eligible:
        raise ValueError('zero_freeze_denominator')
    numerator = sum(bool(packets[r.symbol].quarter_bucket and packets[r.symbol].quarter_bucket >= label)
                    for r in eligible)
    return numerator / len(eligible), numerator, len(eligible)


def freeze_decision(as_of, label, coverage):
    """'calendar' on the quarter's freeze date, else None; coverage is validated and recorded, never a gate."""
    saturday(as_of)
    if isinstance(coverage, bool) or not math.isfinite(coverage) or not 0 <= coverage <= 1:
        raise ValueError('invalid_freeze_coverage')
    return 'calendar' if as_of == freeze_date(label) else None
