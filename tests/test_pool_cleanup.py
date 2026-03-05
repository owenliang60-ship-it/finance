"""
Tests for pool_manager.cleanup_stale_data()
验证：清理基本面 JSON 中已退出股票的条目
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def setup_dirs(tmp_path):
    """创建模拟的 fundamental/ 目录结构"""
    fundamental_dir = tmp_path / "fundamental"
    fundamental_dir.mkdir()
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    return tmp_path, fundamental_dir, pool_dir


def _create_fundamental(fundamental_dir, symbols_data):
    """创建基本面 JSON，symbols_data 是 {symbol: data} 的 dict"""
    for fname in ["profiles", "ratios", "income", "balance_sheet", "cash_flow"]:
        with open(fundamental_dir / f"{fname}.json", "w") as f:
            json.dump(symbols_data, f)


class TestCleanupStaleData:

    def test_cleans_fundamental_json(self, setup_dirs):
        """清理基本面 JSON 中已退出股票的条目 (比例 <30% 才不触发熔断)"""
        tmp_path, fundamental_dir, pool_dir = setup_dirs

        # 10 个有效 + 2 个过期 → 删除比例 ~17%，低于 30% 阈值
        keep_symbols = [f"SYM{i}" for i in range(10)]
        data = {s: {"name": f"Company {s}"} for s in keep_symbols}
        data["DEAD"] = {"name": "Dead Co"}
        data["GONE"] = {"name": "Gone Inc"}
        _create_fundamental(fundamental_dir, data)

        with patch("src.data.pool_manager.FUNDAMENTAL_DIR", fundamental_dir), \
             patch("src.data.pool_manager.BENCHMARK_SYMBOLS", ["SPY", "QQQ"]), \
             patch("src.data.data_guardian.snapshot", return_value=None):
            from src.data.pool_manager import cleanup_stale_data
            stats = cleanup_stale_data(keep_symbols)

        # 每个 JSON 文件清理 2 条 (DEAD + GONE)，共 5 个文件
        assert stats["fundamental_cleaned"] == 10

        # 验证 JSON 内容
        for fname in ["profiles", "ratios", "income", "balance_sheet", "cash_flow"]:
            with open(fundamental_dir / f"{fname}.json") as f:
                cleaned = json.load(f)
            assert keep_symbols[0] in cleaned
            assert "DEAD" not in cleaned
            assert "GONE" not in cleaned

    def test_no_op_when_clean(self, setup_dirs):
        """池和数据完全一致时，不做任何操作"""
        tmp_path, fundamental_dir, pool_dir = setup_dirs

        _create_fundamental(fundamental_dir, {"AAPL": {}, "MSFT": {}})

        with patch("src.data.pool_manager.FUNDAMENTAL_DIR", fundamental_dir), \
             patch("src.data.pool_manager.BENCHMARK_SYMBOLS", ["SPY"]):
            from src.data.pool_manager import cleanup_stale_data
            stats = cleanup_stale_data(["AAPL", "MSFT"])

        assert stats["fundamental_cleaned"] == 0

    def test_handles_missing_dirs(self, setup_dirs):
        """fundamental/ 不存在时不报错"""
        tmp_path, fundamental_dir, pool_dir = setup_dirs

        missing_fund = tmp_path / "nonexistent_fund"

        with patch("src.data.pool_manager.FUNDAMENTAL_DIR", missing_fund), \
             patch("src.data.pool_manager.BENCHMARK_SYMBOLS", ["SPY"]):
            from src.data.pool_manager import cleanup_stale_data
            stats = cleanup_stale_data(["AAPL"])

        assert stats["fundamental_cleaned"] == 0

    def test_handles_corrupt_json(self, setup_dirs):
        """损坏的 JSON 文件跳过不报错"""
        tmp_path, fundamental_dir, pool_dir = setup_dirs

        (fundamental_dir / "profiles.json").write_text("not valid json{{{")
        _create_fundamental(fundamental_dir, {"AAPL": {}})  # 覆盖 profiles 以外的

        with patch("src.data.pool_manager.FUNDAMENTAL_DIR", fundamental_dir), \
             patch("src.data.pool_manager.BENCHMARK_SYMBOLS", ["SPY"]):
            from src.data.pool_manager import cleanup_stale_data
            # 不应该抛出异常
            stats = cleanup_stale_data(["AAPL"])

        assert stats["fundamental_cleaned"] == 0

    def test_safety_fuse_fundamental_threshold(self, setup_dirs):
        """基本面条目删除超过 30% 时触发熔断"""
        tmp_path, fundamental_dir, pool_dir = setup_dirs

        # 创建有 10 个 symbol 条目的 JSON
        data = {f"SYM{i}": {"name": f"Company {i}"} for i in range(10)}
        data["_meta"] = {"updated_at": "2026-02-14"}
        (fundamental_dir / "profiles.json").write_text(json.dumps(data))

        # 只保留 2 个 → 要删 8 个 (80%)
        with patch("src.data.pool_manager.FUNDAMENTAL_DIR", fundamental_dir), \
             patch("src.data.pool_manager.BENCHMARK_SYMBOLS", []):
            from src.data.pool_manager import cleanup_stale_data
            stats = cleanup_stale_data(["SYM0", "SYM1"])

        assert stats.get("aborted") is True
        assert stats["fundamental_cleaned"] == 0

    def test_default_reads_universe(self, setup_dirs):
        """不传 active_symbols 时从 universe.json 读取"""
        tmp_path, fundamental_dir, pool_dir = setup_dirs

        # 创建 universe.json with 10 symbols
        keep = [f"SYM{i}" for i in range(10)]
        universe = [{"symbol": s} for s in keep]
        with open(pool_dir / "universe.json", "w") as f:
            json.dump(universe, f)

        # 创建有 10 个有效 + 1 个过期条目的基本面
        data = {s: {"name": f"Company {s}"} for s in keep}
        data["DEAD"] = {"name": "Dead Co"}
        _create_fundamental(fundamental_dir, data)

        with patch("src.data.pool_manager.FUNDAMENTAL_DIR", fundamental_dir), \
             patch("src.data.pool_manager.BENCHMARK_SYMBOLS", ["SPY"]), \
             patch("src.data.pool_manager.UNIVERSE_FILE", pool_dir / "universe.json"), \
             patch("src.data.data_guardian.snapshot", return_value=None):
            from src.data.pool_manager import cleanup_stale_data
            stats = cleanup_stale_data()  # 不传参数

        # 每个文件 1 条 × 5 文件 = 5
        assert stats["fundamental_cleaned"] == 5
