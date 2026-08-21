"""
Tests for pool_manager.get_symbols() 遗留埋点 + scripts/check_core_references.sh 静态引用检查

R11 两阶段软退役（Stop G）:
  - 埋点: 每次调用 get_symbols() 采样记录 caller 模块 + 日期到 legacy_calls.log
    （任何写入失败必须静默吞掉——get_symbols() 在生产热路径，埋点不能影响调用方）
  - 静态检查: scripts/check_core_references.sh 扫描功能代码中对遗留 Core 池入口
    （get_symbols / UNIVERSE_FILE / universe.json）的残留引用，作为运行期埋点之外
    的 dormant-path 兜底

本 worktree 没有 data/pool 目录（无 live data），因此所有埋点测试用 tmp_path +
monkeypatch 覆盖 LEGACY_CALLS_LOG_FILE，绝不触碰真实 data/pool/。
"""
import os
import subprocess
from pathlib import Path

import pytest

from src.data import pool_manager

REPO_ROOT = Path(__file__).parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_core_references.sh"

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class TestLegacyCallTelemetry:
    """get_symbols() 入口采样日志"""

    def test_get_symbols_appends_log_line_with_caller_module(self, tmp_path, monkeypatch):
        """① 调用 get_symbols() 后，日志文件增加一行，含调用方模块名"""
        log_file = tmp_path / "legacy_calls.log"
        monkeypatch.setattr(pool_manager, "LEGACY_CALLS_LOG_FILE", log_file)

        pool_manager.get_symbols()

        assert log_file.exists()
        lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 1
        # caller 模块名 = 本测试文件的 __name__（不硬编码具体字符串，
        # 兼容 pytest 不同 import mode 下的模块限定名差异）
        assert __name__ in lines[0]

    def test_multiple_calls_append_multiple_lines(self, tmp_path, monkeypatch):
        """多次调用累加多行，不覆盖（append 语义）"""
        log_file = tmp_path / "legacy_calls.log"
        monkeypatch.setattr(pool_manager, "LEGACY_CALLS_LOG_FILE", log_file)

        pool_manager.get_symbols()
        pool_manager.get_symbols()
        pool_manager.get_symbols()

        lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 3

    @pytest.mark.skipif(_IS_ROOT, reason="root 用户忽略目录权限位，无法模拟只读目录")
    def test_readonly_log_dir_does_not_raise(self, tmp_path, monkeypatch):
        """② log 目录只读时，get_symbols() 不抛异常（埋点失败静默吞掉）"""
        log_dir = tmp_path / "pool"
        log_dir.mkdir()
        log_dir.chmod(0o500)  # r-x：目录存在但不可写
        monkeypatch.setattr(pool_manager, "LEGACY_CALLS_LOG_FILE", log_dir / "legacy_calls.log")
        monkeypatch.setattr(pool_manager, "load_universe",
                            lambda: [{"symbol": "AAPL"}])

        try:
            symbols = pool_manager.get_symbols()
        finally:
            log_dir.chmod(0o700)  # 恢复权限，便于 tmp_path 清理

        assert symbols == ["AAPL"]  # 正常返回值不受埋点失败影响
        assert not (log_dir / "legacy_calls.log").exists()

    def test_missing_parent_dir_is_auto_created(self, tmp_path, monkeypatch):
        """父目录尚不存在（worktree 无 data/pool 时的真实情况）时自动创建并正常写入"""
        log_file = tmp_path / "does_not_exist_yet" / "nested" / "legacy_calls.log"
        monkeypatch.setattr(pool_manager, "LEGACY_CALLS_LOG_FILE", log_file)

        pool_manager.get_symbols()

        assert log_file.exists()
        assert __name__ in log_file.read_text(encoding="utf-8")

    def test_uncreatable_parent_dir_does_not_raise(self, tmp_path, monkeypatch):
        """父目录路径被一个同名文件占用、无法创建时，静默吞掉异常"""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")  # 占用本应是目录的路径
        log_file = blocker / "pool" / "legacy_calls.log"
        monkeypatch.setattr(pool_manager, "LEGACY_CALLS_LOG_FILE", log_file)
        monkeypatch.setattr(pool_manager, "load_universe",
                            lambda: [{"symbol": "AAPL"}])

        symbols = pool_manager.get_symbols()  # 不应抛异常

        assert symbols == ["AAPL"]


