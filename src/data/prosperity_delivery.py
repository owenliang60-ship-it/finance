"""M13 Telegram delivery: after a published live week, send the web page and the pool changes.

Plan: docs/plans/2026-11-03-prosperity-m13-delivery.md. Runs after run_week inside the
finance_prosperity job (both locks held). Read-only on every database: it works on fresh
`.backup` snapshots and calls the M11/M12 CLIs as subprocesses. One JSON ledger per week
records each sent item, so a re-run (20:30) sends only what is missing. Not part of the
engine code_version.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Callable, List, Optional, Sequence, Tuple

from src import telegram_bot
from src.data.prosperity_backup import check_space
from src.data.prosperity_pool import POOL_SCHEME_ID, load_prosperity_pool
from src.data.prosperity_storage import BoardKey
from src.data.prosperity_store import ProsperityStore
from terminal.prosperity.schemes import SCHEMES, scheme_hash

ROOT = Path(__file__).resolve().parents[2]

WEB_BUDGET_BYTES = 12_000_000          # north-star §4.2 page budget: warn, still send
WEB_SEND_LIMIT_BYTES = 45_000_000      # Telegram Bot API caps uploads at 50 MB
CAPTION_LIMIT = 1024
WEB_TIMEOUT_SECONDS = 600
HEALTH_TIMEOUT_SECONDS = 1800
UPLOAD_TIMEOUT_SECONDS = 300
KEEP_OUTPUT_WEEKS = 2
VERSION_CHANGED = object()             # last_health_freeze: the last sent report used another code_version


def ledger_path(delivery_dir, as_of: str) -> Path:
    return Path(delivery_dir) / 'ledger' / f'{as_of}.json'


def read_ledger(delivery_dir, as_of: str) -> dict:
    path = ledger_path(delivery_dir, as_of)
    if not path.is_file():
        return {'as_of': as_of, 'items': {}}
    return json.loads(path.read_text(encoding='utf-8'))


def write_ledger(delivery_dir, ledger: dict) -> None:
    target = ledger_path(delivery_dir, ledger['as_of'])
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.ledger-', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(ledger, handle, ensure_ascii=False, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        temp = None
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temp is not None:
            Path(temp).unlink(missing_ok=True)


def previous_ledger(delivery_dir, as_of: str) -> Optional[dict]:
    """The latest ledger before as_of that recorded pool members (weeks without a pool text are skipped)."""
    folder = Path(delivery_dir) / 'ledger'
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob('*.json'), reverse=True):
        if path.stem >= as_of:
            continue
        ledger = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(ledger.get('pool_members'), list):
            return ledger
    return None


def pool_diff(previous: Optional[Sequence[str]], current: Sequence[str]) -> Tuple[List[str], List[str]]:
    if previous is None:
        return [], []
    before, now = set(previous), set(current)
    return sorted(now - before), sorted(before - now)


def md_escape(text: str) -> str:
    """Escape Telegram legacy Markdown (send_message always uses parse_mode=Markdown)."""
    for ch in ('\\', '_', '*', '[', '`'):
        text = text.replace(ch, '\\' + ch)
    return text


def web_caption(*, as_of: str, strict: int, full: int, pool_count: int, size_bytes: int) -> str:
    """Plain-text caption (send_document sends no parse_mode)."""
    caption = f'景气榜 {as_of} · F1 STRICT {strict} / FULL {full} · 入池 {pool_count}'
    if size_bytes > WEB_BUDGET_BYTES:
        caption += f'\n⚠ 网页 {size_bytes / 1e6:.1f} MB，超出 12 MB 预算'
    return caption[:CAPTION_LIMIT]


def pool_text(*, as_of: str, previous_as_of: Optional[str], added: Sequence[str], removed: Sequence[str],
              members: int, reason: Optional[str]) -> str:
    """Markdown message; every variable part is escaped."""
    lines = [f'*景气精选池 {md_escape(as_of)}*']
    if reason:
        lines.append(f'池文件不可用（{md_escape(reason)}），本周不列进出')
        return '\n'.join(lines)
    lines.append(f'入池 {members} 只')
    if previous_as_of is None:
        lines.append('无上周对比')
    elif not added and not removed:
        lines.append(f'与上周相同（对比 {md_escape(previous_as_of)}）')
    else:
        lines.append(f'对比 {md_escape(previous_as_of)}')
        lines.append(f'新进（{len(added)}）: ' + (', '.join(md_escape(s) for s in added) or '无'))
        lines.append(f'移出（{len(removed)}）: ' + (', '.join(md_escape(s) for s in removed) or '无'))
    return '\n'.join(lines)


def last_health_freeze(delivery_dir, as_of: str, code_version: str):
    """None (never sent: record a baseline), VERSION_CHANGED, or the freeze as_of last sent or baselined.

    Looks at ledgers strictly before as_of, newest first, and stops at the first `health` that was
    sent or baselined; failed weeks and `not_due` weeks carry no new information and are skipped.
    """
    folder = Path(delivery_dir) / 'ledger'
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob('*.json'), reverse=True):
        if path.stem >= as_of:
            continue
        health = json.loads(path.read_text(encoding='utf-8')).get('items', {}).get('health') or {}
        if health.get('sent_at') or health.get('baseline'):
            if health.get('code_version') != code_version:
                return VERSION_CHANGED
            return health.get('freeze_as_of')
    return None


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str                       # 'delivered' | 'already_delivered' | 'failed'
    sent: Tuple[str, ...] = ()
    error: str = ''
    baseline: Optional[str] = None    # freeze as_of recorded as the health baseline this run, if any


def _complete(ledger: dict) -> bool:
    items = ledger.get('items', {})
    health = items.get('health') or {}
    return ('web' in items and 'pool_text' in items
            and bool(health.get('sent_at') or health.get('baseline') or health.get('not_due')))


def snapshot(src: Path, dst: Path) -> Path:
    """Self-contained copy (no WAL) via the SQLite backup API; the source is opened read-only."""
    source = sqlite3.connect(Path(src).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    check = sqlite3.connect(Path(dst).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if check.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('snapshot_quick_check')
    finally:
        check.close()
    return dst


def _board_facts(snap: Path, as_of: str, code_version: str) -> Tuple[int, int, Optional[str]]:
    """STRICT and FULL counts of this week's F1 live board, and the latest F1 quarter-final as_of."""
    h = scheme_hash(SCHEMES[POOL_SCHEME_ID])
    with ProsperityStore(snap, read_only=True) as store:
        stored = store.get_board(BoardKey('weekly', as_of, h, code_version))
        if stored is None or stored.meta.mode != 'live':
            raise ValueError('weekly_board_missing')
        rows = [r for r in stored.board.rows if r.status == 'ranked']
        strict = sum(r.grade == 'STRICT' for r in rows)
        full = sum(r.grade == 'FULL' for r in rows)
        frozen = store.list_boards(scheme_hash=h, code_version=code_version, board_type='quarter_final')
    return strict, full, (frozen[-1].as_of if frozen else None)


