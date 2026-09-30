"""Synthetic builders for prosperity engine tests (values cite market.db where noted)."""
from terminal.prosperity.types import EpsQuarter, SymbolHistory


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


QTRS = [("2025-03-31", "2025", "Q1", "2025-05-01 16:00:00"), ("2025-06-30", "2025", "Q2", "2025-07-31 16:00:00"),
        ("2025-09-30", "2025", "Q3", "2025-10-30 16:00:00"), ("2025-12-31", "2025", "Q4", "2026-02-20 16:00:00")]


def _meta(d, fy, p, acc, cur="USD"):
    return {"date": d, "fiscal_year": fy, "period": p, "reported_currency": cur,
            "filing_date": acc[:10], "accepted_date": acc}


def inc(d, fy, p, acc, rev=100.0):
    return {**_meta(d, fy, p, acc), "revenue": rev, "cost_of_revenue": rev * 0.4,
            "gross_profit": rev * 0.6, "net_income": rev * 0.1}


def bs(d, fy, p, acc):
    return {**_meta(d, fy, p, acc), "goodwill_and_intangible_assets": 50.0, "total_assets": 500.0}


def cf(d, fy, p, acc):
    return {**_meta(d, fy, p, acc), "operating_cash_flow": 20.0, "capital_expenditure": -5.0,
            "free_cash_flow": 15.0}


def hist(rows=QTRS, earnings=()):
    return SymbolHistory(symbol="AAA", income=[inc(*r) for r in rows], balance=[bs(*r) for r in rows],
                         cashflow=[cf(*r) for r in rows], vintage={}, earnings=list(earnings),
                         estimates=[], splits=[], closes=[], market_caps=[], profile=None, is_adr=False)


from datetime import date, timedelta


def er(fiscal, announce, eps, est=None, method="estimates_window"):
    return {"fiscal_date": fiscal, "announce_date": announce, "eps_actual": eps,
            "eps_estimated": est, "match_method": method}


def seq(points):
    """(fiscal_date, eps) → earnings rows announced 30 days after quarter end."""
    return [er(f, (date.fromisoformat(f) + timedelta(days=30)).isoformat(), e) for f, e in points]


def gaap_rows(rows, divisor_before=1.0, last_pre=None, gaap_multiplier_before=1.0, scale=0.8):
    """Income rows whose GAAP EPS is 0.8 × fully adjusted street EPS (FMP income is usually fully adjusted).

    divisor_before: street quarters up to last_pre are in pre-split units, so adjusted = street / divisor.
    gaap_multiplier_before: simulate a GAAP series that itself was left unadjusted before last_pre.
    """
    out = []
    for r in rows:
        pre = last_pre is not None and r["fiscal_date"] <= last_pre
        adjusted = r["eps_actual"] / divisor_before if pre else r["eps_actual"]
        out.append({"date": r["fiscal_date"],
                    "eps_diluted": scale * adjusted * (gaap_multiplier_before if pre else 1.0)})
    return out


import math


def closes_series(start, end, start_price, drift=0.001, phase=0.0):
    out, d, t = [], date.fromisoformat(start), 0
    while d <= date.fromisoformat(end):
        if d.weekday() < 5:
            out.append((d.isoformat(),
                        round(start_price * (1 + drift) ** t * (1 + 0.02 * math.sin(t / 3 + phase)), 4)))
            t += 1
        d += timedelta(days=1)
    return out


from datetime import date, timedelta

from terminal.prosperity.types import (ConsensusInputs, EpsQuarter, InputPacket, NtmResult,
                                       PreAnnouncement, QuarterInputs, RevisionResult)

_QEND = {3: 31, 6: 30, 9: 30, 12: 31}


def qends(n, last="2026-06-30"):
    """n calendar quarter ends, oldest first, ending at `last`."""
    y, m, out = int(last[:4]), int(last[5:7]), []
    for _ in range(n):
        out.append(date(y, m, _QEND[m]).isoformat())
        y, m = (y - 1, 12) if m == 3 else (y, m - 3)
    return out[::-1]


