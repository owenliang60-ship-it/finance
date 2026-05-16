"""build_company_concept_registry.py — v2 build pipeline tests.

Coverage:
    - classify chain v2 (manual / rule / llm / llm_failed / llm_fallback)
    - LLM wiring (rule-miss triggers prefill_one; rule-hit skips)
    - 15-col review CSV (hard + soft queues)
    - layered gate (priority_coverage + tail_needs_review_rate + broad_top empty)
    - rebuild_display_tags from concepts.label
    - CLI helpers (_load_universe / _read_portfolio_holdings / _read_broad_top)
"""
import csv
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CFG = PROJECT_ROOT / "config" / "concepts"
TAXONOMY_V2_PATH = CFG / "concept_taxonomy_v2.json"
WATCHLIST_PATH = CFG / "concept_watchlist.json"


# ---- fixtures ----


@pytest.fixture
def build_env(tmp_path):
    """Bootstrap a v2 MarketStore + ConceptRegistry (NO watchlist).

    Empty watchlist keeps the LLM wiring tests deterministic — no surprise
    unclassified symbols pulled in from the watchlist.

    NVDA / MU here are synthetic fixture handles for the two industry_map
    rule-hit branches. Under A+ a deterministic rule hit needs a (sector,
    industry) pair that is actually in the map — and no semiconductor industry
    is (they are deliberately ambiguous → LLM). The symbol names are arbitrary.
    """
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(
        taxonomy_path=TAXONOMY_V2_PATH,
        watchlist_path=None,
    )
    profiles = {
        # Anchor (manual): AMZN
        "AMZN": {"symbol": "AMZN", "industry": "Internet Retail"},
        # industry_map rule hits (deterministic, no LLM call)
        "NVDA": {"symbol": "NVDA", "sector": "Industrials",
                 "industry": "Aerospace & Defense",
                 "description": "aerospace & defense systems"},
        "MU": {"symbol": "MU", "sector": "Utilities",
               "industry": "Regulated Electric",
               "description": "regulated electric utility"},
        # anchor + industry_map both miss → unclassified (LLM wrapper kicks in)
        "OBSCURE": {"symbol": "OBSCURE", "industry": "Unknown",
                    "description": "Mystery"},
    }
    return tmp_path, store, registry, profiles


def _fake_llm(**overrides):
    from terminal.llm_concept_prefill import LLMResult
    base = dict(
        l1="industrial_aerospace",
        l2="engineering_construction",
        l3_themes=[],
        business_role="工程建筑",
        confidence=0.75,
        source="llm",
        evidence="claude",
        needs_review=0,
    )
    base.update(overrides)
    return LLMResult(**base)


# ---- LLM wiring (Task 4b core) ----


def test_build_registry_calls_llm_on_rule_miss(build_env):
    """rule + override 双 miss 时调 prefill_one；rule 命中时不调。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=_fake_llm(),
    ) as mocked:
        build_registry(
            store=store, registry=registry,
            universe_symbols=["NVDA", "OBSCURE"],
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=["NVDA", "OBSCURE"],
            review_csv_path=tmp_path / "out.csv",
            save=False, force_save=False,
        )
    assert mocked.call_count == 1
    called_symbol = mocked.call_args_list[0].kwargs.get("symbol")
    assert called_symbol == "OBSCURE"


def test_build_registry_skips_llm_when_rule_hits(build_env):
    """rule 命中后绝不调 LLM (节省 533 次中能省的尽量省)。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    with patch(
        "scripts.build_company_concept_registry.prefill_one"
    ) as mocked:
        build_registry(
            store=store, registry=registry,
            universe_symbols=["NVDA", "MU"],
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=["NVDA", "MU"],
            review_csv_path=tmp_path / "out.csv",
            save=False, force_save=False,
        )
    assert mocked.call_count == 0


def test_build_registry_skips_llm_when_anchor_hits(build_env):
    """Anchor 命中 (AMZN) 不应该调 LLM。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    with patch(
        "scripts.build_company_concept_registry.prefill_one"
    ) as mocked:
        build_registry(
            store=store, registry=registry,
            universe_symbols=["AMZN"],
            profiles=profiles,
            portfolio_holdings=["AMZN"],
            broad_top_symbols=["AMZN"],
            review_csv_path=tmp_path / "out.csv",
            save=False, force_save=False,
        )
    assert mocked.call_count == 0


def test_build_registry_llm_failed_keeps_row_blank_l1(build_env):
    """LLM 失败时 row 进 CSV 但 l1/l2 留空，prefill_source=llm_failed, needs_review=1。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    failed = _fake_llm(
        l1=None, l2=None, l3_themes=[], business_role="",
        confidence=0.0, source="llm_failed", evidence="timeout", needs_review=1,
    )
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=failed,
    ):
        build_registry(
            store=store, registry=registry,
            universe_symbols=["OBSCURE"],
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=["OBSCURE"],
            review_csv_path=tmp_path / "out.csv",
            save=False, force_save=False,
        )
    rows = list(csv.DictReader((tmp_path / "out.csv").open()))
    obscure = next(r for r in rows if r["symbol"] == "OBSCURE")
    assert obscure["l1"] == ""
    assert obscure["l2"] == ""
    assert obscure["prefill_source"] == "llm_failed"
    assert obscure["needs_review"] == "1"
    assert obscure["review_reason"] == "hard_needs_review"