def _run_cli(run, script: str, args: Sequence[str], timeout: int) -> None:
    cmd = [sys.executable, str(ROOT / 'scripts' / script), *map(str, args)]
    result = run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        tail = (result.stderr or '').strip().splitlines()[-1:] or ['']
        raise RuntimeError(f'{script} rc={result.returncode}: {tail[0][:200]}')


def _stamp(now) -> str:
    return now().astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _prune_outputs(out_root: Path, keep: int = KEEP_OUTPUT_WEEKS) -> None:
    if not out_root.is_dir():
        return
    weeks = sorted(p for p in out_root.iterdir() if p.is_dir() and not p.is_symlink())
    for old in weeks[:-keep]:
        shutil.rmtree(old)


def deliver_week(*, as_of: str, code_version: str, db: Path, market_db: Path, pool_path: Path,
                 delivery_dir: Path, now: Callable[[], datetime],
                 send_document=None, send_message=None, run=subprocess.run) -> DeliveryOutcome:
    """Send this week's web page, pool changes and (when a newer freeze exists) the F1 health report.

    Each item is recorded in the ledger right after it is sent, so a re-run sends only what is
    missing (at-least-once: a crash between a send and its ledger write repeats that item).
    Every failure, including a ledger write, returns status 'failed'; nothing raises.
    """
    send_document = send_document or telegram_bot.send_document
    send_message = send_message or telegram_bot.send_message
    delivery_dir = Path(delivery_dir)
    sent: List[str] = []
    baseline = None
    tmp = delivery_dir / 'tmp'
    stage = ''
    try:
        ledger = read_ledger(delivery_dir, as_of)
        if _complete(ledger):
            return DeliveryOutcome('already_delivered')
        items = ledger.setdefault('items', {})
        out = delivery_dir / 'out' / as_of
        out.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True, exist_ok=True)
        day = as_of.replace('-', '')

        stage = 'snapshot'
        check_space(db)
        snap = snapshot(db, tmp / 'prosperity.db')
        strict, full, latest_freeze = _board_facts(snap, as_of, code_version)
        pool = load_prosperity_pool(pool_path, now=now())
        pool_ok = pool.get('available') and pool.get('as_of') == as_of
        members = sorted(m['symbol'] for m in pool.get('members', [])) if pool_ok else []

        if 'web' not in items:
            stage = 'web'
            page = out / f'prosperity-web-{day}.html'
            _run_cli(run, 'prosperity_web.py', ['--db', snap, '--pool', pool_path, '--code-version',
                                                code_version, '--out', page], WEB_TIMEOUT_SECONDS)
            size = page.stat().st_size
            if size > WEB_SEND_LIMIT_BYTES:
                raise ValueError(f'web_too_large: {size} bytes')
            caption = web_caption(as_of=as_of, strict=strict, full=full, pool_count=len(members),
                                  size_bytes=size)
            if not send_document(str(page), caption=caption, channel='private',
                                 timeout=UPLOAD_TIMEOUT_SECONDS):
                raise RuntimeError('send_document returned False')
            items['web'] = {'sent_at': _stamp(now), 'bytes': size}
            write_ledger(delivery_dir, ledger)
            sent.append('web')

        if 'pool_text' not in items:
            stage = 'pool_text'
            if pool_ok:
                previous = previous_ledger(delivery_dir, as_of)
                added, removed = pool_diff(previous['pool_members'] if previous else None, members)
                text = pool_text(as_of=as_of, previous_as_of=previous['as_of'] if previous else None,
                                 added=added, removed=removed, members=len(members), reason=None)
            else:
                reason = pool.get('reason') or 'pool_as_of_mismatch'
                text = pool_text(as_of=as_of, previous_as_of=None, added=[], removed=[], members=0,
                                 reason=reason)
            if not send_message(text, channel='private'):
                raise RuntimeError('send_message returned False')
            items['pool_text'] = {'sent_at': _stamp(now)}
            if pool_ok:
                ledger['pool_members'] = members
            write_ledger(delivery_dir, ledger)
            sent.append('pool_text')

        health = items.get('health') or {}
        if not (health.get('sent_at') or health.get('baseline') or health.get('not_due')):
            stage = 'health'
            last = last_health_freeze(delivery_dir, as_of, code_version)
            record = {'freeze_as_of': latest_freeze, 'code_version': code_version}
            if latest_freeze is None:
                record['not_due'] = True
            elif last is None:
                record['baseline'] = True
                baseline = latest_freeze
            elif last is not VERSION_CHANGED and latest_freeze <= last:
                record['not_due'] = True
            else:
                check_space(market_db)
                msnap = snapshot(market_db, tmp / 'market.db')
                report_dir = out / 'health'
                _run_cli(run, 'prosperity_health.py',
                         ['--db', snap, '--market-db', msnap, '--code-version', code_version,
                          '--out', report_dir, '--schemes', 'F1'], HEALTH_TIMEOUT_SECONDS)
                attachment = out / f'prosperity-health-F1-{latest_freeze.replace("-", "")}.html'
                shutil.copyfile(report_dir / 'F1.html', attachment)
                if not send_document(str(attachment), caption=f'F1 体检报告 · 冻结 {latest_freeze}',
                                     channel='private', timeout=UPLOAD_TIMEOUT_SECONDS):
                    raise RuntimeError('send_document returned False')
                record['sent_at'] = _stamp(now)
                sent.append('health')
            items['health'] = record
            write_ledger(delivery_dir, ledger)
        return DeliveryOutcome('delivered', tuple(sent), baseline=baseline)
    except Exception as exc:
        message = telegram_bot._sanitize_error_text(f'{stage}: {type(exc).__name__}: {exc}')
        return DeliveryOutcome('failed', tuple(sent), message, baseline)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            _prune_outputs(delivery_dir / 'out')
        except OSError:
            pass