def qin(fiscal, rev=100.0, gm=0.5, nm=0.1, fcfm=0.2, days=91, capex=-5.0, labels=()):
    """QuarterInputs; margins are fractions of revenue."""
    part = lambda frac: None if rev is None or frac is None else rev * frac
    gp = part(gm)
    return QuarterInputs(fiscal_date=fiscal, fiscal_year=fiscal[:4], period="Q", period_days=days,
                         reported_currency="USD", available_on=fiscal, availability_basis="accepted_date",
                         revenue=rev, cost_of_revenue=None if gp is None else rev - gp, gross_profit=gp,
                         net_income=part(nm), operating_cash_flow=None, capital_expenditure=capex,
                         free_cash_flow=part(fcfm), labels=tuple(labels), nulled=())


def eps_q(fiscal, eps, labels=()):
    announce = (date.fromisoformat(fiscal) + timedelta(days=30)).isoformat()
    return EpsQuarter(fiscal, announce, eps, None, tuple(labels))


def cons(pre=None, price_pre=None, ntm=None, ttm=None, delta=None, missing=None, rev_quarters=4):
    missing = dict(missing or {})
    fixed = tuple(qends(rev_quarters, "2027-06-30")) if delta is not None else ()
    return ConsensusInputs(
        pre_announce=PreAnnouncement(pre, None if pre is None else "local_snapshot", None, price_pre,
                                     None if pre is not None else missing.get("pre_announce", "no_pre_announce_snapshot")),
        ntm=NtmResult(ntm, None if ntm is None else "quarter_sum", (), None,
                      None if ntm is not None else missing.get("ntm", "no_current_snapshot")),
        ttm_eps=ttm, ttm_quarters=(),
        revision=RevisionResult(delta, 4, fixed, None, None,
                                None if delta is not None else missing.get("revision", "no_base_snapshot")),
        unit_factor=None if missing else 1.0, missing_reasons=missing)


def pkt(quarters, eps=(), consensus=None, *, symbol="AAA", as_of="2026-09-26", price=100.0,
        sector="Technology", industry="Semiconductors", flags=(), listing_date="2000-01-01"):
    return InputPacket(symbol=symbol, as_of=as_of, membership_basis="extended_membership", sector=sector,
                       industry=industry, current_fiscal=quarters[-1].fiscal_date if quarters else None,
                       quarter_bucket=None, data_age_days=None, reported_this_week=False,
                       quarters=tuple(quarters), eps=tuple(eps), consensus=consensus or cons(),
                       price_asof=price, price_date=as_of if price is not None else None, market_cap_asof=None,
                       beta=None, pit={}, pit_basis="live", flags=tuple(flags), archive={},
                       listing_date=listing_date)          # listing_date: D-2 方案 A


from terminal.prosperity.factors import FactorRow
from terminal.prosperity.schemes import SCORING_FACTORS

OK_AUX = {"net_margin_yoy": 1.0, "ntm_eps": 5.0, "ttm_eps": 4.0, "gm_slope": None, "ep_ntm": None}


def full_values(i):
    """Distinct values per factor; a higher i is higher on every factor."""
    return {f: float(i + k) for k, f in enumerate(SCORING_FACTORS)}


def frow(symbol, values, *, sector="Technology", industry="Semiconductors", disclosed=8,
         listing_days=5000, aux=None, flags=(), as_of="2026-09-26"):
    vals = {f: values.get(f) for f in SCORING_FACTORS}
    return FactorRow(symbol=symbol, as_of=as_of, current_fiscal="2026-06-30", sector=sector, industry=industry,
                     disclosed_quarters=disclosed, listing_days=listing_days, values=vals,
                     missing={f: "test_missing" for f, v in vals.items() if v is None},
                     aux={**OK_AUX, **(aux or {})}, labels=(), packet_flags=tuple(flags), pit_basis="live")
