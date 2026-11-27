"""Unit tests for M1 stock theme basket taxonomy and public flagship overrides."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from terminal.prosperity.version import CODE_VERSION_SOURCES, code_version

REPO_ROOT = Path(__file__).resolve().parents[1]
BASKETS_PATH = REPO_ROOT / "config" / "theme_baskets" / "baskets.json"
OVERRIDES_PATH = REPO_ROOT / "config" / "theme_baskets" / "overrides.json"
REPORT_CONCEPTS_PATH = REPO_ROOT / "config" / "concepts" / "report_concepts.json"
SYMBOL_ALIASES_PATH = REPO_ROOT / "config" / "symbol_aliases.json"

BASKET_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CANONICAL_ASSET_KINDS = {
    "us_equity",
    "etf",
    "fund_cef",
    "non_us_equity",
    "crypto",
    "custom_basket",
    "other",
}
BANNED_MACRO_STYLE_KEYWORDS = (
    "关税",
    "降息",
    "通胀",
    "衰退",
    "高股息",
    "中概风险",
    "tariff",
    "rate_cut",
    "inflation",
    "dividend_style",
)


@pytest.fixture(scope="module")
def baskets_doc() -> dict:
    return json.loads(BASKETS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def overrides_doc() -> dict:
    return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))


def test_baskets_json_schema_and_global_uniqueness(baskets_doc: dict) -> None:
    assert baskets_doc["schema_version"] == 1
    date.fromisoformat(baskets_doc["updated_at"])

    baskets = baskets_doc["baskets"]
    assert len(baskets) == 99

    by_tier: dict[str, list[dict]] = {"pi_bucket": [], "l1": [], "l2": [], "cross": []}
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()

    for entry in baskets:
        bid = entry["basket_id"]
        label = entry["label_zh"].strip()
        tier = entry["tier"]

        assert BASKET_ID_RE.match(bid), f"Invalid basket_id format: {bid}"
        assert bid not in seen_ids, f"Duplicate basket_id: {bid}"
        seen_ids.add(bid)

        assert label, f"Empty label_zh on {bid}"
        assert label not in seen_labels, f"Duplicate label_zh across tiers: {label}"
        seen_labels.add(label)

        assert tier in by_tier, f"Unexpected tier {tier} on {bid}"
        assert entry["is_active"] in (0, 1)
        assert isinstance(entry["sort_order"], int) and entry["sort_order"] > 0
        assert entry["inclusion_rule"].strip(), f"Empty inclusion_rule on {bid}"
        assert entry["exclusion_rule"].strip(), f"Empty exclusion_rule on {bid}"
        by_tier[tier].append(entry)

    assert len(by_tier["pi_bucket"]) == 15
    assert len(by_tier["l1"]) == 10
    assert len(by_tier["l2"]) == 62
    assert len(by_tier["cross"]) == 12
    assert len(by_tier["cross"]) <= 18


def test_pi_bucket_order_and_asset_kind_defaults(baskets_doc: dict) -> None:
    legacy_cfg = json.loads(REPORT_CONCEPTS_PATH.read_text(encoding="utf-8"))
    expected_bucket_order = legacy_cfg["bucket_order"]

    pi_entries = sorted(
        (b for b in baskets_doc["baskets"] if b["tier"] == "pi_bucket"),
        key=lambda b: b["sort_order"],
    )
    assert [b["label_zh"] for b in pi_entries] == expected_bucket_order

    pi_ids = {b["basket_id"] for b in pi_entries}
    defaults = baskets_doc["asset_kind_default_pi_bucket"]
    assert set(defaults.keys()) == CANONICAL_ASSET_KINDS
    for kind, bucket_id in defaults.items():
        assert bucket_id in pi_ids, f"Invalid default pi_bucket {bucket_id} for {kind}"
    assert defaults["etf"] == "pi_etf_macro"
    assert defaults["fund_cef"] == "pi_etf_macro"
    assert defaults["crypto"] == "pi_finance_crypto"


def test_tier_referential_integrity_and_boundary_rules(baskets_doc: dict) -> None:
    baskets = baskets_doc["baskets"]
    l1_ids = {b["basket_id"] for b in baskets if b["tier"] == "l1" and b["is_active"] == 1}
    pi_ids = {b["basket_id"] for b in baskets if b["tier"] == "pi_bucket" and b["is_active"] == 1}

    l2_positive_owner: dict[str, str] = {}
    l1_child_counts = {bid: 0 for bid in l1_ids}

    for b in baskets:
        tier = b["tier"]
        bid = b["basket_id"]
        if tier == "l2":
            assert b["parent_l1_id"] in l1_ids, f"{bid} has invalid parent_l1_id"
            l1_child_counts[b["parent_l1_id"]] += 1
            assert b["default_pi_bucket"] in pi_ids, f"{bid} has invalid default_pi_bucket"
            assert b["min_active_members"] >= 2, f"{bid} min_active_members < 2"
            assert b["narrative_start_date"] is None
            pos = b["positive_examples"]
            neg = b["negative_examples"]
            assert len(pos) >= min(4, b["min_active_members"]), f"{bid} needs enough positive_examples"
            assert len(neg) >= 3, f"{bid} needs >=3 negative_examples"
            assert not (set(pos) & set(neg)), f"{bid} overlaps positive and negative examples"
            for sym in pos:
                assert sym not in l2_positive_owner, (
                    f"{sym} appears in positive_examples of both {l2_positive_owner[sym]} and {bid}"
                )
                l2_positive_owner[sym] = bid
        elif tier == "cross":
            assert b["basket_id"].startswith("cross_")
            assert b["parent_l1_id"] is None
            assert b["default_pi_bucket"] is None
            assert b["min_active_members"] >= 5
            date.fromisoformat(b["narrative_start_date"])
            assert len(b["positive_examples"]) >= 6
            assert len(b["negative_examples"]) >= 3
            assert not (set(b["positive_examples"]) & set(b["negative_examples"]))
            combined = f"{bid} {b['label_zh']}".lower()
            for banned in BANNED_MACRO_STYLE_KEYWORDS:
                assert banned not in combined, f"Macro/style tag leaked into cross basket {bid}"
        else:
            assert b["parent_l1_id"] is None
            assert b["default_pi_bucket"] is None
            assert b["narrative_start_date"] is None

    for l1_id, count in l1_child_counts.items():
        assert count >= 3, f"L1 sector {l1_id} has too few L2 children ({count})"


def test_overrides_json_integrity_and_alias_non_conflict(
    baskets_doc: dict, overrides_doc: dict
) -> None:
    assert overrides_doc["schema_version"] == 1
    date.fromisoformat(overrides_doc["updated_at"])

    baskets_by_id = {b["basket_id"]: b for b in baskets_doc["baskets"]}
    l2_ids = {bid for bid, b in baskets_by_id.items() if b["tier"] == "l2"}
    cross_ids = {bid for bid, b in baskets_by_id.items() if b["tier"] == "cross"}

    locked = overrides_doc["locked_stocks"]
    assert len(locked) >= 30
    locked_syms: set[str] = set()

    for item in locked:
        sym = item["symbol"]
        assert sym not in locked_syms, f"Duplicate locked symbol: {sym}"
        locked_syms.add(sym)
        assert ":" not in sym, f"Non-US/prefixed symbol {sym} must not be in public overrides"
        assert sym != "DXYZ", "CEF/private wrappers must not be in US equity L2 overrides"
        assert item["l2_id"] in l2_ids, f"Unknown l2_id {item['l2_id']} for {sym}"
        assert item["rationale"].strip(), f"Empty rationale for {sym}"

        # Must not appear in negative_examples of its assigned L2
        assert sym not in baskets_by_id[item["l2_id"]]["negative_examples"]
        # Must not appear in positive_examples of a different L2
        for other_l2 in l2_ids - {item["l2_id"]}:
            assert sym not in baskets_by_id[other_l2]["positive_examples"], (
                f"{sym} locked to {item['l2_id']} but listed in {other_l2} positive_examples"
            )

        seen_cross: set[str] = set()
        for ct in item["cross_themes"]:
            tid = ct["theme_id"]
            assert tid in cross_ids, f"Unknown cross theme {tid} on {sym}"
            assert tid not in seen_cross, f"Duplicate cross theme {tid} on {sym}"
            seen_cross.add(tid)
            assert ct["role"] in ("core", "platform_giant")
            assert sym not in baskets_by_id[tid]["negative_examples"]

    sys_aliases = {
        a["alias"]: a["canonical"]
        for a in json.loads(SYMBOL_ALIASES_PATH.read_text(encoding="utf-8"))["aliases"]
    }
    supp_aliases = overrides_doc["supplemental_aliases"]
    seen_aliases: set[str] = set()
    for al in supp_aliases:
        alias_sym = al["alias_symbol"]
        canon_sym = al["canonical_symbol"]
        assert alias_sym != canon_sym
        assert alias_sym not in seen_aliases, f"Duplicate alias_symbol {alias_sym}"
        seen_aliases.add(alias_sym)
        assert alias_sym not in locked_syms, f"Alias {alias_sym} must not be locked directly"
        assert al["kind"] in ("share_class", "rename", "dual_listing", "merger")
        if al["effective_from"] is not None:
            date.fromisoformat(al["effective_from"])
        if alias_sym in sys_aliases:
            assert sys_aliases[alias_sym] == canon_sym

    # Ensure zero 2-hop alias chains inside supplemental_aliases
    assert not (seen_aliases & {al["canonical_symbol"] for al in supp_aliases})


def test_in_memory_sqlite_ddl_rehearsal(baskets_doc: dict, overrides_doc: dict) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE theme_baskets (
            basket_id TEXT PRIMARY KEY,
            label_zh TEXT NOT NULL UNIQUE,
            tier TEXT NOT NULL CHECK (tier IN ('l1', 'l2', 'cross', 'pi_bucket')),
            parent_l1_id TEXT REFERENCES theme_baskets(basket_id),
            default_pi_bucket TEXT REFERENCES theme_baskets(basket_id),
            sort_order INTEGER NOT NULL DEFAULT 0,
            min_active_members INTEGER NOT NULL DEFAULT 0,
            narrative_start_date TEXT,
            inclusion_rule TEXT NOT NULL,
            exclusion_rule TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE stock_themes (
            symbol TEXT PRIMARY KEY,
            asset_kind TEXT NOT NULL CHECK (
                asset_kind IN ('us_equity','etf','fund_cef','non_us_equity','crypto','custom_basket','other')
            ),
            in_us_basket_calc INTEGER NOT NULL CHECK (in_us_basket_calc IN (0, 1)),
            is_superseded INTEGER NOT NULL DEFAULT 0 CHECK (is_superseded IN (0, 1)),
            l1_id TEXT REFERENCES theme_baskets(basket_id),
            l2_id TEXT REFERENCES theme_baskets(basket_id),
            pi_bucket TEXT NOT NULL REFERENCES theme_baskets(basket_id),
            source TEXT NOT NULL CHECK (source IN ('override', 'db_locked', 'agy_llm')),
            agreement_status TEXT NOT NULL CHECK (
                agreement_status IN ('locked', 'dual_agreed', 'single_pass_weekly', 'needs_spot_check', 'disagreed')
            ),
            updated_at TEXT NOT NULL,
            CHECK (in_us_basket_calc = 0 OR (asset_kind = 'us_equity' AND l1_id IS NOT NULL AND l2_id IS NOT NULL))
        );

        CREATE TABLE stock_cross_themes (
            symbol TEXT NOT NULL REFERENCES stock_themes(symbol),
            theme_id TEXT NOT NULL REFERENCES theme_baskets(basket_id),
            role TEXT NOT NULL CHECK (role IN ('core', 'platform_giant')),
            source TEXT NOT NULL CHECK (source IN ('override', 'db_locked', 'agy_llm')),
            PRIMARY KEY (symbol, theme_id)
        );
        """
    )

    # Insert in dependency order: pi_bucket & l1 first, then l2 & cross
    tier_order = {"pi_bucket": 0, "l1": 1, "l2": 2, "cross": 3}
    ordered_baskets = sorted(baskets_doc["baskets"], key=lambda b: tier_order[b["tier"]])
    for b in ordered_baskets:
        conn.execute(
            """
            INSERT INTO theme_baskets (
                basket_id, label_zh, tier, parent_l1_id, default_pi_bucket,
                sort_order, min_active_members, narrative_start_date,
                inclusion_rule, exclusion_rule, is_active, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                b["basket_id"],
                b["label_zh"],
                b["tier"],
                b["parent_l1_id"],
                b["default_pi_bucket"],
                b["sort_order"],
                b["min_active_members"],
                b["narrative_start_date"],
                b["inclusion_rule"],
                b["exclusion_rule"],
                b["is_active"],
                baskets_doc["updated_at"],
            ),
        )

    l2_lookup = {b["basket_id"]: b for b in baskets_doc["baskets"] if b["tier"] == "l2"}
    for lk in overrides_doc["locked_stocks"]:
        l2_meta = l2_lookup[lk["l2_id"]]
        conn.execute(
            """
            INSERT INTO stock_themes (
                symbol, asset_kind, in_us_basket_calc, is_superseded,
                l1_id, l2_id, pi_bucket, source, agreement_status, updated_at
            ) VALUES (?, 'us_equity', 1, 0, ?, ?, ?, 'override', 'locked', ?)
            """,
            (
                lk["symbol"],
                l2_meta["parent_l1_id"],
                lk["l2_id"],
                l2_meta["default_pi_bucket"],
                overrides_doc["updated_at"],
            ),
        )
        for ct in lk["cross_themes"]:
            conn.execute(
                "INSERT INTO stock_cross_themes (symbol, theme_id, role, source) VALUES (?, ?, ?, 'override')",
                (lk["symbol"], ct["theme_id"], ct["role"]),
            )

    assert conn.execute("SELECT COUNT(*) FROM theme_baskets").fetchone()[0] == 99
    assert conn.execute("SELECT COUNT(*) FROM stock_themes").fetchone()[0] == len(overrides_doc["locked_stocks"])

    # Duplicate label_zh must fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO theme_baskets (
                basket_id, label_zh, tier, sort_order, min_active_members,
                inclusion_rule, exclusion_rule, is_active, updated_at
            ) VALUES ('dup_test', '网络安全', 'cross', 999, 5, 'a', 'b', 1, '2026-11-27')
            """
        )
    conn.close()


def test_prosperity_code_version_preserved() -> None:
    for rel in CODE_VERSION_SOURCES:
        assert not rel.startswith("config/theme_baskets")
    assert code_version(REPO_ROOT) == "4c9da30bd5001102"