def test_build_registry_llm_succeeds_writes_l1_l2(build_env):
    """LLM 成功时 row 用 LLM 返回的 (l1,l2,l3) 填充，source=llm, needs_review=0。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    llm = _fake_llm(
        l1="industrial_aerospace", l2="engineering_construction",
        l3_themes=[], business_role="工程", confidence=0.85,
        source="llm", needs_review=0,
    )
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=llm,
    ):
        result = build_registry(
            store=store, registry=registry,
            universe_symbols=["OBSCURE"],
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=["OBSCURE"],
            review_csv_path=tmp_path / "out.csv",
            save=False, force_save=False,
        )
    assert result.llm == 1
    assert result.needs_review == 0


# ---- v2 dry-run & save ----


def test_dry_run_does_not_write_db(build_env):
    """dry-run 不写 concepts / company_concept_tags / concept_themes 都不动。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    csv_path = tmp_path / "review.csv"
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=_fake_llm(),
    ):
        result = build_registry(
            store=store, registry=registry,
            universe_symbols=["AMZN", "NVDA", "MU"],
            profiles=profiles,
            portfolio_holdings=["AMZN", "NVDA"],
            broad_top_symbols=["AMZN", "NVDA", "MU"],
            review_csv_path=csv_path,
            save=False, force_save=False,
        )
    assert result.saved is False
    assert csv_path.exists()
    conn = store._get_conn()
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM company_concept_tags").fetchone()[0] == 0


def test_save_writes_concepts_114_rows(build_env):
    """save 后 concepts 表必须含 11 L1 + 61 L2 + 42 L3 = 114 行
    (telecom_operator L2 由 2026-05-16 重建新增)。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["AMZN", "NVDA", "MU"],
        profiles=profiles,
        portfolio_holdings=["AMZN", "NVDA"],
        broad_top_symbols=["AMZN", "NVDA", "MU"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=False,
    )
    assert result.saved is True
    conn = store._get_conn()
    counts = dict(conn.execute(
        "SELECT level, COUNT(*) FROM concepts GROUP BY level"
    ).fetchall())
    assert counts == {1: 11, 2: 61, 3: 42}


def test_save_persists_amzn_anchor(build_env):
    """AMZN anchor 保存后 DB 中 primary=ai_compute_cloud, secondary=hyperscaler。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    build_registry(
        store=store, registry=registry,
        universe_symbols=["AMZN"],
        profiles=profiles,
        portfolio_holdings=["AMZN"],
        broad_top_symbols=["AMZN"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=True,
    )
    amzn = store.get_company_concepts(["AMZN"])["AMZN"]
    assert amzn["primary_concept_id"] == "ai_compute_cloud"
    assert amzn["secondary_concept_id"] == "hyperscaler"
    assert amzn["theme_ids"] == ["ai_compute"]
    assert amzn["source"] == "manual"


def test_save_fails_when_priority_not_fully_covered(build_env):
    """OBSCURE 走 LLM_failed → needs_review=1 → priority_coverage<100% → gate 失败。"""
    from scripts.build_company_concept_registry import BuildGateError, build_registry

    tmp_path, store, registry, profiles = build_env
    failed = _fake_llm(l1=None, l2=None, source="llm_failed",
                       confidence=0.0, needs_review=1)
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=failed,
    ):
        with pytest.raises(BuildGateError):
            build_registry(
                store=store, registry=registry,
                universe_symbols=["AMZN", "OBSCURE"],
                profiles=profiles,
                portfolio_holdings=[],
                broad_top_symbols=["OBSCURE"],
                review_csv_path=tmp_path / "review.csv",
                save=True, force_save=False,
            )
    # No partial write
    assert store.get_company_concept_coverage()["total"] == 0


def test_force_save_bypasses_gate(build_env):
    """--force-save 绕过 gate 但 needs_review 行仍不进 DB（l1=None 违 FK）。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    failed = _fake_llm(l1=None, l2=None, source="llm_failed",
                       confidence=0.0, needs_review=1)
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=failed,
    ):
        result = build_registry(
            store=store, registry=registry,
            universe_symbols=["AMZN", "OBSCURE"],
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=["OBSCURE"],
            review_csv_path=tmp_path / "review.csv",
            save=True, force_save=True,
        )
    assert result.saved is True
    assert result.forced_save is True
    # AMZN persisted; OBSCURE skipped (l1=None can't satisfy FK)
    fetched = store.get_company_concepts(["AMZN", "OBSCURE"])
    assert "AMZN" in fetched
    assert "OBSCURE" not in fetched


def test_save_fails_when_broad_top_empty(build_env):
    """空 broad_top → gate 失败。"""
    from scripts.build_company_concept_registry import BuildGateError, build_registry

    tmp_path, store, registry, profiles = build_env
    with pytest.raises(BuildGateError, match="broad_top is empty"):
        build_registry(
            store=store, registry=registry,
            universe_symbols=["AMZN"],
            profiles=profiles,
            portfolio_holdings=["AMZN"],
            broad_top_symbols=[],
            review_csv_path=tmp_path / "review.csv",
            save=True, force_save=False,
        )


def test_csv_has_15_columns_review_reason_plus_14(build_env):
    """v2 CSV header 必须有 review_reason + 15 列 schema = 16 列。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    csv_path = tmp_path / "review.csv"
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=_fake_llm(),
    ):
        build_registry(
            store=store, registry=registry,
            universe_symbols=["NVDA"],
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=["NVDA"],
            review_csv_path=csv_path,
            save=False, force_save=False,
        )
    header = next(csv.reader(csv_path.open()))
    # 16 columns total (review_reason + 15 data columns per spec §6.1)
    assert len(header) == 16
    required = {
        "symbol", "company_name", "fmp_sector", "fmp_industry",
        "market_cap_b", "mcap_tier", "description",
        "l1", "l2", "l3_themes", "business_role",
        "prefill_source", "confidence", "needs_review", "boss_notes",
    }
    assert required.issubset(set(header))


