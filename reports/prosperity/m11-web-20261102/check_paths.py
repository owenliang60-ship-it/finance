"""CC's own re-check of Codex's path cases, on throwaway synthetic databases only."""
import hashlib, os, sqlite3, sys, tempfile, unicodedata
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import storage_batch
from scripts.prosperity_web import main

def make(path):
    with ProsperityStore(path, read_only=False) as s:
        s.save_batch(storage_batch(freeze=True, quarter_label='2026Q2'))
    for x in path.parent.glob(path.name + '-*'):
        if x.stat().st_size == 0:
            x.unlink()

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(db, out, *extra): return main(['--db', str(db), '--out', str(out), '--code-version', 'test-v1', *extra])

base = Path(tempfile.mkdtemp(prefix='cases-', dir=sys.argv[2]))
real = base / 'real.db'; make(real)
alias = base / 'alias.db'; alias.symlink_to(real)
before = sha(real)
print('symlink alias → real -wal sidecar   rc', run(alias, base / 'real.db-wal'), '| real -wal created:', (base / 'real.db-wal').exists())
live = base / 'live.db'; make(live)
la = base / 'live-alias.db'; la.symlink_to(live)
con = sqlite3.connect(live); con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA wal_autocheckpoint=0')
con.execute('CREATE TABLE IF NOT EXISTS t(x)'); con.execute('INSERT INTO t VALUES (1)'); con.commit()   # keep WAL open
print('symlink alias with live WAL          rc', run(la, base / 'a.html'), '| live -wal bytes', os.path.getsize(str(live) + '-wal'), '| html written:', (base / 'a.html').exists())
con.close()
nfc = base / unicodedata.normalize('NFC', 'café.db'); make(nfc)
nfd_wal = base / (unicodedata.normalize('NFD', 'café.db') + '-wal')
print('unicode NFD sidecar                  rc', run(nfc, nfd_wal), '| any -wal created:', any(p.name.endswith('-wal') for p in base.iterdir() if 'caf' in p.name))
g = base / 'guard.db'; make(g)
(base / 'sub').mkdir(); dl = base / 'dirlink'; dl.symlink_to(base, target_is_directory=True)
pool = base / 'pool.json'; pool.write_text('{}'); ph = base / 'pool-hard.json'; os.link(pool, ph)
gl = base / 'db-link'; gl.symlink_to(g)
for label, out, extra in [('out = symlink to db', gl, ()), ('out via directory symlink', dl / 'guard.db', ()),
                          ('out via ..', base / 'sub' / '..' / 'guard.db', ()),
                          ('out = hardlink to pool', ph, ('--pool', str(pool))), ('out = case variant', base / 'GUARD.DB', ())]:
    print(f'{label:36s} rc', run(g, out, *extra))
print('real db unchanged:', sha(real) == before)
