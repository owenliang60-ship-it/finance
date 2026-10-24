"""Reproduce code-review findings 1, 2, 3, 7 against the M8 worktree store (temp db only)."""
import sys, tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from src.data.prosperity_storage import real
from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import storage_batch


def freeze(as_of, frozen_at, label='2026Q2', frozen=None, computed_at=None):
    batch = storage_batch(as_of=as_of, freeze=True, frozen=frozen)
    out = []
    for b in batch:
        m = b.meta
        if computed_at:
            m = replace(m, computed_at=computed_at)
        if m.board_type == 'quarter_final':
            m = replace(m, frozen_at=frozen_at, quarter_label=label)
        out.append(replace(b, meta=m))
    return tuple(out)


def run(name, fn):
    try:
        print(f'{name}: {fn()}')
    except Exception as exc:
        print(f'{name}: raised {type(exc).__name__}: {exc}')


tmp = Path(tempfile.mkdtemp(prefix='m8verify-'))
print('tmp', tmp)

# F1: replay weekly 10-03 cites a freeze that only became effective on 10-20.
with ProsperityStore(tmp / 'f1.db', read_only=False) as s:
    a = freeze('2026-09-26', '2026-10-20T08:30:00Z', computed_at='2026-10-20T09:00:00Z')
    s.save_batch(a)
    pk = {b.board.scheme_id: b.board.params for b in a[4:]}
    w = storage_batch(as_of='2026-10-03', frozen=pk)
    w = tuple(replace(b, meta=replace(b.meta, mode='replay', computed_at='2026-10-25T09:00:00Z',
                                      source_evidence={**b.meta.source_evidence, 'observed_at': None}))
              for b in w)
    run('F1 write accepts future-effective freeze', lambda: bool(s.save_batch(w)))
    k = a[4]
    run('F1 read path returns that freeze for 10-03', lambda: s.get_frozen_params(
        scheme_hash=k.board.scheme_hash, code_version=k.board.code_version,
        as_of='2026-10-03', effective_at='2026-10-03T09:00:00Z'))

# F2: newer quarter freeze B ignored because older freeze A has a later frozen_at.
with ProsperityStore(tmp / 'f2.db', read_only=False) as s:
    a = freeze('2026-09-26', '2026-10-20T08:30:00Z', computed_at='2026-10-20T09:00:00Z')
    b = freeze('2026-10-03', '2026-10-03T08:30:00Z', label='2026Q3')
    s.save_batch(a)
    s.save_batch(b)
    k = a[4].board
    p = s.get_frozen_params(scheme_hash=k.scheme_hash, code_version=k.code_version,
                            as_of='2026-10-10', effective_at='2026-10-23T00:00:00Z')
    print('F2 selected freeze as_of:', p.as_of, '(newer quarter freeze is 2026-10-03)')

# F3: two quarter_final boards with the same quarter label.
with ProsperityStore(tmp / 'f3.db', read_only=False) as s:
    s.save_batch(freeze('2026-09-26', '2026-09-26T08:30:00Z', label='2026Q2'))
    run('F3 second 2026Q2 freeze accepted', lambda: bool(s.save_batch(
        freeze('2026-10-03', '2026-10-03T08:30:00Z', label='2026Q2'))))
    n = s._conn.execute("SELECT count(*) FROM board WHERE board_type='quarter_final' "
                        "AND quarter_label='2026Q2'").fetchone()[0]
    print('F3 quarter_final rows labelled 2026Q2:', n)

# F7: huge int overflows instead of ValueError.
run('F7 real(10**400)', lambda: real(10**400))
