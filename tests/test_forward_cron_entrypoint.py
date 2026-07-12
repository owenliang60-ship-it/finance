"""run_forward_data.sh 契约：顺序 / 退出码上抛 / env 加载 / 解释器 fallback / 无 secret。"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "run_forward_data.sh"


def _write_exec(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def env_project(tmp_path):
    """临时 PROJECT_DIR：fake run_update_data.sh + fake update_fmp_forward.py。"""
    project = tmp_path / "project"
    log = tmp_path / "calls.log"
    _write_exec(project / "fake_run_update_data.sh", f"""#!/usr/bin/env bash
echo "yfinance args=$* marker=${{TEST_ENV_MARKER:-unset}}" >> "{log}"
exit "${{FAKE_YF_RC:-0}}"
""")
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "update_fmp_forward.py").write_text(f"""
import os, sys
with open({str(log)!r}, "a") as f:
    f.write("fmp args=" + " ".join(sys.argv[1:]) +
            " interpreter=" + os.environ.get("FAKE_INTERPRETER_NAME", "python3") + "\\n")
sys.exit(int(os.environ.get("FAKE_FMP_RC", "0")))
""")
    (project / ".env").write_text("TEST_ENV_MARKER=loaded_from_env\n")
    return project, log


def _run(project, extra_env=None):
    env = dict(os.environ)
    env.update({
        "FINANCE_PROJECT_DIR": str(project),
        "FINANCE_ENV_FILE": str(project / ".env"),
        "FINANCE_RUN_UPDATE_DATA": str(project / "fake_run_update_data.sh"),
    })
    env.update(extra_env or {})
    return subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True)


def test_yfinance_runs_before_fmp_with_original_args(env_project):
    project, log = env_project
    result = _run(project)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines[0].startswith("yfinance args=--forward-estimates --scope=all")
    assert lines[1].startswith("fmp args=--mode weekly")


def test_yfinance_failure_blocks_fmp_and_propagates(env_project):
    project, log = env_project
    result = _run(project, {"FAKE_YF_RC": "3"})
    assert result.returncode == 3
    content = log.read_text()
    assert "yfinance" in content
    assert "fmp" not in content


def test_fmp_failure_propagates(env_project):
    project, log = env_project
    result = _run(project, {"FAKE_FMP_RC": "5"})
    assert result.returncode == 5
    assert "fmp" in log.read_text()


def test_env_file_loaded(env_project):
    project, log = env_project
    _run(project)
    assert "marker=loaded_from_env" in log.read_text()


def test_interpreter_prefers_venv_then_python3(env_project, tmp_path):
    project, log = env_project
    # 无 .venv → python3 fallback（fixture 默认路径）
    _run(project)
    assert "interpreter=python3" in log.read_text()
    log.write_text("")
    # 有可执行 .venv/bin/python → 优先使用
    venv_python = project / ".venv" / "bin" / "python"
    _write_exec(venv_python, """#!/usr/bin/env bash
FAKE_INTERPRETER_NAME=venv exec python3 "$@"
""")
    _run(project)
    assert "interpreter=venv" in log.read_text()


def test_script_contains_no_secret_literal():
    content = SCRIPT.read_text()
    for pattern in ("apikey=", "API_KEY=\"", "API_KEY='", "sk-", "token="):
        assert pattern not in content
    # key 只能经 .env source 进入进程环境
    assert 'source "$ENV_FILE"' in content
