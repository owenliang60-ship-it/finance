"""run_prosperity.sh picks an interpreter that exists on the cloud host (no .venv there)."""
import os
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'run_prosperity.sh'


def make_stub(path, body):
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o755)


def run(tmp_path, with_venv):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    locks = tmp_path / 'locks'
    # Pretend cron_wrapper already holds both locks (fd 9 = job, fd 8 = resource).
    make_stub(bin_dir / 'readlink', f'''case "$1" in
  */fd/9) echo {locks}/finance_prosperity.lock ;;
  */fd/8) echo {locks}/resource-market_db_writer.lock ;;
esac''')
    make_stub(bin_dir / 'python3', 'echo "python3 $*" > "$TRACE"')
    if with_venv:
        (tmp_path / '.venv' / 'bin').mkdir(parents=True)
        make_stub(tmp_path / '.venv' / 'bin' / 'python', 'echo "venv $*" > "$TRACE"')
    trace = tmp_path / 'trace'
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', TRACE=str(trace),
               FINANCE_PROJECT_DIR=str(tmp_path), FINANCE_CRON_LOCK_DIR=str(locks))
    env.pop('FINANCE_PYTHON', None)
    result = subprocess.run(['bash', str(SCRIPT)], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return trace.read_text().split()


def test_falls_back_to_python3_without_venv(tmp_path):
    words = run(tmp_path, with_venv=False)
    assert words[0] == 'python3'
    assert words[1].endswith('scripts/prosperity_weekly.py') and '--live' in words


def test_prefers_venv_when_present(tmp_path):
    assert run(tmp_path, with_venv=True)[0] == 'venv'
