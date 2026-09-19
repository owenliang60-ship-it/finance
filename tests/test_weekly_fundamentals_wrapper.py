"""Weekly quality failures are visible without weakening Premium's own gate."""
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'run_weekly_fundamentals.sh'


def make_stub(path, body):
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o755)


@pytest.mark.parametrize('update_rc,quality_rc,premium_rc,expected_rc,steps', [
    (0,0,0,0,['fundamental','quality','premium']),
    (7,0,0,7,['fundamental']),
    (0,1,0,1,['fundamental','quality','premium']),
    (0,75,0,75,['fundamental','quality']),
    (0,2,0,2,['fundamental','quality']),
    (0,0,8,8,['fundamental','quality','premium']),
    (0,1,8,8,['fundamental','quality','premium']),
])
def test_weekly_wrapper_preserves_gate_and_exposes_quality_state(tmp_path, update_rc, quality_rc, premium_rc, expected_rc, steps):
    trace=tmp_path/'trace'
    update=tmp_path/'update'
    python=tmp_path/'python'
    report=tmp_path/'report.json'
    make_stub(update, 'echo "fundamental $*" >> "$TRACE"; exit '+str(update_rc))
    make_stub(python, '''name=$(basename "$1"); shift
if [ "$name" = check.py ]; then
  echo "quality $*" >> "$TRACE"
  exit '''+str(quality_rc)+'''
fi
echo "premium $*" >> "$TRACE"
exit '''+str(premium_rc))
    env=dict(os.environ,TRACE=str(trace),FINANCE_PROJECT_DIR=str(tmp_path),
             FINANCE_RUN_UPDATE_DATA=str(update),FINANCE_PYTHON=str(python),
             FINANCE_QUALITY_CHECKER=str(tmp_path/'check.py'),FINANCE_QUALITY_REPORT=str(report),
             FINANCE_PREMIUM_BUILDER=str(tmp_path/'build.py'))
    result=subprocess.run([str(SCRIPT)],env=env,text=True,capture_output=True)
    assert result.returncode==expected_rc
    lines=trace.read_text().splitlines()
    assert [line.split()[0] for line in lines]==steps
    if 'quality' in steps:
        assert lines[1]=='quality --repair --no-lock --max-targets 200 --report '+str(report)
    if quality_rc==1 and premium_rc==0 and update_rc==0:
        assert 'quality unresolved' in result.stdout
