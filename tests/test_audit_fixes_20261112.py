"""Regression tests for the 2026-11-12 project audit fixes."""
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_risk_free_rate_defaults_to_today():
    from terminal.options import risk_free_rate as rfr
    today = datetime.now().strftime("%Y-%m-%d")
    with patch.object(rfr, "_ensure_loaded"), patch.dict(rfr._mem_cache, {today: 0.0321}, clear=True):
        assert rfr.get_risk_free_rate() == 0.0321
        assert rfr.get_risk_free_rate(today) == 0.0321


def test_hy_spread_rationale_is_in_basis_points():
    from terminal import macro_fetcher
    from terminal.macro_snapshot import MacroSnapshot
    snap = MacroSnapshot(hy_spread=5.4)
    names = [n for n in dir(macro_fetcher) if "regime" in n.lower() and callable(getattr(macro_fetcher, n))]
    texts = []
    for name in names:
        try:
            texts.append(str(getattr(macro_fetcher, name)(snap)))
        except TypeError:
            continue
    joined = " ".join(texts)
    assert "540bp" in joined
    assert "(5bp)" not in joined


def test_fundamental_summary_reads_stored_column_names():
    from src.data import fundamental_fetcher as ff
    with patch.object(ff, "get_profile", return_value={"companyName": "X", "marketCap": 5e11}), \
         patch.object(ff, "get_ratios", return_value=[{
             "price_to_earnings_ratio": 30.5, "price_to_book_ratio": 9.0,
             "gross_profit_margin": 0.6, "net_profit_margin": 0.25}]), \
         patch.object(ff, "get_income", return_value=[{"revenue": 100.0, "net_income": 25.0, "eps": 1.5}]):
        s = ff.get_fundamental_summary("X")
    assert (s["marketCap"], s["pe"], s["pb"]) == (5e11, 30.5, 9.0)
    assert (s["grossMargin"], s["netMargin"]) == (0.6, 0.25)
    assert (s["latestRevenue"], s["latestNetIncome"], s["latestEPS"]) == (100.0, 25.0, 1.5)


def test_backfill_delisted_main_resolves_candidate_symbols():
    import scripts.backfill_delisted_large_caps as mod
    assert callable(mod.get_delisted_candidate_symbols)


def test_morning_report_failure_exits_nonzero():
    import scripts.morning_report as mr
    with patch.object(sys, "argv", ["morning_report.py", "--no-telegram"]), \
         patch.object(mr, "_deliver_morning_report", side_effect=RuntimeError("boom")), \
         patch.object(mr, "build_market_signal_report", side_effect=RuntimeError("boom"), create=True):
        with pytest.raises(SystemExit) as exc:
            mr.main()
    assert exc.value.code == 1


@pytest.mark.skipif(sys.platform == "win32", reason="bash lock test")
def test_rejected_sync_run_keeps_the_owner_lock(tmp_path):
    """A second sync run must not delete the first run's lock on its way out."""
    script = (ROOT / "sync_to_cloud.sh").read_text(encoding="utf-8")
    head = script[:script.index("trap cleanup EXIT")]
    lock = tmp_path / "sync.lock"
    owner = subprocess.Popen(["sleep", "30"])
    try:
        lock.write_text(str(owner.pid))
        harness = head.replace('LOCK_FILE="/tmp/finance-companydb-sync.lock"', f'LOCK_FILE="{lock}"')
        harness += "\ntrap cleanup EXIT\nacquire_lock\necho SHOULD_NOT_REACH\n"
        run = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, cwd=tmp_path)
        assert run.returncode == 1
        assert "SHOULD_NOT_REACH" not in run.stdout
        assert lock.exists() and lock.read_text().strip() == str(owner.pid)
    finally:
        owner.kill()
        owner.wait()
