"""Weekly entrant identity bootstrap (T17, R2-P1-2 / R3-P1-2).

Every weekly screener refresh turns up symbols `security_master` (SM) has
never seen. This module resolves their identity BEFORE the membership
snapshot is committed, so a name can only enter Extended membership as a
verified, eligible security.

It owns no identity policy of its own: the per-symbol kernel
(`resolve_identity_for_symbol`) and the cross-symbol share-class settlement
(`resolve_share_classes`) are T6's, imported unchanged, so the frozen
Identity 状态契约 (table in `scripts/bootstrap_security_master.py`) holds
identically at cold start and on every weekly increment.

Two consequences worth stating out loud:

  - A symbol whose profile fetch FAILED gets no SM row at all. It stays in
    the coverage(identity) repair queue for reconcile phase 0 (T12) to
    retry and simply misses this week's membership — never a permanent
    `missing_profile` tombstone.
  - Share-class settlement runs over the entrants PLUS every already-
    mastered symbol sharing their CIK. Grouping the weekly batch alone
    would mint a second eligible primary for a company already in SM
    (double-counted membership) the week a new share class lists.
    Re-grading incumbents already blocked as `identity_conflict` is
    deliberately NOT done here — that is reconcile's (T12) job. An
    incumbent is only ever demoted on evidence: when it carries no
    market-cap data of its own the settlement is declined outright
    (`_decline_metricless_demotions`), and any demotion that does happen
    is logged as a warning, since the weekly caller keeps no hold of the
    summary dict.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.data.security_master import SecurityRecord, resolve_share_classes

# T6 kernel + its I/O helpers, reused verbatim (never reimplemented — the
# Identity 状态契约 must have exactly one implementation).
from scripts.bootstrap_security_master import (  # noqa: E402
    _load_overrides,
    _now_iso,
    _write_company_profiles,
    resolve_identity_for_symbol,
)

logger = logging.getLogger(__name__)

# SQLite hard-caps bound parameters; chunk any IN (...) built from a symbol
# or CIK list (same discipline as market_store's membership UPDATE batching).
_SQL_PARAM_CHUNK = 500


def _chunked_in_query(conn, sql_prefix: str, values: List[str]) -> List:
    rows: List = []
    for i in range(0, len(values), _SQL_PARAM_CHUNK):
        chunk = values[i:i + _SQL_PARAM_CHUNK]
        placeholders = ", ".join(["?"] * len(chunk))
        rows.extend(conn.execute(sql_prefix + " (" + placeholders + ")", chunk).fetchall())
    return rows


def _incumbents_sharing_cik(
    store, entrant_records: List[SecurityRecord], entrant_symbols: set
) -> Tuple[List[SecurityRecord], Dict[str, dict]]:
    """Already-mastered SM rows whose CIK an entrant claims, + their profiles.

    Returned records are rebuilt in `SecurityRecord` shape so they can join
    the entrants in one `resolve_share_classes` call; their profiles come
    from `company_profile` because primary selection compares mktCap/volAvg.
    """
    ciks = sorted({r.cik for r in entrant_records if r.reason == "ok" and r.cik})
    if not ciks:
        return [], {}

    conn = store._get_conn()
    rows = _chunked_in_query(
        conn,
        "SELECT symbol, cik, company_name, exchange, is_etf, is_fund, is_adr, "
        "share_class_of, eligible, reason FROM security_master WHERE cik IN",
        ciks,
    )

    records = [
        SecurityRecord(
            symbol=r["symbol"], cik=r["cik"], company_name=r["company_name"],
            exchange=r["exchange"], is_etf=bool(r["is_etf"]), is_fund=bool(r["is_fund"]),
            is_adr=bool(r["is_adr"]), share_class_of=r["share_class_of"],
            eligible=bool(r["eligible"]), reason=r["reason"],
        )
        for r in rows if r["symbol"] not in entrant_symbols
    ]
    if not records:
        return [], {}

    profile_rows = _chunked_in_query(
        conn,
        "SELECT symbol, payload FROM company_profile WHERE symbol IN",
        [r.symbol for r in records],
    )
    profiles: Dict[str, dict] = {}
    for r in profile_rows:
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError):
            logger.warning("company_profile payload unreadable for %s — "
                           "share-class metrics degrade to volAvg/needs_review", r["symbol"])
            continue
        if isinstance(payload, dict):
            profiles[r["symbol"]] = payload
    return records, profiles


def _has_size_metric(profile: Optional[dict]) -> bool:
    """Does this profile carry the value share-class settlement decides on?"""
    if not profile:
        return False
    for key in ("mktCap", "marketCap"):
        if profile.get(key) is not None:
            return True
    return False


def _decline_metricless_demotions(
    resolved: List[SecurityRecord],
    prior_by_symbol: Dict[str, SecurityRecord],
    entrant_symbols: set,
    profiles: Dict[str, dict],
) -> List[SecurityRecord]:
    """Never let the ABSENCE of data demote an established primary.

    `resolve_share_classes` picks the primary by mktCap, so an incumbent whose
    `company_profile` row is missing (or carries no market cap) contributes no
    value at all and the entrant wins the group uncontested — the incumbent
    would lose its identity to a missing row rather than to evidence.

    For those groups the verdict is declined: incumbents keep their existing
    SM rows and the group's entrants are parked as `needs_review_primary`.
    That is `resolve_share_classes`'s own no-data-no-verdict outcome ("if
    everything is missing/tied throughout, needs_review_primary ... no auto
    pick"), so the entrant stays out of membership and surfaces in
    `get_needs_review_symbols()` until reconcile or a Boss override settles it.

    `identity_conflict` verdicts are deliberately NOT declined: they are
    decided on company_name, which every SM row carries, so no metric is
    missing — and blocking the whole group is already the safe outcome.
    """
    by_symbol = {rec.symbol: rec for rec in resolved}

    groups: Dict[str, List[SecurityRecord]] = {}
    for rec in resolved:
        if rec.cik:
            groups.setdefault(rec.cik, []).append(rec)

    for cik, members in groups.items():
        member_symbols = {m.symbol for m in members}
        entrants_here = sorted(member_symbols & entrant_symbols)
        incumbents_here = sorted(member_symbols - entrant_symbols)
        if not entrants_here or not incumbents_here:
            continue  # nothing established to protect / nothing new to weigh
        if any(m.reason == "identity_conflict" for m in members):
            continue  # name-based verdict, not a metrics one

        blind_losers = sorted(
            m.symbol for m in members
            if m.symbol in prior_by_symbol
            and prior_by_symbol[m.symbol].eligible and not m.eligible
            and not _has_size_metric(profiles.get(m.symbol))
        )
        if not blind_losers:
            continue

        for m in members:
            if m.symbol in entrant_symbols:
                by_symbol[m.symbol] = replace(
                    m, eligible=False, reason="needs_review_primary", share_class_of=None
                )
            elif m.symbol in prior_by_symbol:
                by_symbol[m.symbol] = prior_by_symbol[m.symbol]

        logger.warning(
            "share-class settlement DECLINED for CIK %s: incumbent(s) %s carry no "
            "market-cap data, so the demotion would rest on an absent row, not on "
            "evidence. Incumbent identity kept as-is; entrant(s) %s parked as "
            "needs_review_primary.",
            cik, ", ".join(blind_losers), ", ".join(entrants_here),
        )

    return [by_symbol[rec.symbol] for rec in resolved]


def resolve_share_classes_with_incumbents(
    candidate_records: List[SecurityRecord],
    candidate_profiles: Dict[str, dict],
    *,
    store,
    overrides: Optional[Dict[str, str]] = None,
) -> Tuple[List[SecurityRecord], Dict[str, SecurityRecord]]:
    """Settle a fetched identity batch over complete same-CIK groups.

    A retry/rebootstrap batch is not necessarily identity-complete: one share
    class can fetch while its sibling fails. Pull existing SM incumbents that
    share a candidate CIK into the decision so a partial batch can never mint
    a second eligible primary. Returns the resolved closed group plus the
    incumbent rows as they existed before settlement (used for change logs).
    """
    overrides = overrides or {}
    candidate_symbols = {record.symbol for record in candidate_records}
    incumbents, incumbent_profiles = _incumbents_sharing_cik(
        store, candidate_records, candidate_symbols
    )
    members_by_cik: Dict[str, set] = {}
    for record in candidate_records + incumbents:
        if record.cik:
            members_by_cik.setdefault(record.cik, set()).add(record.symbol)
    needs_review_ciks = {
        record.cik for record in incumbents
        if record.cik and record.reason == "needs_review_primary"
    }
    valid_override_ciks = {
        cik for cik in needs_review_ciks
        if overrides.get(cik) in members_by_cik.get(cik, set())
    }

    # A valid human override is allowed to reopen a parked group for
    # deterministic settlement. Without one, needs_review incumbents remain a
    # hard gate and all new members of that CIK are parked too.
    incumbents_for_resolution = [
        replace(record, eligible=True, reason="ok")
        if (record.cik in valid_override_ciks
            and record.reason == "needs_review_primary")
        else record
        for record in incumbents
    ]
    grouping_profiles = dict(incumbent_profiles)
    grouping_profiles.update(candidate_profiles)
    resolved = resolve_share_classes(
        candidate_records + incumbents_for_resolution, overrides, grouping_profiles
    )
    prior_by_symbol = {record.symbol: record for record in incumbents}
    resolved = _decline_metricless_demotions(
        resolved, prior_by_symbol, candidate_symbols, grouping_profiles
    )
    unresolved_review_ciks = needs_review_ciks - valid_override_ciks
    if unresolved_review_ciks:
        by_symbol = {record.symbol: record for record in resolved}
        for record in resolved:
            if record.cik not in unresolved_review_ciks:
                continue
            if record.symbol in candidate_symbols:
                by_symbol[record.symbol] = replace(
                    record, eligible=False, reason="needs_review_primary",
                    share_class_of=None)
            elif record.symbol in prior_by_symbol:
                by_symbol[record.symbol] = prior_by_symbol[record.symbol]
        for cik in sorted(unresolved_review_ciks):
            entrants = sorted(
                record.symbol for record in candidate_records if record.cik == cik)
            logger.warning(
                "share-class settlement PARKED for CIK %s: existing "
                "needs_review_primary group requires a valid override before "
                "entrant(s) %s may become eligible",
                cik, ", ".join(entrants),
            )
        resolved = [by_symbol[record.symbol] for record in resolved]
    return resolved, prior_by_symbol


def bootstrap_entrants(
    symbols: Iterable[str],
    *,
    client,
    store,
    overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve identity for symbols SM has never seen. Rate limiting is the
    client's (one `get_dataset_with_status("profile", ...)` call per symbol).

    Returns a summary dict:
        requested     — how many distinct symbols were processed
        eligible      — entrants now eligible (may enter membership)
        blocked       — {entrant: reason} written to SM but not eligible
        fetch_failed  — no SM row; queued in coverage(identity) for retry
        reclassified  — INCUMBENT symbols whose SM row this batch rewrote
                        (share-class demotion/conflict), i.e. names whose
                        eligibility may have changed without being entrants
    """
    requested = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    summary: Dict[str, Any] = {
        "requested": len(requested),
        "eligible": [],
        "blocked": {},
        "fetch_failed": [],
        "reclassified": [],
    }
    if not requested:
        return summary

    if overrides is None:
        overrides = _load_overrides()

    entrant_records: List[SecurityRecord] = []
    entrant_profiles: Dict[str, dict] = {}
    for symbol in requested:
        outcome = resolve_identity_for_symbol(symbol, client=client, store=store)
        status = outcome["fetch_status"]
        if status == "fetch_failed":
            summary["fetch_failed"].append(symbol)
        elif status == "provider_empty":
            # kernel already wrote SM reason=missing_profile + coverage provider_empty
            summary["blocked"][symbol] = "missing_profile"
        else:
            entrant_records.append(outcome["record"])
            entrant_profiles[symbol] = outcome["profile"]

    if not entrant_records:
        logger.info("entrant bootstrap: %d symbols, none resolvable "
                    "(fetch_failed=%d, provider_empty=%d)",
                    len(requested), len(summary["fetch_failed"]), len(summary["blocked"]))
        return summary

    entrant_symbols = {r.symbol for r in entrant_records}
    resolved, prior_by_symbol = resolve_share_classes_with_incumbents(
        entrant_records, entrant_profiles, store=store, overrides=overrides
    )

    now_iso = _now_iso()
    sm_rows: List[Dict[str, Any]] = []
    demotions: List[Tuple[str, str, str]] = []
    for rec in resolved:
        prior = prior_by_symbol.get(rec.symbol)
        if prior is not None:
            if rec == prior:
                continue  # incumbent unaffected by this batch — leave its row alone
            summary["reclassified"].append(rec.symbol)
            if prior.eligible and not rec.eligible:
                demotions.append((rec.symbol, prior.reason, rec.reason))
        sm_rows.append({**asdict(rec), "updated_at": now_iso})

    if sm_rows:
        store.upsert_security_master(sm_rows)

    if demotions:
        # The weekly cron keeps no hold of the summary dict, so an established
        # member dropping out of the eligible universe has to be audible here.
        detail = ", ".join([s + " (" + old + " -> " + new + ")"
                            for s, old, new in sorted(demotions)])
        logger.warning(
            "entrant bootstrap demoted %d established member(s) OUT of the eligible "
            "universe: %s — they exit extended_membership at this week's snapshot",
            len(demotions), detail,
        )

    conflicts = sorted(rec.symbol for rec in resolved if rec.reason == "identity_conflict")
    if conflicts:
        store.upsert_coverage_status([
            {"symbol": s, "dataset": "identity", "status": "identity_blocked",
             "detail": "same CIK, different company name across share classes",
             "updated_at": now_iso}
            for s in conflicts
        ])

    if entrant_profiles:
        _write_company_profiles(store, entrant_profiles, now_iso)

    for rec in resolved:
        if rec.symbol not in entrant_symbols:
            continue
        if rec.eligible:
            summary["eligible"].append(rec.symbol)
        else:
            summary["blocked"][rec.symbol] = rec.reason

    summary["eligible"].sort()
    summary["reclassified"].sort()
    logger.info("entrant bootstrap: %d symbols -> eligible=%d blocked=%d "
                "fetch_failed=%d reclassified=%d",
                len(requested), len(summary["eligible"]), len(summary["blocked"]),
                len(summary["fetch_failed"]), len(summary["reclassified"]))
    return summary
