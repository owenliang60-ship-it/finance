"""Synthetic ACME market.db for the valuation tests (plan 2026-11-22 Task 2).

`valuation_db` returns (db_path, seed). `seed(**variants)` writes a fresh database and
returns its path. Variants: fadr (TWD reporter listed as ADR, ×32), thin (FY-blend NTM),
snapshot (weekly snapshot date), and edit (callable that mutates the row dict first).
"""
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.data.market_store import MarketStore
from terminal.valuation.facts import load_facts

QUARTER_ENDS = ("2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31",
                "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")
NTM_QUARTERS = ("2026-12-31", "2027-03-31", "2027-06-30", "2027-09-30")
FX = 32.0
LEDGER_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "valuation" / "ACME"


def _plus(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _weekdays(start: str, end: str):
    d, stop = date.fromisoformat(start), date.fromisoformat(end)
    while d <= stop:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def acme_rows(*, fadr=False, thin=False, snapshot="2026-11-21"):
    """Plain row lists; amounts in reporting currency (×32 when fadr), street EPS always USD."""
    m = FX if fadr else 1.0
    income, earnings = [], []
    for i, end in enumerate(QUARTER_ENDS):
        late = i >= 8
        eps, rev, ni = (2.0, 1500e6, 200e6) if late else (1.0, 1000e6, 100e6)
        month = int(end[5:7])
        income.append({"date": end, "reportedCurrency": "TWD" if fadr else "USD", "fiscalYear": end[:4],
                       "period": f"Q{month // 3}", "filingDate": _plus(end, 30),
                       "acceptedDate": _plus(end, 30) + " 16:00:00", "revenue": rev * m,
                       "netIncome": ni * m, "epsDiluted": eps * m, "weightedAverageShsOutDil": 100e6})
        s = 0.05 if i % 2 == 0 else -0.05
        earnings.append({"announce_date": _plus(end, 30), "fiscal_date": end, "match_method": "estimates_window",
                         "eps_actual": eps, "eps_estimated": eps, "revenue_actual": rev,
                         "revenue_estimated": rev / (1 + s)})
    earnings.append({"announce_date": "2027-01-30", "fiscal_date": "2026-12-31", "match_method": "estimates_window",
                     "eps_actual": None, "eps_estimated": 2.5, "revenue_actual": None,
                     "revenue_estimated": 1600e6})
    estimates = []
    for k, fiscal in enumerate(NTM_QUARTERS):
        estimates.append({"snapshot_date": snapshot, "fiscal_date": fiscal, "period_type": "Q",
                          "snapshot_kind": "weekly", "eps_avg": 2.5 * m, "eps_low": 2.0 * m, "eps_high": 3.0 * m,
                          "rev_avg": 1600e6 * m, "rev_low": 1500e6 * m, "rev_high": 1800e6 * m,
                          "num_analysts_eps": 2 if thin and k >= 2 else 10})
    estimates.append({"snapshot_date": snapshot, "fiscal_date": "2026-12-31", "period_type": "FY",
                      "snapshot_kind": "weekly", "eps_avg": 8.5 * m, "net_income_avg": 850e6 * m,
                      "rev_avg": 6100e6 * m})
    estimates.append({"snapshot_date": snapshot, "fiscal_date": "2027-12-31", "period_type": "FY",
                      "snapshot_kind": "weekly", "eps_avg": 11.0 * m, "net_income_avg": 1100e6 * m,
                      "rev_avg": 6600e6 * m})
    return {
        "security_master": {"symbol": "ACME", "is_adr": 1 if fadr else 0},
        "income": income,
        "earnings": earnings,
        "estimates": estimates,
        "run": {"snapshot_date": snapshot, "run_kind": "weekly", "status": "complete",
                "target_universe": ["ACME"], "target_count": 1,
                "started_at": snapshot + "T03:00:00+00:00", "completed_at": snapshot + "T04:00:00+00:00"},
        "forward": [{"date": "2026-11-21", "period": "0q", "eps_avg": 2.5}],
        "prices": [{"date": d, "close": 80.0} for d in _weekdays("2025-06-02", "2026-11-20")],
    }


def write_db(path: Path, rows) -> Path:
    store = MarketStore(db_path=path)
    conn = store._get_conn()
    sm = rows["security_master"]
    with conn:
        conn.execute("INSERT INTO security_master (symbol, is_adr, eligible, reason, updated_at) "
                     "VALUES (?, ?, 1, 'ok', 't')", (sm["symbol"], sm["is_adr"]))
    if rows["income"]:
        store.upsert_income("ACME", rows["income"])
    if rows["earnings"]:
        store.replace_fmp_earnings("ACME", rows["earnings"])
    if rows["estimates"]:
        store.upsert_fmp_estimates("ACME", rows["estimates"])
    if rows["run"]:
        store.upsert_fmp_forward_run(rows["run"])
    if rows["forward"]:
        store.upsert_forward_estimates("ACME", rows["forward"])
    if rows["prices"]:
        store.upsert_daily_prices("ACME", rows["prices"])
    store.close()
    # Rollback-journal mode: a read-only open of a WAL file creates -wal/-shm side files,
    # which would hide a real write from the zero-write directory check.
    raw = sqlite3.connect(str(path))
    raw.execute("PRAGMA journal_mode=DELETE")
    raw.close()
    return Path(path)


@pytest.fixture
def valuation_db(tmp_path):
    made = []

    def seed(*, edit=None, **variants) -> Path:
        rows = acme_rows(**variants)
        if edit is not None:
            edit(rows)
        made.append(tmp_path / f"market-{len(made)}.db")
        return write_db(made[-1], rows)

    return seed(), seed


@pytest.fixture
def ledger_file(tmp_path):
    """make(*(old, new), text=None, append="") → path of an edited ACME ledger beside a copy of its sources."""
    made = []

    def make(*edits, text=None, append=""):
        body = text if text is not None else (LEDGER_DIR / "ledger.toml").read_text(encoding="utf-8")
        for old, new in edits:
            assert old in body, old
            body = body.replace(old, new, 1)
        root = tmp_path / f"ledger-{len(made)}"
        shutil.copytree(LEDGER_DIR / "sources", root / "sources")
        made.append(root / "ledger.toml")
        made[-1].write_text(body + append, encoding="utf-8")
        return made[-1]

    return make


@pytest.fixture
def read_facts():
    def load(path, as_of="2026-11-22"):
        store = MarketStore(db_path=path, read_only=True)
        try:
            return load_facts(store, "ACME", as_of)
        finally:
            store.close()

    return load