def test_soft_review_includes_low_confidence_llm_rows(build_env):
    """source=llm 行 (confidence < 0.7) 进 soft_low_confidence 队列。

    A+ 之后 industry_map rule 行的 confidence 恒为 0.7（`0.7 < 0.7` 为假），
    永不进 soft 队列；soft 队列只收低置信度的 LLM 行。"""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    csv_path = tmp_path / "review.csv"
    # SAAS hits no anchor and no industry_map key (Software - Application is a
    # deliberately ambiguous industry) → unclassified → LLM.
    profiles_low = {
        "SAAS": {"symbol": "SAAS", "sector": "Technology",
                 "industry": "Software - Application",
                 "description": "enterprise SaaS"},
    }
    low_conf_llm = _fake_llm(confidence=0.55, source="llm", needs_review=0)
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=low_conf_llm,
    ):
        result = build_registry(
            store=store, registry=registry,
            universe_symbols=["SAAS"],
            profiles=profiles_low,
            portfolio_holdings=[],
            broad_top_symbols=["SAAS"],
            review_csv_path=csv_path,
            save=False, force_save=False,
        )
    assert result.llm == 1
    assert result.soft_review == 1
    rows = list(csv.DictReader(csv_path.open()))
    saas = next(r for r in rows if r["symbol"] == "SAAS")
    assert saas["review_reason"] == "soft_low_confidence"


# ---- rebuild_display_tags ----


