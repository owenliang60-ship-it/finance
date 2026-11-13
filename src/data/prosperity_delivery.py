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
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from src import telegram_bot
from src.data.prosperity_backup import _quick_check, check_space
from src.data.prosperity_pool import POOL_SCHEME_ID, load_prosperity_pool
from src.data.prosperity_storage import BoardKey
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_web import pool_binding
from terminal.prosperity.schemes import SCHEMES, scheme_hash

ROOT = Path(__file__).resolve().parents[2]

WEB_BUDGET_BYTES = 12_000_000          # north-star §4.2 page budget: warn, still send
WEB_SEND_LIMIT_BYTES = 45_000_000      # Telegram Bot API caps uploads at 50 MB
CAPTION_LIMIT = 1024
WEB_TIMEOUT_SECONDS = 600
HEALTH_TIMEOUT_SECONDS = 1800
UPLOAD_TIMEOUT_SECONDS = 300
MOMENTUM_TIMEOUT_SECONDS = 600
MESSAGE_LIMIT = 4096                   # Telegram sendMessage text limit
MOMENTUM_TOP = 30                      # rows listed per window in the momentum board text
MOMENTUM_WINDOWS = (('7w', '7周'), ('14w', '14周'))
KEEP_OUTPUT_WEEKS = 2
NEVER = object()                       # last_health_freeze: no report was ever sent or baselined
VERSION_CHANGED = object()             # last_health_freeze: the last sent report used another code_version
OWNER_MARKER = '.prosperity-delivery'  # cleanup only ever touches a directory carrying this file


def ledger_path(delivery_dir, as_of: str) -> Path:
    return Path(delivery_dir) / 'ledger' / f'{as_of}.json'


def _parse_ledger(path: Path) -> dict:
    """A ledger is an object with an `items` object; anything else fails loudly with the file name."""
    try:
        ledger = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f'ledger_unreadable: {path.name}: {type(exc).__name__}') from exc
    if not isinstance(ledger, dict) or not isinstance(ledger.get('items'), dict):
        raise ValueError(f'ledger_unreadable: {path.name}: not an object with items')
    return ledger


def read_ledger(delivery_dir, as_of: str) -> dict:
    path = ledger_path(delivery_dir, as_of)
    if not path.is_file():
        return {'as_of': as_of, 'items': {}}
    return _parse_ledger(path)


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


def _ledgers_before(delivery_dir, as_of: str) -> Iterator[dict]:
    """Ledgers strictly before as_of, newest first."""
    folder = Path(delivery_dir) / 'ledger'
    if not folder.is_dir():
        return
    for path in sorted(folder.glob('*.json'), reverse=True):
        if path.stem < as_of:
            yield _parse_ledger(path)


def _health_done(health: Optional[dict]) -> bool:
    health = health or {}
    return bool(health.get('sent_at') or health.get('baseline') or health.get('not_due'))


def previous_ledger(delivery_dir, as_of: str) -> Optional[dict]:
    """The latest ledger before as_of that recorded pool members (weeks without a pool text are skipped)."""
    return next((l for l in _ledgers_before(delivery_dir, as_of) if isinstance(l.get('pool_members'), list)), None)


def last_health_freeze(delivery_dir, as_of: str, code_version: str):
    """NEVER, VERSION_CHANGED, or the freeze as_of last sent or baselined (None: baselined with no freeze).

    Newest first, stops at the first `health` that was sent or baselined; failed and `not_due`
    weeks carry no new information and are skipped.
    """
    for ledger in _ledgers_before(delivery_dir, as_of):
        health = ledger['items'].get('health') or {}
        if health.get('sent_at') or health.get('baseline'):
            return VERSION_CHANGED if health.get('code_version') != code_version else health.get('freeze_as_of')
    return NEVER


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


def web_caption(*, as_of: str, strict: int, full: int, pool_count: Optional[int], size_bytes: int) -> str:
    """Plain-text caption (send_document sends no parse_mode). pool_count None = pool not trusted."""
    pool = '未知' if pool_count is None else str(pool_count)
    caption = f'景气榜 {as_of} · F1 STRICT {strict} / FULL {full} · 入池 {pool}'
    if size_bytes > WEB_BUDGET_BYTES:
        caption += f'\n⚠ 网页 {size_bytes / 1e6:.1f} MB，超出 12 MB 预算'
    return caption[:CAPTION_LIMIT]


