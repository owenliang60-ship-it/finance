"""Tests for sync_to_cloud.sh — resolver migration (Task 20 matrix #4).

The script embeds Python one-liners, executed locally via "$PYTHON" -c and
remotely via `ssh ... python3 -c`, that display the pool size. This test
extracts each embedded Python block's source (by structural bash anchors,
independent of the get_symbols/sqlite3 content churn the migration makes)
and executes it directly against a throwaway market.db, verifying the
printed count reflects extended_membership's current (effective_to IS NULL)
member count rather than the old pool_manager.get_symbols() length.
"""
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "sync_to_cloud.sh"

# Each target block is located by the unique `info "..."` line that
# immediately precedes it in the script — stable across the migration,
# unlike the Python source inside the block. Extraction is anchor-bounded
# (not "search to the next occurrence of the closing marker anywhere in the
# file") specifically because an earlier, unrelated `python3 -c \"...` block
# (merge_universe, push_to_cloud step 4) closes mid-line rather than on its
# own line, which would otherwise make a naive whole-file regex swallow
# unrelated bash source between blocks.
_ANCHORS = [
    ('info "云端验证..."', 'python3 -c \\"', re.compile(r'\n\\""')),
    ('info "--- 本地 ---"', '"$PYTHON" -c "', re.compile(r'\n"')),
    ('info "--- 云端 ---"', 'python3 -c \\"', re.compile(r'\n\\""')),
]


def _extract_pool_count_blocks() -> list:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    blocks = []
    for anchor, opener, closer_re in _ANCHORS:
        anchor_idx = text.index(anchor)
        opener_idx = text.index(opener, anchor_idx)
        start = opener_idx + len(opener)
        window = text[start:start + 2000]
        m = closer_re.search(window)
        assert m, f"no closing marker found after anchor {anchor!r}"
        blocks.append(window[:m.start()])
    return blocks


@pytest.fixture
def fake_cloud_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    market_db = data_dir / "market.db"
    conn = sqlite3.connect(market_db)
    conn.execute(
        "CREATE TABLE extended_membership "
        "(symbol TEXT, effective_from TEXT, effective_to TEXT)"
    )
    conn.executemany(
        "INSERT INTO extended_membership VALUES (?, ?, ?)",
        [
            ("AAPL", "2026-01-01", None),
            ("MSFT", "2026-01-01", None),
            ("GOOG", "2026-01-01", None),
            ("OLDCO", "2025-01-01", "2026-01-01"),  # exited — must not count
        ],
    )
    conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT)")
    conn.execute("INSERT INTO daily_price VALUES ('AAPL', '2026-08-18')")
    conn.commit()
    conn.close()
    # company.db just needs to exist for os.path.getsize() in the local block
    (data_dir / "company.db").write_bytes(b"")
    return tmp_path


