"""Saturday fundamentals -> Premium Pool ordering contract."""
import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_weekly_fundamentals.sh"


def make_stub(path, body):
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("update_rc", "premium_rc", "expected_rc", "expected"),
    [(0, 0, 0, ["fundamental --fundamental", "premium build.py"]),
     (7, 0, 7, ["fundamental --fundamental"]),
     (0, 8, 8, ["fundamental --fundamental", "premium build.py"])],
)
def test_weekly_wrapper_is_strictly_ordered_and_propagates_failure(
    tmp_path, update_rc, premium_rc, expected_rc, expected
):
    trace = tmp_path / "trace"
    update = tmp_path / "update"
    python = tmp_path / "python"
    builder = tmp_path / "build.py"
    builder.write_text("# sentinel\n", encoding="utf-8")
    make_stub(update, 'echo "fundamental $*" >> "$TRACE"; exit {}'.format(update_rc))
    make_stub(python, 'echo "premium $(basename "$1")" >> "$TRACE"; exit {}'.format(premium_rc))
    env = dict(os.environ, TRACE=str(trace), FINANCE_PROJECT_DIR=str(tmp_path),
               FINANCE_RUN_UPDATE_DATA=str(update), FINANCE_PYTHON=str(python),
               FINANCE_PREMIUM_BUILDER=str(builder))
    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
    assert result.returncode == expected_rc
    assert trace.read_text(encoding="utf-8").splitlines() == expected
