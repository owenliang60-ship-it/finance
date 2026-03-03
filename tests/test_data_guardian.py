"""Tests for data_guardian.py — 快照备份 + 恢复 + 保留策略。"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def mock_data_dir(tmp_path, monkeypatch):
    """创建一个模拟的 data/ 目录结构用于测试。"""
    # 目录结构
    price_dir = tmp_path / "price"
    fundamental_dir = tmp_path / "fundamental"
    pool_dir = tmp_path / "pool"
    backup_dir = tmp_path / ".backups"

    price_dir.mkdir()
    fundamental_dir.mkdir()
    pool_dir.mkdir()

    # 模拟价格 CSV
    for symbol in ["AAPL", "NVDA", "MSFT", "GOOG", "AMZN"]:
        (price_dir / f"{symbol}.csv").write_text(
            "date,open,high,low,close,volume\n2026-02-14,100,105,99,103,1000000\n"
        )

    # 模拟基本面 JSON
    profiles = {"AAPL": {"name": "Apple"}, "NVDA": {"name": "NVIDIA"}, "_meta": {"updated_at": "2026-02-14"}}
    (fundamental_dir / "profiles.json").write_text(json.dumps(profiles))

    # 模拟 universe.json
    universe = [{"symbol": "AAPL"}, {"symbol": "NVDA"}, {"symbol": "MSFT"}]
    (pool_dir / "universe.json").write_text(json.dumps(universe))

    # 模拟 company.db
    (tmp_path / "company.db").write_text("fake db content")

    # 模拟 market.db
    (tmp_path / "market.db").write_text("fake market db content")

    # Monkeypatch 模块常量
    import src.data.data_guardian as guardian
    monkeypatch.setattr(guardian, "DATA_DIR", tmp_path)
    monkeypatch.setattr(guardian, "PRICE_DIR", price_dir)
    monkeypatch.setattr(guardian, "FUNDAMENTAL_DIR", fundamental_dir)
    monkeypatch.setattr(guardian, "POOL_DIR", pool_dir)
    monkeypatch.setattr(guardian, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(guardian, "COMPANY_DB", tmp_path / "company.db")
    monkeypatch.setattr(guardian, "MARKET_DB", tmp_path / "market.db")

    return tmp_path


class TestSnapshot:
    def test_creates_tarball(self, mock_data_dir):
        from src.data.data_guardian import snapshot
        path = snapshot(reason="test")

        assert path is not None
        assert path.exists()
        assert path.suffix == ".gz"
        assert "test" in path.name

    def test_contains_all_files(self, mock_data_dir):
        import tarfile
        from src.data.data_guardian import snapshot

        path = snapshot(reason="test")
        with tarfile.open(path, "r:gz") as tar:
            names = tar.getnames()

        # 5 CSV + 1 JSON + 1 universe.json + 1 company.db + 1 market.db = 9
        assert len(names) == 9
        assert "company.db" in names
        assert "market.db" in names
        assert "price/AAPL.csv" in names
        assert "fundamental/profiles.json" in names
        assert "pool/universe.json" in names

    def test_retention_policy(self, mock_data_dir, monkeypatch):
        import src.data.data_guardian as guardian
        monkeypatch.setattr(guardian, "MAX_SNAPSHOTS", 3)

        from src.data.data_guardian import snapshot, list_snapshots
        import time

        for i in range(5):
            snapshot(reason=f"test{i}")
            time.sleep(0.1)  # 确保时间戳不同

        snapshots = list_snapshots()
        assert len(snapshots) == 3

    def test_returns_none_on_empty_data(self, tmp_path, monkeypatch):
        """空目录也应该能创建快照 (虽然内容为空)。"""
        import src.data.data_guardian as guardian
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(guardian, "DATA_DIR", empty)
        monkeypatch.setattr(guardian, "PRICE_DIR", empty / "price")
        monkeypatch.setattr(guardian, "FUNDAMENTAL_DIR", empty / "fundamental")
        monkeypatch.setattr(guardian, "POOL_DIR", empty / "pool")
        monkeypatch.setattr(guardian, "BACKUP_DIR", empty / ".backups")
        monkeypatch.setattr(guardian, "COMPANY_DB", empty / "company.db")

        from src.data.data_guardian import snapshot
        path = snapshot(reason="empty")
        # 即使没文件也能创建快照 (0 files)
        assert path is not None


class TestRestore:
    def test_restore_deleted_file(self, mock_data_dir):
        from src.data.data_guardian import snapshot, restore

        path = snapshot(reason="before-delete")

        # 删除一个文件
        csv = mock_data_dir / "price" / "AAPL.csv"
        csv.unlink()
        assert not csv.exists()

        # 恢复
        stats = restore(str(path))
        assert stats["files_restored"] >= 1
        assert csv.exists()

    def test_restore_nonexistent_snapshot_raises(self):
        from src.data.data_guardian import restore
        with pytest.raises(FileNotFoundError):
            restore("/nonexistent/path.tar.gz")


class TestListSnapshots:
    def test_empty_when_no_backups(self, tmp_path, monkeypatch):
        import src.data.data_guardian as guardian
        monkeypatch.setattr(guardian, "BACKUP_DIR", tmp_path / "no_backups")

        from src.data.data_guardian import list_snapshots
        assert list_snapshots() == []

    def test_lists_all_snapshots(self, mock_data_dir):
        from src.data.data_guardian import snapshot, list_snapshots
        import time

        snapshot(reason="first")
        time.sleep(0.1)
        snapshot(reason="second")

        snapshots = list_snapshots()
        assert len(snapshots) == 2
        assert snapshots[0]["reason"] == "first"
        assert snapshots[1]["reason"] == "second"
        assert all(s["size_mb"] >= 0 for s in snapshots)
