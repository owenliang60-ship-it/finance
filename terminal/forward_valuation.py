"""Six-basket PIT valuation from frozen analyst consensus, not certified GAAP.

No network or writes here. Rule A NTM uses four forecast quarters; blend uses
three street actual EPS observations times visible diluted shares plus one
forecast NI. Historical current-profile market caps are never substituted.
"""

import json
import math
import sqlite3
from datetime import date
from pathlib import Path
from statistics import median

from src.data.fmp_forward_ingestion import (
    load_basket_configs,
    parse_forward_run_evidence,
    non_equity_holding_reason,
)
from src.data.security_source_corrections import (
    apply_pit_security_corrections, load_security_source_corrections,
)
from terminal.basket_pe_aggregate import compute_aggregate_basket_pe
from terminal.hindsight_ntm_valuation import (
    is_same_fiscal_quarter,
    ESTIMATE_SCALE_GUARD_FACTOR,
)
from terminal.historical_basket_valuation import select_asof_fx, select_asof_market_cap
from terminal.historical_market_cap_sanity import (
    scan_market_cap_candidates,
    accepted_market_cap_status,
)
from terminal.index_pe_weekly import (
    default_share_class_config,
    resolve_company_market_cap,
)

FULL_BASKETS = ("SPY", "QQQ", "SOX", "MAGS", "IGV", "XLF")
CONFIG_DIR = Path(__file__).parent.parent / "config" / "baskets"
BASIS = "analyst_consensus_not_verified_gaap"
METHODOLOGY = "pit-consensus-1.0"
MINIMUM_COVERAGE = 0.90


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _continuous(rows, key="fiscal_date"):
    dates = [_day(r.get(key)) for r in rows]
    return bool(
        dates
        and all(dates)
        and all(60 <= (b - a).days <= 120 for a, b in zip(dates, dates[1:]))
    )


def _forecast_rows(rows, snapshot_date):
    return sorted(
        [
            r
            for r in rows
            if r.get("snapshot_date") == snapshot_date
            and r.get("snapshot_kind") == "weekly"
            and r.get("period_type") == "Q"
            and _day(r.get("fiscal_date"))
        ],
        key=lambda r: r["fiscal_date"],
    )


def _implausible_scale(currency, visible, forecasts):
    if currency == "USD":
        return False
    anchors = [
        abs(_number(r.get("net_income")))
        for r in visible[-8:]
        if _number(r.get("net_income")) not in (None, 0)
    ]
    if not anchors:
        return True
    anchor = median(anchors)
    return any(
        abs(float(r["net_income_avg"])) > anchor * ESTIMATE_SCALE_GUARD_FACTOR
        or 0 < abs(float(r["net_income_avg"])) * ESTIMATE_SCALE_GUARD_FACTOR < anchor
        for r in forecasts
    )


