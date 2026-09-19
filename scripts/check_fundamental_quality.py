#!/usr/bin/env python3
"""Read-only fundamental audit, or bounded collect -> metrics -> re-audit.

No message transport and no raw data deletion. --repair uses the shared writer
lock; --no-lock verifies cron_wrapper's inherited lock. JSON records unresolved
quality independently from HTTP success. Exit 0=completed without actionable
errors (may WARN), 1=unresolved data/repair backlog, 2=execution error, 75=busy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_extended_fundamentals import FileLock
from scripts.prepare_premium_fundamentals import inherited_writer_lock
from src.data.fundamental_collector import collect_fundamentals_for_symbol, _safe_detail
from src.data.market_store import MarketStore
from src.data.metrics_calculator import compute_all_metrics

# These describe applicability / missing event evidence, not repairs that a
# successful financial statement fetch can be claimed to have resolved.
_NON_REPAIR_ISSUES = {'quarter_gap', 'missing_quarters', 'event_evidence_unknown'}


def _remaining_issues(audit, symbol):
    return set(audit['symbols'][symbol]['issues']) - _NON_REPAIR_ISSUES


def run_quality(store, *, repair=False, client=None, lock=None, as_of=None,
                max_targets=50, refresh_days=30, auditor=None):
    if max_targets < 1:
        raise ValueError('max_targets must be positive')
    if repair and lock is None:
        raise ValueError('repair requires an explicit writer lock')
    if repair and not lock.acquire():
        return {'schema_version': 1, 'status': 'BUSY', 'exit_code': 75}
    try:
        if auditor is None:
            from src.data.fundamental_quality import audit_fundamentals
            auditor = audit_fundamentals
        before = auditor(store, as_of=as_of or datetime.now(timezone.utc).isoformat(),
                         refresh_days=refresh_days)
        if not repair:
            return {'schema_version': 1, 'mode': 'audit', 'status': before['status'],
                    'exit_code': 1 if before['status'] == 'FAIL' else 0, 'audit': before}
        repairs = before['repair_targets']
        verification = [s for s in before['verification_targets'] if s not in repairs]
        candidates = repairs + verification
        selected = candidates[:max_targets]
        errors = []
        requested_ok = []
        for symbol in selected:
            try:
                statuses = collect_fundamentals_for_symbol(
                    symbol, store=store, client=client, limit_quarters=8,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                )
                print(json.dumps({'symbol': symbol, 'datasets': statuses}, sort_keys=True), flush=True)
                if any(statuses.get(k) != 'ok' for k in ('income', 'balance', 'cashflow')):
                    errors.append({'symbol': symbol, 'stage': 'collection', 'statuses': statuses})
                elif 'fetch_failed' in statuses.values():
                    errors.append({'symbol': symbol, 'stage': 'collection', 'statuses': statuses})
                else:
                    requested_ok.append(symbol)
            except Exception as exc:
                errors.append({'symbol': symbol, 'stage': 'collection', 'error': _safe_detail(exc)})
        computed = {}
        if requested_ok:
            failures = []
            computed = compute_all_metrics(requested_ok, store=store, collect_failures=failures)
            for symbol in requested_ok:
                if symbol in failures or not computed.get(symbol):
                    errors.append({'symbol': symbol, 'stage': 'metrics', 'error': 'metrics computation failed'})
        after = auditor(store, as_of=datetime.now(timezone.utc).isoformat(), refresh_days=refresh_days)
        if before['universe']['symbols'] != after['universe']['symbols']:
            raise RuntimeError('quality audit universe changed during locked repair')
        error_symbols = {e['symbol'] for e in errors}
        unresolved = [s for s in selected if s in error_symbols or _remaining_issues(after, s)]
        resolved = [s for s in selected if s in repairs and s not in unresolved]
        periodic_verified = [s for s in selected if s in verification and s not in unresolved]
        # Persistent issues that the auditor explicitly puts into cooldown remain
        # WARN/unresolved, never truly_resolved. Only errors or still-due work
        # fail this run; a current source with no newer filing is not an API error.
        rc = 1 if errors or after['repair_targets'] else 0
        return {
            'schema_version': 1, 'mode': 'repair',
            'status': 'FAIL' if rc else after['status'], 'exit_code': rc,
            'before': before, 'after': after, 'requested': selected,
            'requests_successful': requested_ok, 'truly_resolved': resolved,
            'periodic_verified': periodic_verified, 'unresolved': unresolved,
            'deferred_by_budget': candidates[max_targets:], 'errors': errors,
        }
    finally:
        if repair:
            lock.release()


def write_report(report, path):
    path = Path(path)
    content = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.'+path.name+'.', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            f.write(content+'\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repair', action='store_true')
    parser.add_argument('--no-lock', action='store_true')
    parser.add_argument('--as-of')
    parser.add_argument('--max-targets', type=int, default=50)
    parser.add_argument('--refresh-days', type=int, default=30)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    if args.max_targets < 1 or args.refresh_days < 1:
        parser.error('max-targets and refresh-days must be positive')
    if args.repair and args.as_of:
        try:
            supplied = datetime.fromisoformat(args.as_of.replace('Z', '+00:00'))
            if supplied.tzinfo:
                supplied = supplied.astimezone(timezone.utc)
            if supplied.date() != datetime.now(timezone.utc).date():
                parser.error('repair as-of must be the current UTC day')
        except ValueError:
            parser.error('invalid --as-of')
    lock = None
    store = None
    try:
        if args.repair:
            lock = inherited_writer_lock() if args.no_lock else FileLock()
            if not lock.acquire():
                print('fundamental quality: writer lock busy (rc=75)')
                return 75
        store = MarketStore(read_only=not args.repair)
        client = None
        if args.repair:
            from src.data.fmp_client import fmp_client
            client = fmp_client
        report = run_quality(store, repair=args.repair, client=client, lock=lock,
                             as_of=args.as_of, max_targets=args.max_targets,
                             refresh_days=args.refresh_days)
        if args.report:
            write_report(report, args.report)
        assessment = report.get('after', report.get('audit', {}))
        summary = {k: report[k] for k in ('schema_version', 'mode', 'status', 'exit_code')}
        summary.update(coverage=assessment.get('coverage'), report=str(args.report) if args.report else None,
                       repair_due=len(assessment.get('repair_targets', [])),
                       issue_counts={k: len(v) for k, v in assessment.get('issues', {}).items()})
        for field in ('requested', 'requests_successful', 'truly_resolved',
                      'periodic_verified', 'unresolved', 'deferred_by_budget', 'errors'):
            if field in report:
                summary[field + '_count'] = len(report[field])
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        return report['exit_code']
    except Exception as exc:
        print(json.dumps({'status': 'ERROR', 'exit_code': 2, 'error': _safe_detail(exc)}), flush=True)
        return 2
    finally:
        if store is not None:
            store.close()
        if lock is not None:
            lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
