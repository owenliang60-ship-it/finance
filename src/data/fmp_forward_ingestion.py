"""FMP forward EPS 纯转换层：holdings 规范化 / 配置加载 / universe resolver。

本模块绝不调用 API 或 DB（纯函数，全部可单测）。
Spec: docs/design/2026-07-09-fmp-forward-eps-valuation-spec.md §5.4 / §7.1
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

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