def compute_forward_member(
    *, symbol, snapshot_date, estimates, earnings, income, fx, splits
):
    asof = date.fromisoformat(snapshot_date)
    result = {
        "symbol": symbol,
        "earnings_basis": BASIS,
        "ntm_net_income_usd": None,
        "blend_net_income_usd": None,
        "ntm_eps": None,
        "ntm_quarters": [],
        "blend_quarters": [],
        "ntm_exclusion_reason": None,
        "blend_exclusion_reason": None,
        "warnings": [],
    }
    visible = sorted(
        [
            r
            for r in income
            if str(r.get("period") or "").startswith("Q")
            and _day(r.get("date"))
            and _day(r["date"]) <= asof
            and _day(r.get("accepted_date"))
            and _day(r["accepted_date"]) <= asof
        ],
        key=lambda r: r["date"],
    )
    currency = (
        str(visible[-1].get("reported_currency") or "").upper() if visible else ""
    )
    rate = (
        select_asof_fx(currency, fx.get(currency, []), snapshot_date)
        if currency
        else None
    )
    forecasts = _forecast_rows(estimates, snapshot_date)
    ntm = [r for r in forecasts if r["fiscal_date"] >= snapshot_date][:5]
    selected = ntm[:4]
    if len(selected) != 4 or any(
        _number(r.get("net_income_avg")) is None for r in selected
    ):
        result["ntm_exclusion_reason"] = "four_forecast_quarters_missing"
    elif (
        not _continuous(selected)
        or not 0 <= (_day(selected[0]["fiscal_date"]) - asof).days <= 120
    ):
        result["ntm_exclusion_reason"] = "ntm_window_not_continuous_or_anchored"
    elif len(ntm) > 4 and is_same_fiscal_quarter(
        ntm[3]["fiscal_date"], ntm[4]["fiscal_date"]
    ):
        result["ntm_exclusion_reason"] = "duplicate_forecast_quarter"
    elif not rate:
        result["ntm_exclusion_reason"] = "reporting_currency_or_fx_unavailable"
    else:
        if _implausible_scale(currency, visible, selected):
            result["ntm_exclusion_reason"] = "estimate_currency_scale_implausible"
        else:
            result["ntm_quarters"] = [
                {
                    "fiscal_date": r["fiscal_date"],
                    "source": "consensus",
                    "snapshot_date": snapshot_date,
                    "net_income_local": float(r["net_income_avg"]),
                    "currency": currency,
                    "usd_per_unit": rate["usd_per_unit"],
                    "fx_date": rate["date"],
                    "net_income_usd": float(r["net_income_avg"]) * rate["usd_per_unit"],
                }
                for r in selected
            ]
            result["ntm_net_income_usd"] = sum(
                q["net_income_usd"] for q in result["ntm_quarters"]
            )
            if all(_number(r.get("eps_avg")) is not None for r in selected):
                result["ntm_eps"] = sum(float(r["eps_avg"]) for r in selected)
            if any((_number(r.get("num_analysts_eps")) or 0) < 3 for r in selected):
                result["warnings"].append("thin_coverage")
            if currency != "USD":
                result["warnings"].append(
                    "consensus_currency_inferred_from_visible_income"
                )

    actuals = sorted(
        [
            r
            for r in earnings
            if r.get("match_method") == "estimates_window"
            and _day(r.get("fiscal_date"))
            and _day(r.get("announce_date"))
            and _day(r["fiscal_date"]) < _day(r["announce_date"]) <= asof
            and _number(r.get("eps_actual")) is not None
        ],
        key=lambda r: r["fiscal_date"],
    )
    actuals = actuals[-4:]
    if len(actuals) < 3 or not _continuous(actuals):
        result["blend_exclusion_reason"] = (
            "three_visible_street_quarters_missing_or_ambiguous"
        )
        return result
    actuals = actuals[-3:]
    next_est = [
        r
        for r in forecasts
        if r["fiscal_date"] > actuals[-1]["fiscal_date"]
        and not is_same_fiscal_quarter(r["fiscal_date"], actuals[-1]["fiscal_date"])
    ]
    if (
        not next_est
        or not _continuous(actuals + next_est[:1])
        or _number(next_est[0].get("net_income_avg")) is None
        or (
            len(next_est) > 1
            and is_same_fiscal_quarter(
                next_est[0]["fiscal_date"], next_est[1]["fiscal_date"]
            )
        )
    ):
        result["blend_exclusion_reason"] = (
            "next_unreported_forecast_missing_or_ambiguous"
        )
        return result
    evidence = []
    for actual in actuals:
        fiscal = actual["fiscal_date"]
        matching = [r for r in visible if is_same_fiscal_quarter(r["date"], fiscal)]
        if len(matching) > 1:
            result["blend_exclusion_reason"] = "diluted_shares_quarter_ambiguous"
            return result
        if not matching:
            matching = [r for r in visible if r["date"] <= fiscal][-1:]
        shares_row = matching[0] if matching else {}
        shares = _number(shares_row.get("weighted_average_shs_out_dil"))
        if (
            not shares
            or shares <= 0
            or (asof - (_day(shares_row.get("date")) or asof)).days > 450
        ):
            result["blend_exclusion_reason"] = "visible_diluted_shares_missing"
            return result
        if any(
            shares_row["date"] < str(r.get("date") or "") <= snapshot_date
            and r.get("numerator") != r.get("denominator")
            for r in splits
        ):
            result["blend_exclusion_reason"] = "shares_split_basis_uncertain"
            return result
        actual_currency = str(shares_row.get("reported_currency") or "").upper()
        if actual_currency != "USD":
            # Earnings EPS can be quoted per ADR/in trading currency while
            # filing shares are ordinary shares in reporting currency. Neither
            # table carries the conversion contract needed for their product.
            result["blend_exclusion_reason"] = (
                "street_eps_currency_or_adr_basis_unverified"
            )
            return result
        actual_fx = select_asof_fx(
            actual_currency, fx.get(actual_currency, []), snapshot_date
        )
        if not actual_fx:
            result["blend_exclusion_reason"] = "actual_currency_or_fx_unavailable"
            return result
        evidence.append(
            {
                "source": "street_actual",
                "fiscal_date": fiscal,
                "announce_date": actual["announce_date"],
                "eps_actual": float(actual["eps_actual"]),
                "shares": shares,
                "shares_fiscal_date": shares_row["date"],
                "shares_accepted_date": shares_row["accepted_date"],
                "currency": actual_currency,
                "usd_per_unit": actual_fx["usd_per_unit"],
                "net_income_usd": float(actual["eps_actual"])
                * shares
                * actual_fx["usd_per_unit"],
            }
        )
    if not rate:
        result["blend_exclusion_reason"] = "estimate_currency_or_fx_unavailable"
        return result
    estimate = next_est[0]
    if _implausible_scale(currency, visible, [estimate]):
        result["blend_exclusion_reason"] = "estimate_currency_scale_implausible"
        return result
    evidence.append(
        {
            "source": "consensus",
            "fiscal_date": estimate["fiscal_date"],
            "snapshot_date": snapshot_date,
            "net_income_local": float(estimate["net_income_avg"]),
            "currency": currency,
            "usd_per_unit": rate["usd_per_unit"],
            "net_income_usd": float(estimate["net_income_avg"]) * rate["usd_per_unit"],
        }
    )
    result["blend_quarters"] = evidence
    result["blend_net_income_usd"] = sum(q["net_income_usd"] for q in evidence)
    return result


