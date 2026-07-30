"""FMP forward EPS 纯转换层：holdings/estimates/earnings 规范化 + universe resolver。

本模块绝不调用 API 或 DB（纯函数，全部可单测）。
Spec: docs/design/2026-07-09-fmp-forward-eps-valuation-spec.md §5.3 / §5.4 / §7.1
"""
import calendar as calendar_module
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# API-to-basket 映射（SOX 篮子刻意用 SOXX holdings 代理；MAGS 静态清单无 holdings）
ETF_HOLDING_SOURCES = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "SOX": "SOXX",
    "IGV": "IGV",
    "XLF": "XLF",
}

# 现金/货基识别：已知代码 + 名称标记（保守启发式；Task 11 live audit 全量复核）
_KNOWN_CASH_ASSETS = {"XTSLA"}
_CASH_NAME_RE = re.compile(
    r"\b(CASH|CSH FND|MONEY MARKET|TREASURY (?:SL )?(?:FND|FUND|AGENCY))\b",
    re.IGNORECASE)
_SWAP_NAME_RE = re.compile(r"\b(SWAP|TRS)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Run evidence schema
# ---------------------------------------------------------------------------

def parse_forward_run_evidence(raw: Any) -> Dict[str, Any]:
    """Parse and validate the persisted run-wide evidence envelope.

    Resume and verification both depend on this payload to remember unresolved
    symbols.  Treating a missing field as an empty collection would therefore
    turn corrupt evidence into a false success, so the schema is intentionally
    strict and fail-closed.
    """
    if raw is None or raw == "":
        raise ValueError("evidence missing")
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence JSON unparseable") from exc
    else:
        payload = raw

    if not isinstance(payload, dict):
        raise ValueError("evidence must be a JSON object")
    run_state = payload.get("run_state")
    if not isinstance(run_state, dict):
        raise ValueError("run_state must be an object")

    for field_name in ("quarter_empty", "earnings_failed"):
        symbols = run_state.get(field_name)
        if not isinstance(symbols, list):
            raise ValueError(f"run_state.{field_name} must be a list")
        if any(not isinstance(symbol, str) or not symbol.strip()
               for symbol in symbols):
            raise ValueError(
                f"run_state.{field_name} must contain non-empty strings")
        if len({symbol.upper() for symbol in symbols}) != len(symbols):
            raise ValueError(f"run_state.{field_name} contains duplicates")

    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("attempts must be a list")
    if any(not isinstance(attempt, dict) for attempt in attempts):
        raise ValueError("attempts must contain objects")

    errors = payload.get("errors")
    if errors is not None:
        if not isinstance(errors, list):
            raise ValueError("errors must be a list when present")
        if any(not isinstance(error, dict) for error in errors):
            raise ValueError("errors must contain objects")

    return payload


# ---------------------------------------------------------------------------
# 配置加载 + fail-fast 校验
# ---------------------------------------------------------------------------

def validate_listing_overrides(overrides: Mapping[str, str]) -> None:
    """外股 → US ticker 映射校验：目标必须是大写无点号 US ticker。"""
    for foreign, us in overrides.items():
        if not us or us != us.upper() or "." in us:
            raise ValueError(
                f"listing_overrides target must be uppercase US ticker: "
                f"{foreign!r} -> {us!r}")
        if foreign == us:
            raise ValueError(f"listing_overrides identity mapping: {foreign!r}")


def validate_share_class_groups(groups: Mapping[str, Sequence[str]]) -> None:
    """双股权组校验：副类股全局唯一、主类股不得同时是副类股、全部大写。"""
    primaries = set(groups.keys())
    seen_secondaries: set = set()
    for primary, secondaries in groups.items():
        if primary != primary.upper():
            raise ValueError(f"share_class primary must be uppercase: {primary!r}")
        for sec in secondaries:
            if sec != sec.upper():
                raise ValueError(f"share_class secondary must be uppercase: {sec!r}")
            if sec in seen_secondaries:
                raise ValueError(f"duplicate secondary share class: {sec!r}")
            if sec == primary:
                raise ValueError(f"secondary equals primary: {sec!r}")
            seen_secondaries.add(sec)
    overlap = primaries & seen_secondaries
    if overlap:
        raise ValueError(f"primary also listed as secondary: {sorted(overlap)}")


def validate_mags_members(config: Mapping[str, Any]) -> List[str]:
    if config.get("basket") != "MAGS":
        raise ValueError(f"mags config basket must be 'MAGS': {config.get('basket')!r}")
    symbols = config.get("symbols") or []
    if len(symbols) != len(set(symbols)) or not symbols:
        raise ValueError("mags symbols must be nonempty and unique")
    for s in symbols:
        if s != s.upper():
            raise ValueError(f"mags symbol must be uppercase: {s!r}")
    return list(symbols)


def load_basket_configs(config_dir: Path) -> Tuple[Dict[str, str],
                                                   Dict[str, List[str]],
                                                   List[str]]:
    """读取并校验 3 个 basket 配置。任何一项不合法直接抛 ValueError。"""
    config_dir = Path(config_dir)
    with open(config_dir / "listing_overrides.json", encoding="utf-8") as f:
        listing = json.load(f)
    with open(config_dir / "share_class_groups.json", encoding="utf-8") as f:
        groups = json.load(f)
    with open(config_dir / "mags_members.json", encoding="utf-8") as f:
        mags_config = json.load(f)
    validate_listing_overrides(listing)
    validate_share_class_groups(groups)
    mags = validate_mags_members(mags_config)
    return listing, groups, mags


# ---------------------------------------------------------------------------
# Three-index (SPY/QQQ/SOXX) historical basket disclosure config
# ---------------------------------------------------------------------------

def validate_index_pe_basket_entry(
    basket_symbol: str, entry: Mapping[str, Any],
) -> None:
    """Fail-closed schema check for one basket entry in index_pe_baskets.json."""
    if basket_symbol != basket_symbol.upper():
        raise ValueError(f"basket key must be uppercase: {basket_symbol!r}")
    source_basket = entry.get("source_basket")
    if (not isinstance(source_basket, str) or not source_basket
            or source_basket != source_basket.upper()):
        raise ValueError(
            f"{basket_symbol}: source_basket must be a nonempty uppercase string")
    display_order = entry.get("display_order")
    if (not isinstance(display_order, int) or isinstance(display_order, bool)
            or display_order < 1):
        raise ValueError(f"{basket_symbol}: display_order must be a positive int")
    window_years = entry.get("five_year_window_years")
    if window_years != 5:
        raise ValueError(
            f"{basket_symbol}: five_year_window_years must be 5 (frozen scope)")
    months = entry.get("rebalance_months")
    if (not isinstance(months, list) or not months
            or any(not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= 12
                   for m in months)
            or len(set(months)) != len(months)):
        raise ValueError(
            f"{basket_symbol}: rebalance_months must be unique ints in 1-12")
    recon_month = entry.get("expected_reconstitution_month")
    if recon_month is not None and (
            not isinstance(recon_month, int) or isinstance(recon_month, bool)
            or recon_month not in months):
        raise ValueError(
            f"{basket_symbol}: expected_reconstitution_month must be null "
            "or one of rebalance_months")
    staleness = entry.get("market_cap_staleness_days")
    if (not isinstance(staleness, int) or isinstance(staleness, bool)
            or staleness <= 0):
        raise ValueError(
            f"{basket_symbol}: market_cap_staleness_days must be a positive int")
    quality = entry.get("snapshot_quality")
    if not isinstance(quality, Mapping):
        raise ValueError(f"{basket_symbol}: snapshot_quality must be an object")
    min_members, max_members = quality.get("minimum_members"), quality.get("maximum_members")
    min_weight, max_weight = quality.get("minimum_weight"), quality.get("maximum_weight")
    if (not isinstance(min_members, int) or isinstance(min_members, bool)
            or not isinstance(max_members, int) or isinstance(max_members, bool)
            or not 0 < min_members <= max_members):
        raise ValueError(
            f"{basket_symbol}: snapshot_quality member bounds must satisfy "
            "0 < minimum_members <= maximum_members")
    if (not isinstance(min_weight, (int, float)) or isinstance(min_weight, bool)
            or not isinstance(max_weight, (int, float)) or isinstance(max_weight, bool)
            or not 0 < min_weight <= max_weight):
        raise ValueError(
            f"{basket_symbol}: snapshot_quality weight bounds must satisfy "
            "0 < minimum_weight <= maximum_weight")
    gap_start = entry.get("history_available_from")
    if gap_start is not None:
        try:
            date.fromisoformat(gap_start)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{basket_symbol}: history_available_from must be null or "
                "an ISO date") from exc