def test_rebuild_display_tags_v2(build_env):
    """rebuild_display_tags 用 concepts.label 重拼三段 display_tags。"""
    from scripts.build_company_concept_registry import (
        build_registry, rebuild_display_tags,
    )

    tmp_path, store, registry, profiles = build_env
    build_registry(
        store=store, registry=registry,
        universe_symbols=["AMZN"],
        profiles=profiles,
        portfolio_holdings=["AMZN"],
        broad_top_symbols=["AMZN"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=True,
    )
    # Mutate concept label and verify rebuild picks it up
    conn = store._get_conn()
    conn.execute(
        "UPDATE concepts SET label = '云端霸主' WHERE concept_id = 'hyperscaler'"
    )
    conn.commit()

    summary = rebuild_display_tags(store=store, registry=registry)
    assert summary["updated"] >= 1
    amzn = store.get_company_concepts(["AMZN"])["AMZN"]
    assert "云端霸主" in amzn["display_tags"]


# ---- Phase 4: write_review_csv with mcap_tier + Chinese labels ----


def _seed_company_db(tmp_path: Path, market_caps: dict[str, float]) -> Path:
    """Bootstrap a minimal company.db mirroring the real schema columns we need."""
    import sqlite3
    db = tmp_path / "company.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE companies (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            sector TEXT,
            industry TEXT,
            market_cap REAL,
            exchange TEXT
        )
    """)
    for sym, cap in market_caps.items():
        conn.execute(
            "INSERT INTO companies (symbol, market_cap) VALUES (?, ?)",
            (sym, cap),
        )
    conn.commit()
    conn.close()
    return db


def test_mcap_to_tier_boundaries():
    from scripts.build_company_concept_registry import _mcap_to_tier
    assert _mcap_to_tier(1_000e9) == "mega"
    assert _mcap_to_tier(999e9) == "large"
    assert _mcap_to_tier(300e9) == "large"
    assert _mcap_to_tier(299e9) == "mid"
    assert _mcap_to_tier(100e9) == "mid"
    assert _mcap_to_tier(99e9) == "small"
    assert _mcap_to_tier(10e9) == "small"
    assert _mcap_to_tier(9e9) == ""
    assert _mcap_to_tier(None) == ""
    assert _mcap_to_tier(0) == ""


def test_load_market_caps_from_company_db(tmp_path):
    from scripts.build_company_concept_registry import _load_market_caps_from_company_db
    db = _seed_company_db(tmp_path, {"AAPL": 3_500e9, "FOO": 50e9})
    caps = _load_market_caps_from_company_db(db)
    assert caps["AAPL"] == 3_500e9
    assert caps["FOO"] == 50e9


def test_load_market_caps_returns_empty_when_db_missing(tmp_path):
    from scripts.build_company_concept_registry import _load_market_caps_from_company_db
    assert _load_market_caps_from_company_db(tmp_path / "missing.db") == {}


def test_write_review_csv_uses_chinese_labels_and_mcap_tier(tmp_path):
    """v2 CSV: l1/l2/l3 列写中文 label，mcap_tier 来自 market_caps 字典。"""
    from scripts.build_company_concept_registry import write_review_csv

    rows = [
        {"symbol": "AAPL", "l1": "consumer_retail",
         "l2": "consumer_electronics_brand", "l3_themes": ["edge_ai"],
         "business_role": "iPhone", "confidence": 0.95,
         "source": "manual", "needs_review": 0, "evidence": "",
         "display_tags": ""},
        {"symbol": "FOO", "l1": None, "l2": None, "l3_themes": [],
         "business_role": "", "confidence": 0.0, "source": "llm_failed",
         "needs_review": 1, "evidence": "timeout", "display_tags": ""},
    ]
    profiles = {
        "AAPL": {"symbol": "AAPL", "companyName": "Apple Inc.",
                 "sector": "Tech", "industry": "Consumer Electronics",
                 "description": "iPhone"},
        "FOO": {"symbol": "FOO", "companyName": "Foo Corp.",
                "sector": "Industrials", "industry": "Construction",
                "description": "Builds stuff"},
    }
    market_caps = {"AAPL": 3_500e9, "FOO": 50e9}
    taxonomy = json.loads(TAXONOMY_V2_PATH.read_text(encoding="utf-8"))

    csv_path = tmp_path / "out.csv"
    write_review_csv(
        rows=rows, csv_path=csv_path,
        taxonomy=taxonomy, profiles=profiles, market_caps=market_caps,
    )

    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        out = list(reader)
        fields = reader.fieldnames

    assert fields is not None
    assert len(fields) == 16  # review_reason + 15 data columns
    by_sym = {r["symbol"]: r for r in out}

    aapl = by_sym["AAPL"]
    assert aapl["mcap_tier"] == "mega"
    assert aapl["l1"] == "消费与零售"       # Chinese label
    assert aapl["l2"] == "消费电子与品牌硬件"
    assert aapl["l3_themes"] == "端侧AI"
    assert aapl["company_name"] == "Apple Inc."

    foo = by_sym["FOO"]
    assert foo["mcap_tier"] == "small"
    assert foo["l1"] == ""  # failed row leaves l1 blank
    assert foo["l2"] == ""
    assert foo["needs_review"] == "1"
    assert foo["review_reason"] == "hard_needs_review"


def test_taxonomy_reference_csv_lists_all_114_concepts(tmp_path):
    """taxonomy_reference.csv 必须含 11+61+42 = 114 行 (header 之外)。"""
    from scripts.build_company_concept_registry import _write_taxonomy_reference_csv

    taxonomy = json.loads(TAXONOMY_V2_PATH.read_text(encoding="utf-8"))
    out = tmp_path / "taxonomy_reference.csv"
    _write_taxonomy_reference_csv(taxonomy, out)

    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 114
    levels = {int(r["level"]) for r in rows}
    assert levels == {1, 2, 3}


# ---- Phase 2: refresh_profiles ----


def test_refresh_profiles_writes_json_for_each_symbol(tmp_path, monkeypatch):
    """profiles.json 必须包含每个 symbol 的 description/sector/industry/companyName。"""
    profiles_path = tmp_path / "profiles.json"

    fake_fmp = {
        "AAPL": {"symbol": "AAPL", "companyName": "Apple Inc.", "sector": "Tech",
                 "industry": "Consumer Electronics", "description": "iPhone maker"},
        "MSFT": {"symbol": "MSFT", "companyName": "Microsoft Corp.", "sector": "Tech",
                 "industry": "Software", "description": "Azure + Office"},
    }
    monkeypatch.setattr(
        "scripts.build_company_concept_registry._fetch_fmp_profile",
        lambda sym: fake_fmp[sym],
    )

    from scripts.build_company_concept_registry import refresh_profiles
    count = refresh_profiles(symbols=["AAPL", "MSFT"], profiles_path=profiles_path)

    assert count == 2
    assert profiles_path.exists()
    data = json.loads(profiles_path.read_text(encoding="utf-8"))
    by_sym = data if isinstance(data, dict) else {p["symbol"]: p for p in data}
    assert by_sym["AAPL"]["description"] == "iPhone maker"
    assert by_sym["MSFT"]["industry"] == "Software"
    assert by_sym["AAPL"]["sector"] == "Tech"


def test_refresh_profiles_backs_up_existing_json(tmp_path, monkeypatch):
    """如果 profiles.json 已存在，先备份再覆盖。"""
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(json.dumps({"OLD": {"symbol": "OLD"}}), encoding="utf-8")

    monkeypatch.setattr(
        "scripts.build_company_concept_registry._fetch_fmp_profile",
        lambda sym: {"symbol": sym, "description": "new"},
    )

    from scripts.build_company_concept_registry import refresh_profiles
    refresh_profiles(symbols=["AAPL"], profiles_path=profiles_path)

    backups = list(tmp_path.glob("profiles.json.backup-*-preprofiles"))
    assert len(backups) == 1
    old = json.loads(backups[0].read_text(encoding="utf-8"))
    assert "OLD" in old


def test_refresh_profiles_skips_symbols_with_fmp_errors(tmp_path, monkeypatch):
    """单个 symbol FMP 调用失败不应整个 batch 中断；其他 symbol 仍写入。"""
    profiles_path = tmp_path / "profiles.json"

    def _fake(sym):
        if sym == "BAD":
            raise RuntimeError("FMP rate limit")
        return {"symbol": sym, "description": "ok"}

    monkeypatch.setattr(
        "scripts.build_company_concept_registry._fetch_fmp_profile", _fake,
    )

    from scripts.build_company_concept_registry import refresh_profiles
    count = refresh_profiles(["AAPL", "BAD", "MSFT"], profiles_path=profiles_path)
    assert count == 2
    data = json.loads(profiles_path.read_text(encoding="utf-8"))
    # Successful symbols persisted; _meta tracks freshness (parity with
    # fundamental_fetcher.update_profiles). BAD is absent because no prior
    # cache to preserve in this test.
    assert "AAPL" in data and "MSFT" in data
    assert "BAD" not in data
    assert "_meta" in data and "updated_at" in data["_meta"]


# ---- CLI helpers ----


def test_load_universe_returns_empty_when_path_missing(tmp_path):
    from scripts.build_company_concept_registry import _load_universe
    assert _load_universe(tmp_path / "missing.json") == []


def test_load_universe_accepts_raw_list(tmp_path):
    from scripts.build_company_concept_registry import _load_universe
    p = tmp_path / "u.json"
    p.write_text(json.dumps(["mu", "AAPL"]), encoding="utf-8")
    assert _load_universe(p) == ["MU", "AAPL"]


def test_load_universe_accepts_symbols_list_dict(tmp_path):
    from scripts.build_company_concept_registry import _load_universe
    p = tmp_path / "u.json"
    p.write_text(
        json.dumps({"updated": "2026-04-25", "symbols": ["mu", "nvda"]}),
        encoding="utf-8",
    )
    assert _load_universe(p) == ["MU", "NVDA"]


def test_load_universe_accepts_broad_universe_stocks_dict(tmp_path):
    from scripts.build_company_concept_registry import _load_universe
    p = tmp_path / "u.json"
    p.write_text(
        json.dumps({
            "updated": "2026-04-25",
            "stocks": {
                "MU": {"marketCap": 100e9},
                "nvda": {"marketCap": 3e12},
                "AAPL": {"marketCap": 3e12},
            },
        }),
        encoding="utf-8",
    )
    out = _load_universe(p)
    assert sorted(out) == ["AAPL", "MU", "NVDA"]


def test_load_universe_returns_empty_for_unknown_dict_shape(tmp_path):
    from scripts.build_company_concept_registry import _load_universe
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    assert _load_universe(p) == []


def test_cli_reader_portfolio_uses_correct_company_store_api(monkeypatch, tmp_path):
    """_read_portfolio_holdings must call CompanyStore(path) directly so the
    --company-db override actually targets the requested DB. Using the
    get_store() singleton silently ignores subsequent paths after the first
    call (singleton cache).
    """
    from scripts import build_company_concept_registry as mod

    fake_called = {"hits": 0, "paths": []}

    class _FakeStore:
        def __init__(self, db_path=None):
            fake_called["paths"].append(db_path)

        def get_all_open_holdings(self):
            fake_called["hits"] += 1
            return [{"symbol": "MU"}, {"symbol": "nvda"}, {"symbol": ""}]

    import terminal.company_store as cs_mod
    monkeypatch.setattr(cs_mod, "CompanyStore", _FakeStore)

    target = tmp_path / "company.db"
    target.write_bytes(b"")  # exists() check passes

    result = mod._read_portfolio_holdings(target)
    assert fake_called["hits"] == 1
    assert fake_called["paths"] == [target], (
        "explicit company_db_path must be passed straight to CompanyStore"
    )
    assert result == ["MU", "NVDA"]


def test_cli_reader_broad_top_uses_correct_get_rankings_signature(monkeypatch):
    from scripts import build_company_concept_registry as mod
    from src.data import dollar_volume as dv_mod

    captured = {}

    def _fake_get_latest_date(db_path=None):
        return "2026-04-25"

    def _fake_get_rankings(date, limit=50, db_path=None):
        captured["date"] = date
        captured["limit"] = limit
        return [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "nvda"}]

    monkeypatch.setattr(dv_mod, "get_latest_date", _fake_get_latest_date)
    monkeypatch.setattr(dv_mod, "get_rankings", _fake_get_rankings)

    result = mod._read_broad_top(100)
    assert captured == {"date": "2026-04-25", "limit": 100}
    assert result == ["AAPL", "MSFT", "NVDA"]


def test_cli_reader_broad_top_empty_when_no_rankings(monkeypatch):
    from scripts import build_company_concept_registry as mod
    from src.data import dollar_volume as dv_mod

    monkeypatch.setattr(dv_mod, "get_latest_date", lambda *a, **kw: None)
    monkeypatch.setattr(dv_mod, "get_rankings",
                        lambda *a, **kw: pytest.fail("should not be called"))

    assert mod._read_broad_top(100) == []


# ---- Phase 5: read_reviewed_csv fail-fast validation (Task 7) ----

_TAXONOMY = json.loads(TAXONOMY_V2_PATH.read_text(encoding="utf-8"))
_EXTEND_POOL = {"NVDA"}


def _valid_row(symbol: str = "NVDA") -> dict:
    """One canonical valid CSV row that passes all 10 checks."""
    return {
        "review_reason": "ok",
        "symbol": symbol,
        "company_name": "NVIDIA Corp.",
        "fmp_sector": "Technology",
        "fmp_industry": "Semiconductors",
        "market_cap_b": "3000.00",
        "mcap_tier": "mega",
        "description": "GPU + AI accelerator",
        "l1": "半导体",                          # semiconductor
        "l2": "计算芯片/GPU加速器",              # gpu_accelerator
        "l3_themes": "AI算力",                   # ai_compute (label alias)
        "business_role": "GPU",
        "prefill_source": "manual",
        "confidence": "0.95",
        "needs_review": "0",
        "boss_notes": "",
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    from scripts.build_company_concept_registry import REVIEW_CSV_FIELDS
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in REVIEW_CSV_FIELDS})


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_valid_csv(tmp_path: Path) -> Path:
    p = tmp_path / "review.csv"
    _write_csv(p, [_valid_row()])
    return p


def _write_csv_with_2_bad_rows(tmp_path: Path) -> Path:
    """Row 1 missing l1; Row 2 has l2 parent mismatch with l1."""
    bad1 = {**_valid_row("NVDA"), "l1": ""}
    bad2 = {**_valid_row("AMD"), "l1": "金融", "l2": "超大规模云"}  # mismatch
    p = tmp_path / "review.csv"
    _write_csv(p, [bad1, bad2])
    return p


def _write_single_row_with_three_errors(tmp_path: Path) -> Path:
    """One row that simultaneously violates: l1 empty + l2 empty + bad L3."""
    bad = {**_valid_row("FOO"), "l1": "", "l2": "", "l3_themes": "电商生态"}
    p = tmp_path / "review.csv"
    _write_csv(p, [bad])
    return p


def _write_csv_with_one_good_row(tmp_path: Path, symbol: str) -> Path:
    p = tmp_path / "review.csv"
    _write_csv(p, [_valid_row(symbol)])
    return p


@pytest.mark.parametrize("scenario,mutations,expected_msg", [
    ("missing_row", lambda rows: rows[:-1], "missing"),
    ("duplicate_row", lambda rows: rows + [rows[0]], "duplicate"),
    ("empty_l1", lambda rows: [{**rows[0], "l1": ""}] + rows[1:], "l1 empty"),
    ("empty_l2", lambda rows: [{**rows[0], "l2": ""}] + rows[1:], "l2 empty"),
    ("invalid_l1", lambda rows: [{**rows[0], "l1": "无效L1"}] + rows[1:], "not in 11 l1"),
    ("invalid_l2", lambda rows: [{**rows[0], "l2": "无效L2"}] + rows[1:], "l2 pool"),
    ("l2_parent_mismatch",
     lambda rows: [{**rows[0], "l1": "金融", "l2": "超大规模云"}] + rows[1:],
     "parent mismatch"),
    ("invalid_l3",
     lambda rows: [{**rows[0], "l3_themes": "电商生态"}] + rows[1:],
     "not in pool"),
])
def test_read_reviewed_csv_fail_fast(tmp_path, scenario, mutations, expected_msg):
    csv_path = _write_valid_csv(tmp_path)
    rows = _read_csv(csv_path)
    mutated = mutations(rows)
    _write_csv(csv_path, mutated)

    from scripts.build_company_concept_registry import (
        read_reviewed_csv, CSVValidationError,
    )
    with pytest.raises(CSVValidationError) as exc:
        read_reviewed_csv(csv_path, extend_pool=_EXTEND_POOL, taxonomy=_TAXONOMY)
    assert expected_msg in str(exc.value).lower(), (
        f"scenario={scenario} expected '{expected_msg}' in message: {exc.value}"
    )


def test_read_reviewed_csv_validate_only_emits_per_row_report(tmp_path):
    """rejected.csv has one row per failing symbol, errors aggregated into _errors col."""
    csv_path = _write_csv_with_2_bad_rows(tmp_path)
    from scripts.build_company_concept_registry import read_reviewed_csv
    read_reviewed_csv(csv_path, extend_pool={"NVDA", "AMD"},
                      taxonomy=_TAXONOMY, validate_only=True)

    rejected_path = csv_path.parent / f"{csv_path.stem}_rejected.csv"
    summary_path = csv_path.parent / f"{csv_path.stem}_rejected_summary.txt"
    assert rejected_path.exists()
    assert summary_path.exists()

    rejected_rows = list(csv.DictReader(rejected_path.open()))
    assert len(rejected_rows) == 2
    for r in rejected_rows:
        assert r["_errors"], "_errors must be populated"


def test_read_reviewed_csv_one_row_can_have_multiple_errors(tmp_path):
    """A row violating 3 checks emits ONE rejected row with all 3 in _errors (' | ' sep)."""
    csv_path = _write_single_row_with_three_errors(tmp_path)
    from scripts.build_company_concept_registry import read_reviewed_csv
    read_reviewed_csv(csv_path, extend_pool={"FOO"}, taxonomy=_TAXONOMY,
                      validate_only=True)
    rejected = list(csv.DictReader(
        (csv_path.parent / f"{csv_path.stem}_rejected.csv").open()
    ))
    assert len(rejected) == 1
    errs = rejected[0]["_errors"].split(" | ")
    assert len(errs) >= 3, f"expected 3+ errors, got: {errs}"


# ---- Phase 6 helpers ----


def _bootstrap_store_with_v2_taxonomy(tmp_path: Path):
    """MarketStore seeded with the v2 concepts needed by Phase 6 tests."""
    from src.data.market_store import MarketStore
    store = MarketStore(tmp_path / "market.db")
    store.upsert_concepts([
        {"concept_id": "semiconductor", "label": "半导体",
         "level": 1, "parent_id": None},
        {"concept_id": "consumer_retail", "label": "消费与零售",
         "level": 1, "parent_id": None},
        {"concept_id": "gpu_accelerator", "label": "计算芯片/GPU加速器",
         "level": 2, "parent_id": "semiconductor"},
        {"concept_id": "consumer_staples", "label": "必需消费品",
         "level": 2, "parent_id": "consumer_retail"},
        {"concept_id": "ai_compute", "label": "AI算力", "level": 3,
         "concept_type": "theme"},
        {"concept_id": "hbm", "label": "HBM", "level": 3,
         "concept_type": "theme"},
    ])
    # Expose the db_path so save_to_market_db can use it.
    store.db_path = tmp_path / "market.db"
    return store


# ---- Task 8: Phase 6 save (backup + 3-segment display_tags + level=3 guard) ----


def test_phase6_save_writes_three_segment_display_tags(tmp_path):
    """display_tags = L1_label / L2_label / L3_first_label."""
    store = _bootstrap_store_with_v2_taxonomy(tmp_path)
    rows = [{
        "symbol": "NVDA",
        "primary_concept_id": "semiconductor",
        "secondary_concept_id": "gpu_accelerator",
        "theme_ids": ["ai_compute", "hbm"],
        "business_role": "GPU",
        "confidence": 0.95,
        "source": "manual",
        "needs_review": 0,
    }]
    from scripts.build_company_concept_registry import save_to_market_db
    save_to_market_db(rows=rows, store=store, market_db_path=store.db_path)

    fetched = store.get_company_concepts(["NVDA"])
    assert fetched["NVDA"]["display_tags"] == "半导体 / 计算芯片/GPU加速器 / AI算力"


def test_phase6_display_tags_two_segment_when_no_l3(tmp_path):
    store = _bootstrap_store_with_v2_taxonomy(tmp_path)
    rows = [{
        "symbol": "KO",
        "primary_concept_id": "consumer_retail",
        "secondary_concept_id": "consumer_staples",
        "theme_ids": [],
        "business_role": "",
        "confidence": 0.95,
        "source": "manual",
        "needs_review": 0,
    }]
    from scripts.build_company_concept_registry import save_to_market_db
    save_to_market_db(rows=rows, store=store, market_db_path=store.db_path)
    fetched = store.get_company_concepts(["KO"])
    assert fetched["KO"]["display_tags"] == "消费与零售 / 必需消费品"


def test_phase6_save_does_not_backup_in_isolation(tmp_path):
    """save_to_market_db is a low-level upsert helper; it must NOT back up the
    DB on its own. Backups belong at the rebuild boundary (see
    ``apply_reviewed_csv`` / ``build_registry``) — backing up here would either
    duplicate the caller's backup or, worse, capture an already-cleared DB.
    """
    store = _bootstrap_store_with_v2_taxonomy(tmp_path)
    from scripts.build_company_concept_registry import save_to_market_db
    save_to_market_db(rows=[], store=store, market_db_path=store.db_path)
    backups = list(tmp_path.glob("market.db.backup-*"))
    assert backups == [], (
        "save_to_market_db must not produce backups — callers do that "
        "BEFORE rebuild_concept_tree clears the DB"
    )


def test_backup_sqlite_captures_wal_committed_writes(tmp_path):
    """WAL invariant: committed writes must appear in the backup even without
    an explicit checkpoint. shutil.copy2 fails this (may miss -wal sidecar);
    sqlite3.Connection.backup() coordinates with WAL and produces a clean
    target file.
    """
    import sqlite3
    db = tmp_path / "live.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.execute("INSERT INTO t VALUES ('committed_then_backup')")
    conn.commit()
    # Do NOT checkpoint — simulate real-world -wal still containing data.

    from scripts.build_company_concept_registry import _backup_sqlite
    backup = _backup_sqlite(db, "wal-test")
    assert backup is not None

    bconn = sqlite3.connect(str(backup))
    rows = bconn.execute("SELECT k FROM t").fetchall()
    bconn.close()
    conn.close()
    assert rows == [("committed_then_backup",)]


# ---- Task 11: legacy taxonomy.json + concept_themes.json are dead code ----


def test_legacy_taxonomy_jsons_not_imported_by_live_code():
    """v2 production code (terminal/, scripts/, src/) MUST NOT reference the
    legacy taxonomy.json or concept_themes.json files. Only concept_taxonomy_v2.json
    is allowed. This is a static grep so a regression (someone re-adds an import)
    fails loudly."""
    import subprocess
    roots = [PROJECT_ROOT / "terminal", PROJECT_ROOT / "scripts", PROJECT_ROOT / "src"]
    hits: list[str] = []
    for r in roots:
        if not r.exists():
            continue
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py",
             r"taxonomy\.json\|concept_themes\.json", str(r)],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            # Allow concept_taxonomy_v2.json reference; reject the bare names.
            if "concept_taxonomy_v2.json" in line:
                continue
            hits.append(line)
    assert not hits, (
        "Legacy taxonomy.json / concept_themes.json references still live in "
        "terminal/scripts/src:\n" + "\n".join(hits)
    )


def test_v2_builder_runs_without_legacy_jsons(tmp_path, monkeypatch):
    """ConceptRegistry must only require concept_taxonomy_v2.json. We delete
    the legacy taxonomy.json + concept_themes.json from a staging dir and
    confirm build_registry still classifies + writes CSV without raising."""
    import shutil
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry
    from scripts.build_company_concept_registry import build_registry

    cfg_src = PROJECT_ROOT / "config" / "concepts"
    cfg_dst = tmp_path / "config" / "concepts"
    cfg_dst.mkdir(parents=True)
    # Copy only the files v2 actually needs.
    shutil.copy(cfg_src / "concept_taxonomy_v2.json", cfg_dst)
    # Intentionally do NOT copy taxonomy.json or concept_themes.json.
    assert not (cfg_dst / "taxonomy.json").exists()
    assert not (cfg_dst / "concept_themes.json").exists()

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(
        taxonomy_path=cfg_dst / "concept_taxonomy_v2.json",
        watchlist_path=None,
    )
    profiles = {
        # industry_map rule hit (Industrials|Aerospace & Defense) →
        # deterministic, no LLM spawn — keeps this test offline.
        "NVDA": {"symbol": "NVDA", "sector": "Industrials",
                 "industry": "Aerospace & Defense",
                 "description": "aerospace & defense systems"},
    }
    # Must not raise FileNotFoundError on the missing legacy files.
    build_registry(
        store=store, registry=registry,
        universe_symbols=["NVDA"],
        profiles=profiles,
        portfolio_holdings=[],
        broad_top_symbols=["NVDA"],
        review_csv_path=tmp_path / "review.csv",
        save=False, force_save=False,
    )
    assert (tmp_path / "review.csv").exists()


def test_read_reviewed_csv_coverage_errors_go_to_summary_not_rows(tmp_path):
    """Missing symbols (coverage-level) appear in summary.txt, NOT per-row rejected.csv."""
    csv_path = _write_csv_with_one_good_row(tmp_path, symbol="AAPL")
    # Tweak AAPL row to use a valid (consumer_retail, consumer_electronics_brand)
    rows = _read_csv(csv_path)
    rows[0]["l1"] = "消费与零售"
    rows[0]["l2"] = "消费电子与品牌硬件"
    rows[0]["l3_themes"] = ""
    _write_csv(csv_path, rows)

    from scripts.build_company_concept_registry import read_reviewed_csv
    read_reviewed_csv(csv_path, extend_pool={"AAPL", "MSFT", "NVDA"},
                      taxonomy=_TAXONOMY, validate_only=True)
    rejected = list(csv.DictReader(
        (csv_path.parent / f"{csv_path.stem}_rejected.csv").open()
    ))
    assert rejected == []     # no per-row failures
    summary = (csv_path.parent / f"{csv_path.stem}_rejected_summary.txt").read_text(
        encoding="utf-8"
    )
    assert "MSFT" in summary and "NVDA" in summary


# ---- --reclassify: re-run classify over a run-1 review CSV (plan 2026-05-16) ----


def _build_registry_v2():
    from terminal.company_concepts import ConceptRegistry
    return ConceptRegistry(taxonomy_path=TAXONOMY_V2_PATH, watchlist_path=None)


def _reclassify_row(symbol, prefill_source, fmp_sector, fmp_industry,
                    l1="", l2=""):
    """One CSV row for the --reclassify input fixture (16-col schema)."""
    return {
        "review_reason": "ok", "symbol": symbol,
        "company_name": f"{symbol} Corp", "fmp_sector": fmp_sector,
        "fmp_industry": fmp_industry, "market_cap_b": "12.34",
        "mcap_tier": "small", "description": f"{symbol} description",
        "l1": l1, "l2": l2, "l3_themes": "", "business_role": "",
        "prefill_source": prefill_source, "confidence": "0.70",
        "needs_review": "0", "boss_notes": "",
    }


def test_reclassify_csv_routes_by_old_source(tmp_path):
    """--reclassify routes each row by its OLD prefill_source (plan §3.6):
    manual → passthrough; rule → overwrite or fresh-LLM; llm → whitelist
    overwrite / deterministic_conflict / passthrough."""
    from scripts.build_company_concept_registry import reclassify_csv

    rows = [
        # manual anchor → always passthrough
        _reclassify_row("MANUALCO", "manual", "Financial Services",
                        "Insurance - Diversified", l1="金融", l2="保险"),
        # old rule, industry in map → overwrite. Old l1/l2 here is the
        # issue-025 bug (an electric utility misrouted to 新能源).
        _reclassify_row("RULEHIT", "rule", "Utilities", "Regulated Electric",
                        l1="能源与材料", l2="新能源"),
        # old rule, ambiguous industry not in map → fresh LLM
        _reclassify_row("RULEAMBIG", "rule", "Technology", "Semiconductors",
                        l1="半导体", l2="模拟与功率"),
        # old llm, Telecommunications Services ∈ whitelist → overwrite
        _reclassify_row("TELECOM", "llm", "Communication Services",
                        "Telecommunications Services",
                        l1="互联网与软件", l2="流媒体与内容"),
        # old llm, non-whitelist, new map disagrees → deterministic_conflict
        _reclassify_row("CONFLICT", "llm", "Real Estate", "REIT - Specialty",
                        l1="地产与公用", l2="住宅与商业REIT"),
        # old llm, non-whitelist, consistent with new map → passthrough
        _reclassify_row("LLMOK", "llm", "Real Estate", "REIT - Office",
                        l1="地产与公用", l2="住宅与商业REIT"),
        # old llm_failed → passthrough
        _reclassify_row("LLMFAIL", "llm_failed", "Unknown", "Mystery"),
    ]
    in_csv = tmp_path / "run1.csv"
    _write_csv(in_csv, rows)
    out_csv = tmp_path / "run2.csv"

    registry = _build_registry_v2()
    fresh = _fake_llm(l1="semiconductor", l2="analog_power", source="llm",
                      confidence=0.8, needs_review=0)
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=fresh,
    ) as mocked:
        stats = reclassify_csv(
            input_csv=in_csv, output_csv=out_csv,
            registry=registry, profiles={},
            taxonomy=registry._taxonomy,
        )

    # Fresh LLM is called exactly once — only the ambiguous old-rule row.
    assert mocked.call_count == 1
    assert mocked.call_args_list[0].kwargs["symbol"] == "RULEAMBIG"

    assert stats["total"] == 7
    assert stats["overwrite_rule"] == 1
    assert stats["fresh_llm"] == 1
    assert stats["overwrite_llm_whitelist"] == 1
    assert stats["deterministic_conflict"] == 1
    assert stats["passthrough"] == 3        # manual + llmok + llmfail
    assert stats["whitelist_symbols"] == ["TELECOM"]
    assert stats["conflict_symbols"] == ["CONFLICT"]

    out = {r["symbol"]: r for r in _read_csv(out_csv)}
    assert len(out) == 7
    # RULEHIT: the issue-025 bug row is corrected to the deterministic result.
    assert out["RULEHIT"]["l1"] == "地产与公用"
    assert out["RULEHIT"]["l2"] == "电力与公用"
    assert out["RULEHIT"]["prefill_source"] == "rule"
    # TELECOM: whitelist overwrite → the new telecom_operator L2.
    assert out["TELECOM"]["l2"] == "电信运营商"
    assert out["TELECOM"]["prefill_source"] == "rule"
    # CONFLICT: old llm row kept verbatim, only flagged for Boss review.
    assert out["CONFLICT"]["review_reason"] == "deterministic_conflict"
    assert out["CONFLICT"]["l1"] == "地产与公用"
    assert out["CONFLICT"]["l2"] == "住宅与商业REIT"
    assert out["CONFLICT"]["prefill_source"] == "llm"
    # MANUAL anchor row untouched.
    assert out["MANUALCO"]["prefill_source"] == "manual"
    assert out["MANUALCO"]["l2"] == "保险"
    # RULEAMBIG: fresh LLM result rendered in.
    assert out["RULEAMBIG"]["prefill_source"] == "llm"
    assert out["RULEAMBIG"]["l2"] == "模拟与功率"  # analog_power label


def test_reclassify_csv_writes_full_manifest(tmp_path):
    """The output manifest records ALL symbols (incl passthrough) so
    apply_reviewed_csv's coverage union does not miss them (plan §3.7)."""
    from scripts.build_company_concept_registry import reclassify_csv

    rows = [
        _reclassify_row("MANUALCO", "manual", "Financial Services",
                        "Insurance - Diversified", l1="金融", l2="保险"),
        _reclassify_row("LLMFAIL", "llm_failed", "Unknown", "Mystery"),
    ]
    in_csv = tmp_path / "run1.csv"
    _write_csv(in_csv, rows)
    out_csv = tmp_path / "run2.csv"

    registry = _build_registry_v2()
    reclassify_csv(
        input_csv=in_csv, output_csv=out_csv,
        registry=registry, profiles={}, taxonomy=registry._taxonomy,
    )
    manifest = json.loads(
        (out_csv.parent / f"{out_csv.stem}_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(manifest["symbols"]) == {"MANUALCO", "LLMFAIL"}