def compute_forward_basket(basket, snapshot_date, members):
    if basket not in FULL_BASKETS or not members:
        raise ValueError("configured nonempty basket required")
    ntm = compute_aggregate_basket_pe(members, income_key="ntm_net_income_usd")
    blend = compute_aggregate_basket_pe(members, income_key="blend_net_income_usd")
    ntm_pe = ntm["pe"] if ntm["weight_coverage"] >= MINIMUM_COVERAGE else None
    blend_pe = blend["pe"] if blend["weight_coverage"] >= MINIMUM_COVERAGE else None
    payload = {
        "methodology_version": METHODOLOGY,
        "earnings_basis": BASIS,
        "snapshot_date": snapshot_date,
        "members": members,
        "ntm_total_mcap": ntm["covered_market_cap"],
        "blend_total_mcap": blend["covered_market_cap"],
        "weight_coverage_ntm": ntm["weight_coverage"],
        "weight_coverage_blend": blend["weight_coverage"],
        "status": (
            "complete"
            if ntm_pe is not None and blend_pe is not None
            else "partial"
            if ntm_pe is not None
            else "unpublishable"
        ),
        "gate_reasons": [
            name
            for name, value in (("ntm", ntm_pe), ("blend", blend_pe))
            if value is None
        ],
    }
    return {
        "basket": basket,
        "snapshot_date": snapshot_date,
        "fwd_pe_ntm": ntm_pe,
        "fwd_pe_blend": blend_pe,
        "total_mcap": ntm["observed_market_cap"],
        "ntm_net_income": ntm["net_income_total"],
        "blend_net_income": blend["net_income_total"],
        "n_members": len(members),
        "n_covered_ntm": len(ntm["covered_members"]),
        "n_covered_blend": len(blend["covered_members"]),
        "mcap_coverage_ntm": ntm["mcap_coverage"],
        "mcap_coverage_blend": blend["mcap_coverage"],
        "weight_coverage": ntm["weight_coverage"],
        "members_json": payload,
    }


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def build_forward_valuations(conn, snapshot_date, config_dir=CONFIG_DIR):
    """Read one complete weekly source vintage; no source writes or requests."""
    run = conn.execute(
        "SELECT * FROM fmp_forward_runs WHERE snapshot_date=? AND run_kind='weekly'",
        [snapshot_date],
    ).fetchone()
    if not run or run["status"] != "complete":
        raise ValueError("valuation requires a complete weekly source manifest")
    universe = json.loads(run["target_universe_json"])
    if (
        not isinstance(universe, list)
        or not universe
        or not all(isinstance(s, str) and s for s in universe)
        or len(set(universe)) != len(universe)
        or len(universe) != run["target_count"]
    ):
        raise ValueError("source manifest universe is invalid")
    parse_forward_run_evidence(run["summary_json"])
    universe = set(universe)
    if snapshot_date < "2026-07-13":
        raise ValueError("PIT valuation starts at 2026-07-13")
    _, _, mags = load_basket_configs(Path(config_dir))
    corrections = load_security_source_corrections(config_dir)
    share = default_share_class_config(Path(config_dir))
    fx = {}
    for r in _rows(conn, "SELECT * FROM fx_daily WHERE date<=?", [snapshot_date]):
        fx.setdefault(r["currency"], []).append(r)
    cache, caps, sanity, splits = {}, {}, {}, {}

    def load_symbol(symbol):
        if symbol in caps:
            return
        caps[symbol] = _rows(
            conn,
            "SELECT * FROM historical_market_cap WHERE symbol=? AND date<=? ORDER BY date",
            [symbol, snapshot_date],
        )
        prices = _rows(
            conn,
            "SELECT date,close FROM daily_price WHERE symbol=? AND date<=? ORDER BY date",
            [symbol, snapshot_date],
        )
        splits[symbol] = _rows(
            conn,
            "SELECT * FROM fmp_stock_splits WHERE symbol=? AND date<=? ORDER BY date",
            [symbol, snapshot_date],
        )
        sanity[symbol] = scan_market_cap_candidates(
            caps[symbol], prices, splits[symbol]
        )

    output = []
    for basket in FULL_BASKETS:
        holdings = (
            [{"included": 1, "symbol": s, "weight_pct": 100 / len(mags)} for s in mags]
            if basket == "MAGS"
            else _rows(
                conn,
                "SELECT * FROM fmp_etf_holdings_snapshot WHERE basket=? AND snapshot_date=? ORDER BY raw_row_index",
                [basket, snapshot_date],
            )
        )
        if not holdings:
            raise ValueError(f"{basket}: holdings snapshot missing")
        if any(r.get('filter_reason') == 'reviewed_cvr' for r in holdings):
            raise ValueError('physical holdings cannot supply a reviewed correction marker')
        holdings = apply_pit_security_corrections(conn, basket, snapshot_date, holdings, corrections)
        primaries = {r.get("symbol") for r in holdings if r.get("included") == 1}
        weights = {}
        non_equity = []
        for index, row in enumerate(holdings):
            reason = row.get("filter_reason")
            if reason not in ("cash_or_fund", "swap", "futures", "reviewed_cvr"):
                reason = non_equity_holding_reason(
                    row.get("raw_asset"), row.get("name")
                )
            if reason in ("cash_or_fund", "swap", "futures", "reviewed_cvr"):
                non_equity.append(
                    {
                        "raw_asset": row.get("raw_asset"),
                        "name": row.get("name"),
                        "weight_pct": row.get("weight_pct"),
                        "source_filter_reason": row.get("filter_reason"),
                        "valuation_filter_reason": reason,
                        **({'correction_id': row['correction_id']} if row.get('correction_id') else {}),
                    }
                )
                continue
            target = (
                row.get("symbol") if row.get("included") == 1 else row.get("covered_by")
            )
            target = target or f"unmapped:{index}:{row.get('raw_asset', '')}"
            weight = _number(row.get("weight_pct"))
            if weight is None or weight < 0:
                raise ValueError(f"{basket}: invalid equity weight")
            weights[target] = weights.get(target, 0) + weight
        members = []
        for symbol, weight in sorted(weights.items()):
            if (
                symbol.startswith("unmapped:")
                or symbol not in primaries
                or symbol not in universe
            ):
                members.append(
                    {
                        "symbol": symbol,
                        "weight_pct": weight,
                        "market_cap": None,
                        "ntm_net_income_usd": None,
                        "blend_net_income_usd": None,
                        "ntm_exclusion_reason": "holding_unmapped_or_primary_outside_manifest",
                        "blend_exclusion_reason": "holding_unmapped_or_primary_outside_manifest",
                    }
                )
                continue
            load_symbol(symbol)
            for secondary in share["groups"].get(symbol, []):
                load_symbol(secondary)
            status = {r["date"]: r["status"] for r in sanity[symbol]}
            observation = select_asof_market_cap(
                caps[symbol],
                snapshot_date,
                status,
                {d for d, s in status.items() if not accepted_market_cap_status(s)},
            )
            resolved = resolve_company_market_cap(
                primary=symbol,
                base_market_cap=observation["market_cap"] if observation else None,
                secondaries=share["groups"].get(symbol, []),
                convention=share["conventions"].get(symbol),
                valuation_date=snapshot_date,
                market_cap_by_symbol=caps,
                sanity_by_symbol=sanity,
            )
            if symbol not in cache:
                cache[symbol] = compute_forward_member(
                    symbol=symbol,
                    snapshot_date=snapshot_date,
                    estimates=_rows(
                        conn,
                        "SELECT * FROM fmp_estimates WHERE symbol=? AND snapshot_date=?",
                        [symbol, snapshot_date],
                    ),
                    earnings=_rows(
                        conn, "SELECT * FROM fmp_earnings WHERE symbol=?", [symbol]
                    ),
                    income=_rows(
                        conn, "SELECT * FROM income_quarterly WHERE symbol=?", [symbol]
                    ),
                    fx=fx,
                    splits=splits[symbol],
                )
            members.append(
                {
                    **cache[symbol],
                    "weight_pct": weight,
                    "market_cap": resolved["market_cap"],
                    "market_cap_date": observation["date"] if observation else None,
                    "market_cap_exclusion_reason": resolved["exclusion_reason"]
                    or (None if observation else "missing_stale_or_quarantined"),
                    "share_class": resolved,
                }
            )
        valued = compute_forward_basket(basket, snapshot_date, members)
        valued["members_json"]["non_equity_exclusions"] = non_equity
        output.append(valued)
    return output


