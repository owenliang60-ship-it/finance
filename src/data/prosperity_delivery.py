"""M13 Telegram delivery: after a published live week, send the web page and the pool changes.

Plan: docs/plans/2026-11-03-prosperity-m13-delivery.md. Runs after run_week inside the
finance_prosperity job (both locks held). Read-only on every database: it works on fresh
`.backup` snapshots and calls the M11/M12 CLIs as subprocesses. One JSON ledger per week
records each sent item, so a re-run (20:30) sends only what is missing. Not part of the
engine code_version.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import List, Optional, Sequence, Tuple

WEB_BUDGET_BYTES = 12_000_000          # north-star §4.2 page budget: warn, still send
WEB_SEND_LIMIT_BYTES = 45_000_000      # Telegram Bot API caps uploads at 50 MB
CAPTION_LIMIT = 1024


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