def pool_text(*, as_of: str, previous_as_of: Optional[str], added: Sequence[str], removed: Sequence[str],
              members: int, reason: Optional[str], current: Sequence[str] = ()) -> str:
    """Markdown message; every variable part is escaped. `current` = members in pool-rank order,
    listed on the last line (Boss 2026-11-09)."""
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
    if current:
        lines.append(f'当前（{len(current)}，按景气排名）: ' + ', '.join(md_escape(s) for s in current))
    return '\n'.join(lines)


def _long_rows(board: dict) -> List[dict]:
    """Long-eligible rows of one window in rank order (M16 payload: every scored row is listed)."""
    return sorted((r for r in board['scored'] if r['eligible']), key=lambda r: r['rank'])


def _counted(text: str) -> str:
    if len(text) > MESSAGE_LIMIT:
        raise ValueError(f'message_too_long: {len(text)}')
    return text


def momentum_text(payload: dict, pool: Optional[Sequence[str]]) -> str:
    """M16 item 1: top rows of the 7-week and 14-week V4 long boards. pool None = pool not trusted
    (no stars). Counts come from the row lists, never from a stored total."""
    starred = set(pool or ())
    lines = [f'*动量榜 {md_escape(payload["as_of"])}*（V4 · 只看上涨 · 价格截至 {md_escape(payload["price_date"])}）',
             f'主池 {payload["universe"]["count"]} 只']
    for key, label in MOMENTUM_WINDOWS:
        board = payload['boards'][key]
        rows = _long_rows(board)
        head = (f'*{label}*（{board["days"]} 交易日）有效 {len(board["scored"])} · '
                f'不可用 {len(board["unavailable"])} · 上涨合格 {len(rows)}')
        lines.append('')
        if not rows:
            lines.append(head + '：无')
            continue
        top = rows[:MOMENTUM_TOP]
        lines.append(head + f'，前 {len(top)}：')
        lines.extend(f'{r["rank"]}. {md_escape(r["symbol"])} {r["score"]:.1f} {r["return"]:+.0%}'
                     + (' ★' if r['symbol'] in starred else '') for r in top)
    if pool is not None:
        lines += ['', '★ = 景气精选池成员']
    return _counted('\n'.join(lines))


def combo_messages(payload: dict, pool: Optional[Sequence[str]], reason: Optional[str]) -> List[str]:
    """M16 item 2: pool members that pass the long gate, per window, with their whole-universe rank.

    Sections are packed in order into messages of at most MESSAGE_LIMIT; a section is never cut
    (a pool at its 100-member cap needs two messages). pool None = pool not trusted.
    """
    title = f'*景气动量精选 {md_escape(payload["as_of"])}*'
    if pool is None:
        return [_counted(f'{title}\n池文件不可用（{md_escape(reason or "unknown")}），本周不列名单')]
    members = set(pool)
    sections, per_window = [], []
    for key, label in MOMENTUM_WINDOWS:
        hits = [r for r in _long_rows(payload['boards'][key]) if r['symbol'] in members]
        per_window.append({r['symbol'] for r in hits})
        listed = ' · '.join(f'{md_escape(r["symbol"])} #{r["rank"]} {r["return"]:+.0%}' for r in hits) or '无'
        sections.append(f'*{label}*（{len(hits)}）: {listed}')
    both = sorted(set.intersection(*per_window))
    sections.append(f'*两榜都在*（{len(both)}）: ' + (', '.join(md_escape(s) for s in both) or '无')
                    + '\n#n = 全主池上涨榜名次')
    head = f'{title}\n景气精选池 {len(members)} 只 ∩ 上涨合格（V4 · 价格截至 {md_escape(payload["price_date"])}）'
    more = f'*景气动量精选 {md_escape(payload["as_of"])}（续）*'
    parts, current = [], head
    for section in sections:
        if len(current) + 1 + len(section) <= MESSAGE_LIMIT:
            current += '\n' + section
        else:
            parts.append(current)
            current = _counted(more + '\n' + section)
    parts.append(current)
    return [_counted(p) for p in parts]


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str                       # 'delivered' | 'already_delivered' | 'failed'
    sent: Tuple[str, ...] = ()
    error: str = ''
    baseline: Optional[str] = None    # freeze as_of recorded as the health baseline this run, if any


