"""Synthetic builders for prosperity engine tests (values cite market.db where noted)."""
from terminal.prosperity.types import EpsQuarter


def est(snap, fiscal, eps, n=10, period="Q"):
    return {"snapshot_date": snap, "fiscal_date": fiscal, "period_type": period,
            "snapshot_kind": "weekly", "eps_avg": eps, "num_analysts_eps": n}


def eq(fiscal, announce, eps):
    return EpsQuarter(fiscal, announce, eps, None, ())


import json
from pathlib import Path

from src.data.market_store import MarketStore


def seed_db(path) -> Path:
    w = MarketStore(db_path=path)
    conn = w._get_conn()
    with conn:
        conn.execute("INSERT INTO security_master (symbol, is_adr, eligible, reason, updated_at) VALUES "
                     "('AAA',0,1,'ok','t'),('BBB',1,1,'ok','t'),('CCC',0,0,'non_common_instrument','t')")
        conn.execute("INSERT INTO extended_membership (symbol, effective_from) VALUES "
                     "('AAA','2026-08-21'),('BBB','2026-08-21'),('CCC','2026-08-21')")
        conn.execute("INSERT INTO company_profile (symbol, payload, updated_at) VALUES (?,?,?)",
                     ("AAA", json.dumps({"sector": "Technology", "industry": "Semiconductors"}), "t"))
    w.upsert_income("AAA", [{"date": "2026-03-31", "reportedCurrency": "USD", "fiscalYear": "2026",
                             "period": "Q1", "revenue": 100.0, "filingDate": "2026-05-01",
                             "acceptedDate": "2026-05-01 16:00:00"}])
    w.upsert_fmp_estimates("AAA", [
        {"snapshot_date": "2026-09-26", "fiscal_date": "2026-09-30", "period_type": "Q",
         "snapshot_kind": "weekly", "eps_avg": 1.0},
        {"snapshot_date": "2026-07-13", "fiscal_date": "2026-09-30", "period_type": "Q",
         "snapshot_kind": "backfill", "eps_avg": 9.9}])
    w.upsert_historical_market_cap("AAA", [{"date": "2021-12-31", "market_cap": 2e10},
                                           {"date": "2026-09-25", "market_cap": 3e10}])
    return Path(path)