def verify_forward_valuations(conn, snapshot_date, rows, config_dir=CONFIG_DIR):
    """Raw-source replay plus independent quarter sums and ratio assertions."""
    from terminal.forward_source_verifier import verify_pit_security_corrections
    errors = verify_pit_security_corrections(conn, snapshot_date, rows, config_dir)
    expected = build_forward_valuations(conn, snapshot_date, config_dir)
    observed = {r["basket"]: dict(r) for r in rows}
    if len(observed) != len(rows) or set(observed) != set(FULL_BASKETS):
        errors.append("six_unique_baskets_required")
    for truth in expected:
        basket = truth["basket"]
        row = observed.get(basket)
        if row is None:
            continue
        payload = row.get("members_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = None
        if not isinstance(payload, dict) or not payload.get("members"):
            errors.append(f"{basket}:members_evidence_missing")
            continue
        row["members_json"] = payload
        if any(row.get(k) != v for k, v in truth.items()):
            errors.append(f"{basket}:raw_source_replay_mismatch")
        for kind in ("ntm", "blend"):
            covered = [
                m
                for m in payload["members"]
                if m.get("market_cap") is not None
                and m.get(f"{kind}_net_income_usd") is not None
            ]
            for member in covered:
                quarters = member.get(f"{kind}_quarters", [])
                if len(quarters) != 4 or not math.isclose(
                    sum(q["net_income_usd"] for q in quarters),
                    member[f"{kind}_net_income_usd"],
                    rel_tol=1e-9,
                ):
                    errors.append(
                        f"{basket}:{member['symbol']}:{kind}_quarter_sum_mismatch"
                    )
            pe = row.get(f"fwd_pe_{kind}")
            if pe is None:
                # NTM is the product gate; blend is an auxiliary line with its
                # own coverage. A correctly withheld blend never masquerades
                # as complete and does not suppress a valid NTM observation.
                if kind == "ntm":
                    errors.append(f"{basket}:{kind}_publication_gate_failed")
            else:
                ni = sum(m[f"{kind}_net_income_usd"] for m in covered)
                if ni <= 0 or not math.isclose(
                    pe, sum(m["market_cap"] for m in covered) / ni, rel_tol=1e-9
                ):
                    errors.append(f"{basket}:{kind}_ratio_mismatch")
    return errors