class TestSyncToCloudBashSyntax:
    def test_bash_n_clean(self):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT_PATH)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_exit_trap_preserves_original_status(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        cleanup = text[text.index("cleanup() {"):text.index("\n}\n\ntrap cleanup EXIT")]
        assert "local rc=$?" in cleanup
        assert 'return "$rc"' in cleanup

    def test_company_db_push_uses_validated_atomic_replace(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        push = text[text.index("push_to_cloud() {"):text.index("\n# ── Status:")]
        assert 'remote_tmp="/tmp/finance-companydb-sync-$$.db"' in push
        assert "PRAGMA quick_check" in push
        assert "hashlib.sha256" in push
        assert "lsof '$REMOTE_DIR/data/company.db'" in push
        assert "mv '$remote_tmp' '$REMOTE_DIR/data/company.db'" in push
        assert 'rsync -avz "$LOCAL_DIR/data/company.db" "$REMOTE/data/company.db"' not in push


class TestPoolCountDisplaysExtendedMembershipCount:
    def test_three_display_blocks_present(self):
        blocks = _extract_pool_count_blocks()
        assert len(blocks) == 3, blocks

    def test_each_block_prints_current_membership_count(self, fake_cloud_dir):
        blocks = _extract_pool_count_blocks()
        assert blocks, "no pool-count display blocks found in sync_to_cloud.sh"
        for code in blocks:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=fake_cloud_dir,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (code, result.stderr)
            assert "3 只" in result.stdout, (code, result.stdout)


def _heredoc(opener: str, closer: str) -> str:
    """Python source of the heredoc that starts right after `opener` in the script."""
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    start = text.index(opener) + len(opener)
    return text[start:text.index(closer, start)]


class TestMarketDbPull:
    """issue 059 / 2026-09-30: never rsync the live cloud DB; publish only a validated snapshot."""

    def test_pull_publishes_only_a_validated_snapshot(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        pull = text[text.index("# ── market.db 云端→本地"):text.index("\n# ── Push:")]
        assert "src.backup(dst)" in pull
        assert 'shasum -a 256 "$_LOCAL_PULL_TMP"' in pull
        assert pull.count("PRAGMA quick_check") == 2
        assert 'LSOF="/usr/sbin/lsof"' in text and '"$LSOF" "$f"' in pull
        assert 'mv "$_LOCAL_PULL_TMP" "$LOCAL_DIR/data/market.db"' in pull
        assert 'rsync -avz "$REMOTE/data/market.db"' not in text
        assert "PRAGMA wal_checkpoint" not in pull
        # without a checkpoint the live main file understates the DB: size-gate the snapshot instead
        body = pull[pull.index("pull_from_cloud() {"):]
        gate = 'check_file_size "$LOCAL_DIR/data/market.db" "$_REMOTE_SNAP" "market.db" "pull"'
        assert gate in body and body.index("snapshot_remote_market_db") < body.index(gate)
        assert '"$REMOTE_DIR/data/market.db" "market.db" "pull"' not in text

    def test_snapshot_block_copies_wal_pages_and_prints_the_file_sha(self, tmp_path):
        import hashlib
        code = _heredoc("'$_REMOTE_SNAP' <<'PY'\n", '\nPY")')
        data = tmp_path / "data"
        data.mkdir()
        live = sqlite3.connect(data / "market.db")
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("CREATE TABLE t (x INTEGER)")
        live.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5000)])
        live.commit()
        assert (data / "market.db-wal").stat().st_size > 0          # committed rows still only in the WAL
        snap = data / ".pull-snapshot-1.db"
        out = subprocess.run([sys.executable, "-", str(data), str(snap)], input=code,
                             capture_output=True, text=True)
        main_size = (data / "market.db").stat().st_size        # before close: closing checkpoints the WAL
        live.close()
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == hashlib.sha256(snap.read_bytes()).hexdigest()
        # the live main file alone is far smaller than the data it holds (why the size gate uses the snapshot)
        assert main_size * 2 < snap.stat().st_size
        assert sqlite3.connect(snap).execute("SELECT count(*) FROM t").fetchone()[0] == 5000

    def test_local_check_rejects_a_truncated_download(self, tmp_path):
        code = _heredoc('"$PYTHON" - "$_LOCAL_PULL_TMP" <<\'PY\'\n', "\nPY\n")
        good = tmp_path / "good.db"
        conn = sqlite3.connect(good)
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.executemany("INSERT INTO t VALUES (?)", [("x" * 500,) for _ in range(2000)])
        conn.commit()
        conn.close()
        run = lambda path: subprocess.run([sys.executable, "-", str(path)], input=code,
                                          capture_output=True, text=True)
        assert run(good).returncode == 0
        bad = tmp_path / "bad.db"
        raw = good.read_bytes()
        bad.write_bytes(raw[:len(raw) - 8 * 4096])                 # the 2026-09-30 failure: pages past EOF
        assert run(bad).returncode != 0
