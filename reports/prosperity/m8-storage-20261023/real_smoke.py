"""Acceptance smoke: real 2026-09-29 snapshot → M4/M6 → M8 temp db → reopen → compare. Read-only on market.db."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root))
from src.data.market_store import MarketStore
from src.data.prosperity_storage import BoardMeta, prepare_board, key_for
from src.data.prosperity_store import ProsperityStore
from terminal.prosperity.factors import compute_factor_row
from terminal.prosperity.inputs import build_packets
from terminal.prosperity.kernel import score_board
from terminal.prosperity.schemes import SCHEMES
from terminal.prosperity.version import code_version

db, as_of = Path(sys.argv[1]), sys.argv[2]
out = {}
t0 = time.perf_counter()
store = MarketStore(db_path=db, read_only=True)
packets, meta = build_packets(store, as_of, mode="live", observed_at=as_of, with_beta=True)
factors = [compute_factor_row(p) for p in packets]
ver = code_version()
boards = [score_board(factors, s, frozen=None, as_of=as_of, code_version=ver) for s in SCHEMES.values()]
out["members"], out["packets"], out["build_errors"] = meta["members_resolved"], len(packets), len(meta["errors"])
out["build_seconds"] = round(time.perf_counter() - t0, 1)
members = sorted(set(p.symbol for p in packets) | set(meta["errors"]))
# Unresolved build errors are members too; M8 requires reconciliation.
bm = BoardMeta("weekly", None, None, as_of + "T09:00:00Z",
               subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "live",
               tuple(members), dict(meta["errors"]),
               {"source_id": "phase0-accepted-input-20260929", "observed_at": as_of + "T00:00:00Z",
                "snapshot_sha256": None, "profile_observed_at": None})
failures = {}
stored = []
for b in boards:
    try:
        stored.append(prepare_board(bm, b, SCHEMES[b.scheme_id], packets, factors,
                                    names={p.symbol: None for p in packets}))
    except Exception as exc:
        failures[b.scheme_id] = f"{type(exc).__name__}: {exc}"
out["prepare_failures"] = failures
if not failures:
    tmp = Path(tempfile.mkdtemp(prefix="m8real-")) / "prosperity_research.db"
    t1 = time.perf_counter()
    with ProsperityStore(tmp, read_only=False) as w:
        keys = w.save_batch(stored)
        again = w.save_batch(stored)
    out["write_seconds"] = round(time.perf_counter() - t1, 1)
    out["idempotent_keys"] = keys == again
    with ProsperityStore(tmp) as r:
        t2 = time.perf_counter()
        back = tuple(r.get_board(k) for k in keys)
        out["read_seconds"] = round(time.perf_counter() - t2, 1)
    out["round_trip_equal"] = back == tuple(stored)
    import sqlite3
    c = sqlite3.connect(tmp)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out["rows"] = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                   for t in ("board", "input_set", "stock_input", "stock_period", "factor_value", "score", "params", "scheme")}
    out["db_bytes"] = os.path.getsize(tmp)
    try:
        out["table_bytes"] = dict(c.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC").fetchall())
    except sqlite3.OperationalError as exc:
        out["table_bytes"] = f"dbstat unavailable: {exc}"
    out["avg_input_json_bytes"] = c.execute("SELECT avg(length(input_json)) FROM stock_input").fetchone()[0]
    out["avg_factor_meta_bytes"] = c.execute("SELECT avg(length(factor_meta_json)) FROM stock_input").fetchone()[0]
    out["max_quarters_in_input"] = max(len(json.loads(x)["quarters"]) for (x,) in
                                       c.execute("SELECT input_json FROM stock_input"))
    out["temp_db"] = str(tmp)
print(json.dumps(out, indent=2, default=str))