def load_index_pe_basket_configs(config_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Load + validate config/baskets/index_pe_baskets.json.

    SSOT for the SPY/QQQ/SOXX historical disclosure pipeline: each basket's
    FMP-forward source identity (`source_basket`, must agree with
    `ETF_HOLDING_SOURCES`), the frozen five-year window, its quarterly
    rebalance-close schedule and (basket-specific, possibly absent) expected
    reconstitution month, disclosure snapshot coverage/staleness bounds, the
    morning-report display order, and any audited pre-history gap boundary.
    """
    path = Path(config_dir) / "index_pe_baskets.json"
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("index PE basket config must be a nonempty object")
    seen_source_baskets: Dict[str, str] = {}
    seen_display_orders: Dict[int, str] = {}
    for basket_symbol, entry in payload.items():
        if not isinstance(entry, Mapping):
            raise ValueError(f"{basket_symbol}: basket config entry must be an object")
        validate_index_pe_basket_entry(basket_symbol, entry)
        source_basket = entry["source_basket"]
        if source_basket in seen_source_baskets:
            raise ValueError(
                f"duplicate source_basket {source_basket!r}: "
                f"{seen_source_baskets[source_basket]} and {basket_symbol}")
        seen_source_baskets[source_basket] = basket_symbol
        display_order = entry["display_order"]
        if display_order in seen_display_orders:
            raise ValueError(
                f"duplicate display_order {display_order!r}: "
                f"{seen_display_orders[display_order]} and {basket_symbol}")
        seen_display_orders[display_order] = basket_symbol
    return {symbol: dict(entry) for symbol, entry in payload.items()}


def basket_history_gap(
    basket_symbol: str, reference_date: str,
    basket_configs: Mapping[str, Mapping[str, Any]],
) -> bool:
    """True if reference_date predates this basket's audited disclosure history.

    A basket with no declared `history_available_from` boundary has no known
    gap *yet* -- that is an empirical finding to make against real vendor
    responses (see SOXX's 2021-09-01 boundary, established in
    docs/plans/2026-07-14-soxx-historical-ttm-pe.md), not an assumption to
    make on baskets that have not been audited.
    """
    symbol = basket_symbol.upper()
    if symbol not in basket_configs:
        raise ValueError(f"unknown basket: {basket_symbol}")
    gap_start = basket_configs[symbol].get("history_available_from")
    if gap_start is None:
        return False
    return date.fromisoformat(reference_date) < date.fromisoformat(gap_start)


# ---------------------------------------------------------------------------
# Holdings 规范化
# ---------------------------------------------------------------------------

def normalize_holdings(
    basket: str,
    snapshot_date: str,
    raw_rows: Sequence[Mapping[str, Any]],
    listing_overrides: Mapping[str, str],
    share_class_groups: Mapping[str, Sequence[str]],
) -> List[Dict[str, Any]]:
    """每一原始输入行恰好产出一行审计记录；绝不调用 API 或 DB。

    分类顺序冻结（plan Task 4）：
    1. cash/fund → 2. swap/TRS → 3. 外股映射/排除 → 4. 副类股 → 5. 正常股票。
    未知空行 fail-closed：included=0, filter_reason='unrecognized_asset'。
    """
    secondary_to_primary: Dict[str, str] = {}
    for primary, secondaries in share_class_groups.items():
        for sec in secondaries:
            secondary_to_primary[sec] = primary
    # 双股权词表内的点号 ticker（如 HEI.A）是美股类别股，不是外市代码
    class_vocabulary = set(share_class_groups.keys()) | set(secondary_to_primary)

    out: List[Dict[str, Any]] = []
    for idx, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            raw = {}  # 坏行照样留档一行（fail-closed unrecognized_asset）
        asset = str(raw.get("asset") or "").strip().upper()
        name = str(raw.get("name") or "").strip()

        symbol = None
        included = 0
        filter_reason = None
        covered_by = None

        if asset in _KNOWN_CASH_ASSETS or _CASH_NAME_RE.search(name):
            filter_reason = "cash_or_fund"
        elif _SWAP_NAME_RE.search(name):
            filter_reason = "swap"
        elif "." in asset and asset not in class_vocabulary:
            mapped = listing_overrides.get(asset)
            if mapped:
                symbol = mapped
            else:
                filter_reason = "foreign_listing_unmapped"
        elif asset:
            symbol = asset

        if filter_reason is None and symbol is not None:
            if symbol in secondary_to_primary:
                filter_reason = "dual_class_secondary"
                covered_by = secondary_to_primary[symbol]
                symbol = None
            else:
                included = 1
        elif filter_reason is None:
            # 空 asset 且无任何已知标记 → fail-closed 排除并留审计
            filter_reason = "unrecognized_asset"

        if included == 0 and filter_reason not in ("dual_class_secondary",):
            symbol = None if filter_reason else symbol

        out.append({
            "basket": basket.upper(),
            "snapshot_date": snapshot_date,
            "raw_row_index": idx,
            "raw_asset": str(raw.get("asset") or ""),
            "symbol": symbol,
            "name": name or None,
            "weight_pct": raw.get("weightPercentage"),
            "market_value": raw.get("marketValue"),
            "updated_at": raw.get("updatedAt"),
            "included": included,
            "filter_reason": filter_reason,
            "covered_by": covered_by,
        })
    return out


# ---------------------------------------------------------------------------
# Historical fund disclosure adapter + SOXX calendar semantics
# ---------------------------------------------------------------------------

_SOXX_REBALANCE_MONTHS = (3, 6, 9, 12)


def _third_friday(year: int, month: int) -> date:
    fridays = [week[calendar_module.FRIDAY]
               for week in calendar_module.monthcalendar(year, month)
               if week[calendar_module.FRIDAY]]
    return date(year, month, fridays[2])


def infer_basket_rebalance_close(
    reference_date: str, rebalance_months: Sequence[int] = _SOXX_REBALANCE_MONTHS,
) -> str:
    """Most recent scheduled quarterly third-Friday close on/before reference_date.

    `rebalance_months` is basket-configured (see `load_index_pe_basket_configs`)
    rather than hardcoded, because published quarterly weight-adjustment /
    reconstitution effective dates are set by each index's own methodology,
    not by SOXX's.
    """
    reference = date.fromisoformat(reference_date)
    months = sorted(set(rebalance_months))
    if not months:
        raise ValueError("rebalance_months must be nonempty")
    candidates = [
        _third_friday(year, month)
        for year in (reference.year - 1, reference.year)
        for month in months
    ]
    eligible = [candidate for candidate in candidates if candidate <= reference]
    if not eligible:  # pragma: no cover - previous year always supplies one
        raise ValueError("no rebalance close on or before reference date")
    return max(eligible).isoformat()


def infer_soxx_rebalance_close(reference_date: str) -> str:
    """Backward-compatible SOXX wrapper; delegates to the general basket helper."""
    return infer_basket_rebalance_close(reference_date, _SOXX_REBALANCE_MONTHS)


def _normalized_trading_dates(trading_dates: Iterable[str]) -> List[date]:
    try:
        normalized = sorted({date.fromisoformat(value) for value in trading_dates})
    except (TypeError, ValueError) as exc:
        raise ValueError("trading calendar contains a non-ISO date") from exc
    if not normalized:
        raise ValueError("trading calendar is empty")
    return normalized


def next_trading_date(after_date: str, trading_dates: Iterable[str]) -> str:
    anchor = date.fromisoformat(after_date)
    later = [value for value in _normalized_trading_dates(trading_dates)
             if value > anchor]
    if not later:
        raise ValueError(f"no trading date after {after_date}")
    return min(later).isoformat()


def anchor_trading_date(holding_date: str, trading_dates: Iterable[str]) -> str:
    """Map a vendor holding date to the latest SOXX trading date on/before it."""
    anchor = date.fromisoformat(holding_date)
    earlier = [value for value in _normalized_trading_dates(trading_dates)
               if value <= anchor]
    if not earlier:
        raise ValueError(f"no trading date on or before {holding_date}")
    return max(earlier).isoformat()


def load_soxx_symbol_aliases(path: Path) -> Dict[str, Dict[str, str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SOXX aliases must be an object")
    normalized: Dict[str, Dict[str, str]] = {}
    for raw_symbol, item in payload.items():
        if (not isinstance(raw_symbol, str) or raw_symbol != raw_symbol.upper()
                or not isinstance(item, dict)):
            raise ValueError("SOXX alias keys must be uppercase symbols")
        target = item.get("symbol")
        cik = item.get("cik")
        reason = item.get("reason")
        mode = item.get("mode", "fallback")
        cusip = item.get("cusip")
        isin = item.get("isin")
        if (not isinstance(target, str) or target != target.upper()
                or not isinstance(cik, str) or not cik
                or not isinstance(reason, str) or not reason.strip()
                or mode not in {"fallback", "authoritative"}):
            raise ValueError(f"invalid SOXX alias: {raw_symbol}")
        if mode == "authoritative" and (
                not isinstance(cusip, str) or not cusip
                or not isinstance(isin, str) or not isin):
            raise ValueError(
                f"authoritative SOXX alias requires CUSIP and ISIN: {raw_symbol}")
        normalized[raw_symbol] = {
            "symbol": target, "cik": cik, "mode": mode,
            "reason": reason.strip(),
            **({"cusip": cusip} if cusip else {}),
            **({"isin": isin} if isin else {}),
        }
    return normalized


def resolve_disclosure_symbol(
    raw_symbol: str,
    cik: Optional[str],
    aliases: Mapping[str, Mapping[str, str]],
    *,
    cusip: Optional[str] = None,
    isin: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, str]]]:
    """Resolve only exact, CIK-backed corporate aliases; never fuzzy-match."""
    symbol = str(raw_symbol or "").strip().upper()
    alias = aliases.get(symbol)
    if alias is None:
        return symbol, None
    if str(cik or "") != str(alias.get("cik") or ""):
        raise ValueError(f"CIK mismatch for configured alias {symbol}")
    if alias.get("cusip") and str(cusip or "") != str(alias["cusip"]):
        raise ValueError(f"CUSIP mismatch for configured alias {symbol}")
    if alias.get("isin") and str(isin or "") != str(alias["isin"]):
        raise ValueError(f"ISIN mismatch for configured alias {symbol}")
    target = str(alias["symbol"]).upper()
    return target, {
        "raw_symbol": symbol,
        "symbol": target,
        "cik": str(cik),
        "mode": str(alias.get("mode", "fallback")),
        "reason": str(alias["reason"]),
    }


def _vendor_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} missing or malformed")
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} missing or malformed") from exc


def normalize_fund_disclosure_snapshot(
    basket_symbol: str,
    raw_rows: Sequence[Mapping[str, Any]],
    source_kind: str,
    fetched_at: str,
    trading_dates: Iterable[str],
    listing_overrides: Mapping[str, str],
    share_class_groups: Mapping[str, Sequence[str]],
    symbol_aliases: Mapping[str, Mapping[str, str]],
    previous_symbols: Optional[Iterable[str]] = None,
    *,
    rebalance_months: Sequence[int] = _SOXX_REBALANCE_MONTHS,
    expected_reconstitution_month: Optional[int] = 9,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Adapt disclosure/live rows, then reuse the proven holdings normalizer.

    `rebalance_months` / `expected_reconstitution_month` default to SOXX's
    already-audited values so every existing SOXX call site (production
    script and tests) is unaffected; other baskets pass their own values
    from `load_index_pe_basket_configs`. `expected_reconstitution_month =
    None` means the basket has no documented month where membership changes
    are the expected/scheduled outcome, so every observed delta warns.
    """
    if source_kind not in {"disclosure", "live"}:
        raise ValueError("source_kind must be disclosure or live")
    if not raw_rows:
        raise ValueError("fund snapshot cannot be empty")
    fetched = _vendor_timestamp(fetched_at, "fetched_at")
    warnings: List[str] = []
    accepted_values: List[datetime] = []
    holding_dates = set()
    adapted: List[Dict[str, Any]] = []
    identity: List[Dict[str, Any]] = []

    for raw_value in raw_rows:
        if not isinstance(raw_value, Mapping):
            raise ValueError("fund snapshot rows must be objects")
        raw = dict(raw_value)
        if source_kind == "disclosure":
            raw_date = _parse_iso_date(raw.get("date"))
            if raw_date is None:
                raise ValueError("disclosure row date missing or malformed")
            holding_dates.add(raw_date.isoformat())
            accepted = _vendor_timestamp(
                raw.get("acceptedDate"), "acceptedDate")
            accepted_values.append(accepted)
            raw_symbol = str(raw.get("symbol") or "").strip().upper()
            _, alias_evidence = resolve_disclosure_symbol(
                raw_symbol, raw.get("cik"), symbol_aliases,
                cusip=raw.get("cusip"), isin=raw.get("isin"))
            adapted.append({
                # Raw-first contract: an alias is evidence for a later
                # source-data fallback, never an unconditional ticker rewrite.
                "asset": raw_symbol,
                # Disclosure `name` can be only the issuer (for example
                # "BlackRock Funds III") while `title` carries the security
                # identity ("BlackRock Cash Funds..."). Prefer title so the
                # existing cash/fund rule can fail closed on BISXX/STIV rows.
                "name": raw.get("title") or raw.get("name"),
                "weightPercentage": raw.get("pctVal"),
                "marketValue": raw.get("valUsd"),
                "updatedAt": raw.get("acceptedDate"),
            })
            identity.append({
                "raw_symbol": raw_symbol,
                "cik": raw.get("cik"),
                "cusip": raw.get("cusip"),
                "isin": raw.get("isin"),
                "row_accepted_at": raw.get("acceptedDate"),
                "alias_symbol": (
                    alias_evidence["symbol"] if alias_evidence else None),
                "alias_mode": (
                    alias_evidence["mode"] if alias_evidence else None),
                "alias_reason": (
                    alias_evidence["reason"] if alias_evidence else None),
            })
        else:
            raw_symbol = str(raw.get("asset") or "").strip().upper()
            _, alias_evidence = resolve_disclosure_symbol(
                raw_symbol, raw.get("cik"), symbol_aliases,
                cusip=raw.get("cusip"), isin=raw.get("isin"))
            adapted.append(dict(raw))
            identity.append({
                "raw_symbol": raw_symbol,
                "cik": raw.get("cik"),
                "cusip": raw.get("cusip"),
                "isin": raw.get("isin"),
                "row_accepted_at": None,
                "alias_symbol": (
                    alias_evidence["symbol"] if alias_evidence else None),
                "alias_mode": (
                    alias_evidence["mode"] if alias_evidence else None),
                "alias_reason": (
                    alias_evidence["reason"] if alias_evidence else None),
            })

    if source_kind == "disclosure":
        if len(holding_dates) != 1:
            raise ValueError("disclosure snapshot contains mixed holding dates")
        holding_date = next(iter(holding_dates))
        accepted_text = {value.isoformat(sep=" ") for value in accepted_values}
        if len(accepted_text) > 1:
            warnings.append("inconsistent_row_accepted_at")
        composition_available_date = max(accepted_values).date().isoformat()
        weight_basis = "fixed_rebalance_weight_proxy"
        quality = "historical_disclosure_fixed_proxy"
    else:
        holding_date = fetched.date().isoformat()
        composition_available_date = holding_date
        weight_basis = "live_snapshot_backcast_proxy"
        quality = "live_tail_weaker"

    rebalance_close_date = infer_basket_rebalance_close(
        holding_date, rebalance_months)
    composition_effective_date = next_trading_date(
        rebalance_close_date, trading_dates)
    anchor_date = anchor_trading_date(holding_date, trading_dates)
    normalized = normalize_holdings(
        basket_symbol, holding_date, adapted,
        listing_overrides, share_class_groups)
    for row, evidence in zip(normalized, identity):
        row.update(evidence)

    # Membership drift is about source constituents, not filtered cash rows
    # or the economic target used to merge dual share-class weights.
    current_symbols = {
        str(row.get("raw_symbol") or row.get("symbol")
            or row.get("covered_by")).upper()
        for row in normalized
        if (row.get("included") == 1 or row.get("covered_by"))
        and (row.get("raw_symbol") or row.get("symbol")
             or row.get("covered_by"))
    }
    if previous_symbols is not None:
        previous = {str(symbol).upper() for symbol in previous_symbols}
        is_expected_reconstitution = (
            expected_reconstitution_month is not None
            and date.fromisoformat(rebalance_close_date).month
            == expected_reconstitution_month)
        if current_symbols != previous and not is_expected_reconstitution:
            warnings.append("non_reconstitution_membership_delta")
    for row in normalized:
        row["snapshot_warnings_json"] = list(warnings)

    metadata = {
        "basket_symbol": basket_symbol.upper(),
        "holding_date": holding_date,
        "anchor_trading_date": anchor_date,
        "source_kind": source_kind,
        "rebalance_close_date": rebalance_close_date,
        "composition_effective_date": composition_effective_date,
        "composition_available_date": composition_available_date,
        "fetched_at": fetched_at,
        "weight_basis": weight_basis,
        "data_quality_tier": quality,
        "warnings": warnings,
    }
    return normalized, metadata


# ---------------------------------------------------------------------------
# Estimates 规范化（Spec §5.3：weekly 120d 回看窗口 / backfill 2021+ 打标）
# ---------------------------------------------------------------------------

# vendor camelCase → Spec 字段的唯一映射；不在表内的 vendor 字段一律丢弃
_ESTIMATE_FIELD_MAP = {
    "epsAvg": "eps_avg", "epsHigh": "eps_high", "epsLow": "eps_low",
    "revenueAvg": "rev_avg", "revenueHigh": "rev_high", "revenueLow": "rev_low",
    "netIncomeAvg": "net_income_avg", "ebitdaAvg": "ebitda_avg",
    "numAnalystsEps": "num_analysts_eps", "numAnalystsRevenue": "num_analysts_rev",
}

_EARNINGS_FIELD_MAP = {
    "epsActual": "eps_actual", "epsEstimated": "eps_estimated",
    "revenueActual": "revenue_actual", "revenueEstimated": "revenue_estimated",
}

ESTIMATE_LOOKBACK_DAYS = 120  # 报告滞后窗口（P0：PE_blend 的 +1 预测季常已结束未报告）
EARNINGS_MATCH_WINDOW_DAYS = 120


def _parse_iso_date(value: Any) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _row_date(raw: Any) -> Optional[date]:
    """行级校验：vendor list 里混入 None/非 dict 元素时按 malformed 跳过。"""
    if not isinstance(raw, Mapping):
        return None
    return _parse_iso_date(raw.get("date"))


def normalize_estimates(
    symbol: str,
    raw_rows: Sequence[Mapping[str, Any]],
    snapshot_date: str,
    period_type: str,
    mode: str,
    backfill_start: str = "2021-01-01",
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """vendor estimates → fmp_estimates 行 + 数据损耗计数器。

    weekly: 保留 fiscal_date >= snapshot - 120d；backfill: >= backfill_start。
    同 period 重复 fiscal_date 确定性保留首行；坏日期跳过计数，绝不产出 null PK。
    """
    if period_type not in ("Q", "FY"):
        raise ValueError(f"period_type must be 'Q' or 'FY': {period_type!r}")
    snapshot = date.fromisoformat(snapshot_date)
    if mode == "weekly":
        cutoff = snapshot - timedelta(days=ESTIMATE_LOOKBACK_DAYS)
        snapshot_kind = "weekly"
    elif mode == "backfill":
        cutoff = date.fromisoformat(backfill_start)
        snapshot_kind = "backfill"
    else:
        raise ValueError(f"mode must be 'weekly' or 'backfill': {mode!r}")

    counters = {"input": len(raw_rows), "kept": 0, "malformed": 0, "duplicate": 0}
    rows: List[Dict[str, Any]] = []
    seen_dates: set = set()
    for raw in raw_rows:
        fiscal = _row_date(raw)
        if fiscal is None:
            counters["malformed"] += 1
            continue
        if fiscal < cutoff:
            continue
        if fiscal in seen_dates:
            counters["duplicate"] += 1
            continue
        seen_dates.add(fiscal)
        row: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "snapshot_date": snapshot_date,
            "fiscal_date": fiscal.isoformat(),
            "period_type": period_type,
            "snapshot_kind": snapshot_kind,
        }
        for vendor_key, spec_key in _ESTIMATE_FIELD_MAP.items():
            row[spec_key] = raw.get(vendor_key)
        rows.append(row)
        counters["kept"] += 1
    return rows, counters


def extract_valid_quarter_fiscal_dates(
    raw_rows: Sequence[Mapping[str, Any]],
) -> List[str]:
    """全量 raw quarter payload 的有效去重财季日期集。

    earnings fiscal join 必须用这个 pre-filter 全集——120 天规则只约束
    写入 fmp_estimates 的行，绝不约束 fiscal 匹配的查找空间。
    """
    dates = {_row_date(r) for r in raw_rows}
    return sorted(d.isoformat() for d in dates if d is not None)


# ---------------------------------------------------------------------------
# Earnings fiscal 匹配 + 规范化（Spec §5.3：join SSOT 入库）
# ---------------------------------------------------------------------------

def match_fiscal_date(announce_date: str,
                      estimate_fiscal_dates: Iterable[str]) -> Optional[str]:
    """取满足 fiscal < announce 且间隔 <= 120 天的最大 fiscal_date。"""
    announce = _parse_iso_date(announce_date)
    if announce is None:
        return None
    parsed = (_parse_iso_date(d) for d in estimate_fiscal_dates)
    candidates = [d for d in parsed
                  if d is not None and d < announce
                  and (announce - d).days <= EARNINGS_MATCH_WINDOW_DAYS]
    return max(candidates).isoformat() if candidates else None


def normalize_earnings(
    symbol: str,
    raw_rows: Sequence[Mapping[str, Any]],
    quarter_fiscal_dates: Iterable[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """vendor earnings → fmp_earnings 行。绝不用日历季推断 fiscal date。

    epsActual=null 的预排行保留（当前批口径）；无匹配记 match_method='none'，
    行保留但下游计算跳过。
    """
    fiscal_dates = list(quarter_fiscal_dates)
    counters = {"input": len(raw_rows), "kept": 0, "malformed": 0,
                "matched": 0, "unmatched": 0}
    last_updated = datetime.now(timezone.utc).isoformat()
    rows: List[Dict[str, Any]] = []
    for raw in raw_rows:
        announce = _row_date(raw)
        if announce is None:
            counters["malformed"] += 1
            continue
        fiscal = match_fiscal_date(announce.isoformat(), fiscal_dates)
        if fiscal is None:
            counters["unmatched"] += 1
        else:
            counters["matched"] += 1
        row: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "announce_date": announce.isoformat(),
            "fiscal_date": fiscal,
            "match_method": "estimates_window" if fiscal else "none",
            "last_updated": last_updated,
        }
        for vendor_key, spec_key in _EARNINGS_FIELD_MAP.items():
            row[spec_key] = raw.get(vendor_key)
        rows.append(row)
        counters["kept"] += 1
    return rows, counters


# ---------------------------------------------------------------------------
# Universe resolver（writer 与 verifier 共用的唯一分母构造器）
# ---------------------------------------------------------------------------

def resolve_fmp_forward_universe(
    core_symbols: Iterable[str],
    extended_symbols: Iterable[str],
    normalized_holdings: Iterable[Mapping[str, Any]],
    mags_symbols: Iterable[str],
) -> List[str]:
    """core_pool ∪ extended_pool ∪ included 规范化 symbol ∪ MAGS（排序去重）。

    core/extended 任一为空 → fail fast：loader 返回空集意味着池文件损坏，
    静默缩水的分母绝不能被写进 fmp_forward_runs。
    """
    core = {s.upper() for s in core_symbols}
    extended = {s.upper() for s in extended_symbols}
    if not core:
        raise ValueError("core pool symbols empty — pool cache broken, refusing")
    if not extended:
        raise ValueError("extended symbols empty — extended cache broken, refusing")
    included = {r["symbol"] for r in normalized_holdings
                if r["included"] == 1 and r.get("symbol")}
    return sorted(core | extended | included | {s.upper() for s in mags_symbols})