class TestCheckCoreReferencesScript:
    """scripts/check_core_references.sh 静态引用检查"""

    def test_script_has_valid_posix_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(CHECK_SCRIPT)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_currently_exits_nonzero_with_known_references(self):
        """③ 迁移矩阵（T20）尚未执行，脚本当前必须 exit 非 0，
        且清单中包含已知、明确尚未迁移的引用（与现实一致）"""
        result = subprocess.run(
            ["bash", str(CHECK_SCRIPT)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert result.returncode != 0

        output = result.stdout
        # T20 矩阵中列出、本 task（19）不负责迁移的具体行，抽样核对清单与现实一致
        # T20 #11 迁移后 scripts/backfill_iv.py 的 pool_manager 引用已彻底删除
        # （--symbols 必填，无回退路径），故从清单移除——这正是本 telemetry 想记录的进展
        known_unmigrated = [
            "src/data/data_validator.py",       # T20 #1/#2（保留 legacy 回退分支）
            "src/indicators/engine.py",         # T20 #13
            "terminal/company_store.py",        # T20 #3
            "terminal/dashboard.py",            # T20 #3
            "scripts/scan_themes.py",           # T20 #12
        ]
        for needle in known_unmigrated:
            assert needle in output, f"expected reference to {needle} in output"

    def test_excludes_pool_manager_itself(self):
        """pool_manager.py 是遗留入口的定义处，应被排除，不出现在清单里"""
        result = subprocess.run(
            ["bash", str(CHECK_SCRIPT)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert "src/data/pool_manager.py:" not in result.stdout

    def test_excludes_known_unrelated_substring_collisions(self):
        """已知子串误命中不应出现：MarketStore.get_symbols 系方法、
        BROAD_/EXTENDED_UNIVERSE_FILE、extended_/broad_universe.json、
        以及包含 "get_symbols" 子串的无关标识符（如 target_symbols）"""
        result = subprocess.run(
            ["bash", str(CHECK_SCRIPT)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        output = result.stdout
        assert "src/data/market_store.py" not in output
        assert "get_symbols_with_market_cap_at" not in output
        assert "BROAD_UNIVERSE_FILE" not in output
        assert "EXTENDED_UNIVERSE_FILE" not in output
        assert "backtest/rebalancer.py" not in output

    @staticmethod
    def _is_noise_line(file_, content):
        """通过 `source` 脚本（不触发主流程，见脚本内 BASH_SOURCE 守卫）直接单测
        _is_noise_line()。返回 shell 退出码：0=判定为噪声（排除），1=判定为真实引用（保留）。
        file_/content 经参数传递而非拼进脚本字符串，避免引号/特殊字符转义问题。"""
        script = f'source "{CHECK_SCRIPT}"; _is_noise_line "$1" "$2"'
        result = subprocess.run(
            ["bash", "-c", script, "check_helper", file_, content],
            capture_output=True, text=True,
        )
        return result.returncode

    def test_noise_filter_flags_pool_manager_dotted_call_as_real_reference(self):
        """回归 review round-1 finding：`.get_symbols(` 排除曾用无界通配
        `*.get_symbols\\(*`，连 pool_manager.get_symbols( 这种真实遗留引用也一并
        滤掉了。修复后：点号限定调用只有明确的 MarketStore 实例形状
        （store./self.get_symbols(）才被排除，pool_manager. 前缀必须仍被判定为
        真实引用（保留，rc=1）。"""
        rc = self._is_noise_line(
            "some/hypothetical_future_file.py",
            "        symbols = pool_manager.get_symbols()",
        )
        assert rc == 1, "pool_manager.get_symbols( must be FLAGGED, not silently excluded"

    def test_noise_filter_excludes_market_store_instance_call(self):
        """store.get_symbols( / self.get_symbols( 是 MarketStore 的实例调用/方法体
        内调用形状，明确排除（rc=0）。"""
        assert self._is_noise_line(
            "src/data/market_store.py", '        return self.get_symbols("daily_price")'
        ) == 0
        assert self._is_noise_line(
            "scripts/rs_universe_scan.py", '    cached_symbols = set(store.get_symbols("daily_price"))'
        ) == 0