def _sent(items: dict, key: str, code_version: str) -> bool:
    """An item counts only for the board version it was sent for (second review P2): a same-week
    engine upgrade publishes a new board, so every item is due again under the new code_version."""
    item = items.get(key)
    if not isinstance(item, dict) or item.get('code_version') != code_version:
        return False
    return _health_done(item) if key == 'health' else True


def _complete(ledger: dict, code_version: str) -> bool:
    return all(_sent(ledger['items'], key, code_version) for key in ('web', 'pool_text', 'health'))


def _inputs_outside(delivery_dir: Path, **inputs: Path) -> None:
    """Refuse an input that resolves inside delivery_dir (second review P1).

    tmp/ is removed and old out/ weeks are pruned after every run, and a snapshot onto its own
    source blocks; checked on resolved paths so a symlinked alias counts too.
    """
    root = delivery_dir.resolve()
    for name, path in inputs.items():
        resolved = Path(path).resolve()
        if resolved == root or root in resolved.parents:
            raise ValueError(f'input_inside_delivery_dir: {name}')


def snapshot(src: Path, dst: Path) -> Path:
    """Self-contained copy via the SQLite backup API (source opened read-only).

    backup() copies the WAL journal-mode header too; _quick_check switches the private copy to
    DELETE mode so later read-only opens leave no -wal/-shm (code review #6).
    """
    source = sqlite3.connect(Path(src).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    _quick_check(dst)
    return dst


def _load_board(snap: Path, as_of: str, code_version: str):
    """This week's F1 live board and the latest F1 quarter-final as_of."""
    h = scheme_hash(SCHEMES[POOL_SCHEME_ID])
    with ProsperityStore(snap, read_only=True) as store:
        stored = store.get_board(BoardKey('weekly', as_of, h, code_version))
        if stored is None or stored.meta.mode != 'live':
            raise ValueError('weekly_board_missing')
        frozen = store.list_boards(scheme_hash=h, code_version=code_version, board_type='quarter_final')
    return stored, (frozen[-1].as_of if frozen else None)


def _trusted_pool(pool: dict, stored, as_of: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """(members, None) for a fresh pool bound to this board (north-star §4.1 reader rule), else (None, reason)."""
    if not pool.get('available'):
        return None, pool.get('reason') or 'prosperity_pool_invalid'
    if pool.get('as_of') != as_of:
        return None, 'pool_as_of_mismatch'
    _, note = pool_binding(pool, stored)
    if not note.startswith('入池'):                 # positive check: an unknown note is never trusted
        return None, 'pool_board_mismatch'
    return sorted(m['symbol'] for m in pool['members']), None


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


LAYOUT = frozenset({'ledger', 'out', 'tmp', OWNER_MARKER})


def _claim(delivery_dir: Path) -> None:
    """Mark the directory as ours, or refuse a shared one (code review #8).

    A directory without the marker is accepted only if it holds nothing but this module's layout
    (an operator may pre-seed ledger/ by hand, runbook); anything else (e.g. `--delivery-dir data`)
    is refused before any cleanup can run.
    """
    marker = delivery_dir / OWNER_MARKER
    if (delivery_dir.exists() and not marker.is_file()
            and any(entry.name not in LAYOUT for entry in delivery_dir.iterdir())):
        raise ValueError(f'delivery_dir_not_owned: {delivery_dir}')
    delivery_dir.mkdir(parents=True, exist_ok=True)
    marker.touch()


def deliver_week(*, as_of: str, code_version: str, db: Path, market_db: Path, pool_path: Path,
                 delivery_dir: Path, now: Callable[[], datetime],
                 send_document=None, send_message=None, run=subprocess.run) -> DeliveryOutcome:
    """Send this week's web page, pool changes and (when a newer freeze exists) the F1 health report.

    The three items are independent: each failure is collected and the others still run
    (code review #4). Each item is recorded in the ledger right after it is sent, so a re-run
    sends only what is missing (at-least-once: a crash between a send and its ledger write
    repeats that item). Every failure, including a ledger write, returns status 'failed';
    nothing raises.
    """
    send_document = send_document or telegram_bot.send_document
    send_message = send_message or telegram_bot.send_message
    delivery_dir = Path(delivery_dir)
    sent: List[str] = []
    errors: List[str] = []
    baseline = None

    def fail(stage, exc):
        errors.append(telegram_bot._sanitize_error_text(f'{stage}: {type(exc).__name__}: {exc}'))

    try:
        _inputs_outside(delivery_dir, db=db, market_db=market_db, pool_path=pool_path)
        _claim(delivery_dir)
        ledger = read_ledger(delivery_dir, as_of)
    except Exception as exc:
        fail('setup', exc)
        return DeliveryOutcome('failed', (), '; '.join(errors))
    if _complete(ledger, code_version):
        return DeliveryOutcome('already_delivered')
    items = ledger['items']
    tmp = delivery_dir / 'tmp'
    try:
        out = delivery_dir / 'out' / as_of
        out.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            check_space(db)
            snap = snapshot(db, tmp / 'prosperity.db')
            stored, latest_freeze = _load_board(snap, as_of, code_version)
            pool = load_prosperity_pool(pool_path, now=now())
            members, pool_reason = _trusted_pool(pool, stored, as_of)
            # the validator already holds members in strictly increasing rank (review #8: no re-sort)
            by_rank = [m['symbol'] for m in pool['members']] if members is not None else []
        except Exception as exc:
            fail('snapshot', exc)
            return DeliveryOutcome('failed', (), '; '.join(errors))

        if not _sent(items, 'web', code_version):
            try:
                page = out / f'prosperity-web-{as_of.replace("-", "")}.html'
                _run_cli(run, 'prosperity_web.py', ['--db', snap, '--pool', pool_path, '--code-version',
                                                    code_version, '--out', page], WEB_TIMEOUT_SECONDS)
                size = page.stat().st_size
                if size > WEB_SEND_LIMIT_BYTES:
                    raise ValueError(f'web_too_large: {size} bytes')
                ranked = [r for r in stored.board.rows if r.status == 'ranked']
                caption = web_caption(as_of=as_of, strict=sum(r.grade == 'STRICT' for r in ranked),
                                      full=sum(r.grade == 'FULL' for r in ranked),
                                      pool_count=None if members is None else len(members), size_bytes=size)
                if not send_document(str(page), caption=caption, channel='private',
                                     timeout=UPLOAD_TIMEOUT_SECONDS):
                    raise RuntimeError('send_document returned False')
                items['web'] = {'sent_at': _stamp(now), 'bytes': size, 'code_version': code_version}
                write_ledger(delivery_dir, ledger)
                sent.append('web')
            except Exception as exc:
                fail('web', exc)

        if not _sent(items, 'pool_text', code_version):
            try:
                if members is not None:
                    previous = previous_ledger(delivery_dir, as_of)
                    added, removed = pool_diff(previous['pool_members'] if previous else None, members)
                    text = pool_text(as_of=as_of, previous_as_of=previous['as_of'] if previous else None,
                                     added=added, removed=removed, members=len(members), reason=None,
                                     current=by_rank)
                else:
                    text = pool_text(as_of=as_of, previous_as_of=None, added=[], removed=[], members=0,
                                     reason=pool_reason)
                if not send_message(text, channel='private'):
                    raise RuntimeError('send_message returned False')
                items['pool_text'] = {'sent_at': _stamp(now), 'code_version': code_version}
                if members is not None:
                    ledger['pool_members'] = members
                write_ledger(delivery_dir, ledger)
                sent.append('pool_text')
            except Exception as exc:
                fail('pool_text', exc)

        if not _sent(items, 'health', code_version):
            try:
                last = last_health_freeze(delivery_dir, as_of, code_version)
                record = {'freeze_as_of': latest_freeze, 'code_version': code_version}
                if last is NEVER:
                    record['baseline'] = True
                    if latest_freeze is not None:      # never silent (code review #5)
                        notice = (f'*景气体检报告基线：冻结 {md_escape(latest_freeze)}*\n'
                                  '本次只记起点、不发送；之后出现更新的季末冻结时自动发送 F1 体检报告')
                        if not send_message(notice, channel='private'):
                            raise RuntimeError('send_message returned False')
                        baseline = latest_freeze
                        sent.append('health_baseline')
                elif latest_freeze is None or (last is not VERSION_CHANGED and last is not None
                                               and latest_freeze <= last):
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
            except Exception as exc:
                fail('health', exc)
    except Exception as exc:
        fail('setup', exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            _prune_outputs(delivery_dir / 'out')
        except OSError:
            pass
    if errors:
        return DeliveryOutcome('failed', tuple(sent), '; '.join(errors), baseline)
    return DeliveryOutcome('delivered', tuple(sent), baseline=baseline)
