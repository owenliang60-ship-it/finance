"""cron_wrapper.sh critical lock 语义测试。

macOS 无 flock(1)，用 fake flock shim 驱动 busy/free 分支：
wrapper 内 flock 调用顺序确定（第 1 次 = job 锁，第 2 次 = 资源锁），
shim 按调用序号对照 FAKE_FLOCK_BUSY_CALLS 判 busy。
真实 flock 语义由云端 Linux smoke（Task 12/13）覆盖。
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).parent.parent / "scripts" / "cron_wrapper.sh"

FAKE_FLOCK = """#!/usr/bin/env bash
# fake flock -n FD: busy iff this invocation ordinal is in FAKE_FLOCK_BUSY_CALLS
count_file="${FAKE_FLOCK_COUNT_FILE:?}"
n="$(cat "$count_file" 2>/dev/null || echo 0)"
n=$((n + 1))
echo "$n" > "$count_file"
case ",${FAKE_FLOCK_BUSY_CALLS:-}," in
  *,"$n",*) exit 1 ;;
esac
exit 0
"""

FAKE_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$FAKE_CURL_LOG"
exit 0
"""


def _write_exec(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def harness(tmp_path):
    shim_dir = tmp_path / "shims"
    _write_exec(shim_dir / "flock", FAKE_FLOCK)
    _write_exec(shim_dir / "curl", FAKE_CURL)
    project = tmp_path / "project"
    (project / "logs").mkdir(parents=True)
    (project / ".env").write_text("")
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    curl_log = tmp_path / "curl.log"
    marker = tmp_path / "ran.marker"

    def run(job="finance_forward", extra_env=None, command=None,
            busy_calls=""):
        count_file = tmp_path / f"flock_count_{os.urandom(4).hex()}"
        env = dict(os.environ)
        env.update({
            "PATH": f"{shim_dir}:{env['PATH']}",
            "FINANCE_PROJECT_DIR": str(project),
            "FINANCE_ENV_FILE": str(project / ".env"),
            "FINANCE_LOG_DIR": str(project / "logs"),
            "FINANCE_CRON_LOCK_DIR": str(lock_dir),
            "FAKE_CURL_LOG": str(curl_log),
            "FAKE_FLOCK_COUNT_FILE": str(count_file),
            "FAKE_FLOCK_BUSY_CALLS": busy_calls,
            "TELEGRAM_BOT_TOKEN": "canary_" + "tg_token",
            "TELEGRAM_CHAT_ID": "12345",
        })
        env.update(extra_env or {})
        cmd = command or ["bash", "-c", f"touch {marker}"]
        return subprocess.run(
            ["bash", str(WRAPPER), job, "test.log"] + cmd,
            env=env, capture_output=True, text=True)

    return SimpleNamespaceLike(run=run, lock_dir=lock_dir, marker=marker,
                               curl_log=curl_log,
                               log=project / "logs" / "test.log")


class SimpleNamespaceLike:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_default_job_lock_busy_preserves_skip_exit_0(harness):
    result = harness.run(job="finance_daily", busy_calls="1")
    assert result.returncode == 0
    assert "SKIP locked" in harness.log.read_text()
    assert not harness.marker.exists()          # busy 时命令不得执行
    assert not harness.curl_log.exists()        # 默认不告警


def test_lock_busy_rc_75_alerts_and_exits_75(harness):
    result = harness.run(busy_calls="1",
                         extra_env={"FINANCE_CRON_LOCK_BUSY_RC": "75"})
    assert result.returncode == 75
    text = harness.log.read_text()
    assert "SKIP locked" in text and "rc=75" in text
    assert not harness.marker.exists()
    assert harness.curl_log.exists()             # 走既有私人告警路径
    curl_args = harness.curl_log.read_text()
    assert "rc=75" in curl_args


def test_shared_resource_key_blocks_across_job_names(harness):
    # 模拟另一 job 名持有 market_db_writer 资源锁（job 锁空闲、资源锁 busy）
    result = harness.run(
        job="finance_forward", busy_calls="2",
        extra_env={"FINANCE_CRON_RESOURCE_KEY": "market_db_writer",
                   "FINANCE_CRON_LOCK_BUSY_RC": "75"})
    assert result.returncode == 75
    assert "resource-market_db_writer" in harness.log.read_text()
    assert not harness.marker.exists()


def test_resource_lock_free_runs_command(harness):
    result = harness.run(
        extra_env={"FINANCE_CRON_RESOURCE_KEY": "market_db_writer"})
    assert result.returncode == 0
    assert harness.marker.exists()
    assert "OK duration=" in harness.log.read_text()


def test_alert_contains_no_env_secrets(harness):
    secret = "canary_" + "env_secret_value"
    harness.run(busy_calls="1",
                extra_env={"FINANCE_CRON_LOCK_BUSY_RC": "75",
                           "FMP_API_KEY": secret})
    curl_args = harness.curl_log.read_text()
    assert secret not in curl_args               # env secret 绝不进任何 curl 参数
    # bot token 是 API URL 的固有形态；message 文本本身不得含 token
    message_lines = [l for l in curl_args.splitlines() if l.startswith("text=")]
    assert message_lines
    assert all(secret not in l and "canary_" + "tg_token" not in l
               for l in message_lines)
