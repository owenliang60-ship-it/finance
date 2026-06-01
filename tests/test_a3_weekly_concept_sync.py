import csv
from pathlib import Path
from scripts.build_company_concept_registry import (
    _read_csv_symbols, _normalize_review_csv, _append_csv_atomic, REVIEW_CSV_FIELDS,
)


def _write(p: Path, header: list[str], rows: list[list[str]]):
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header)
        for r in rows: w.writerow(r)


def test_normalize_dedups_duplicate_business_role(tmp_path):
    # legacy 17-col header: business_role at pos 2 AND pos 11
    header = ["review_reason", "symbol", "business_role", "company_name", "fmp_sector",
              "fmp_industry", "market_cap_b", "mcap_tier", "description", "l1", "l2",
              "l3_themes", "business_role", "prefill_source", "confidence", "needs_review", "boss_notes"]
    row = ["ok", "AAA", "", "Co", "Tech", "Semis", "12.0", "small", "desc", "信息技术",
           "半导体", "存储", "代工", "rule", "0.70", "0", ""]
    src = tmp_path / "legacy.csv"; _write(src, header, [row])
    dst = tmp_path / "canon.csv"
    assert _normalize_review_csv(src, dst) == 1
    out = list(csv.DictReader(dst.open(encoding="utf-8")))
    assert list(out[0].keys()) == REVIEW_CSV_FIELDS          # 16 unique fields
    assert out[0]["business_role"] == "代工"                  # coalesced non-empty (pos 11)


def test_append_csv_atomic_normalizes(tmp_path):
    canon = tmp_path / "canon.csv"
    _write(canon, REVIEW_CSV_FIELDS, [["ok", "AAA"] + [""] * 14])
    _append_csv_atomic(canon, [{"symbol": "BBB", "l1": "x"}])
    assert _read_csv_symbols(canon) == {"AAA", "BBB"}


# ---- Task 2: weekly_sync drift detection + classify split ----

import scripts.build_company_concept_registry as b


def _fake_registry(): return object()


def _run_ws(tmp_path, monkeypatch, *, base_syms, uni_syms, classify_fn, sent):
    canon = tmp_path / "canon.csv"
    canon.write_text("symbol\n" + "\n".join(base_syms) + "\n", encoding="utf-8")
    uni = tmp_path / "uni.json"
    import json as _j
    uni.write_text(_j.dumps({"symbols": uni_syms}), encoding="utf-8")
    return b.weekly_sync(
        registry=_fake_registry(), taxonomy={"concepts": []},
        canonical_csv=canon, extended_universe_path=uni,
        profiles_path=tmp_path / "prof.json", market_db_path=tmp_path / "m.db",
        queue_dir=tmp_path, run_date="2026-06-01",
        classify_fn=classify_fn, refresh_fn=lambda syms, profiles_path: 0,
        store_factory=None,                                    # skip persist (Task 2 scope)
        telegram_fn=lambda text, channel: sent.append((text, channel)))


def test_weekly_sync_splits_and_always_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "_load_profiles",
        lambda p: {"RULEX": {"sector": "Tech", "industry": "Semis"},
                   "LLMY": {"sector": "X", "industry": "Y"}})

    def fake_classify(reg, profile, tax):
        sym = profile["symbol"]
        if sym == "RULEX":
            return {"symbol": sym, "source": "rule", "l1": "l1_tech", "l2": "l2_semis", "l3_themes": []}
        return {"symbol": sym, "source": "llm", "l1": "l1_x", "l2": "l2_y", "l3_themes": [], "needs_review": 1}

    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"],
                  uni_syms=["AAA", "RULEX", "LLMY"], classify_fn=fake_classify, sent=sent)
    assert res.auto_saved == ["RULEX"] and res.queued == ["LLMY"] and res.error is None
    assert sent and sent[0][1] == "group"                      # D2: telegram fired


def test_weekly_sync_anchor_without_profile_auto_saves(tmp_path, monkeypatch):
    """P1.5: anchor matched by SYMBOL even with empty profile → deterministic."""
    monkeypatch.setattr(b, "_load_profiles", lambda p: {})     # no profile at all
    fake = lambda reg, prof, tax: {"symbol": prof["symbol"], "source": "manual",
                                   "l1": "l1_a", "l2": "l2_b", "l3_themes": []}
    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"], uni_syms=["AAA", "ANCHORX"],
                  classify_fn=fake, sent=sent)
    assert res.auto_saved == ["ANCHORX"] and res.failed == []


def test_weekly_sync_classify_error_becomes_failed_artifact(tmp_path, monkeypatch):
    """P1.4: classify raising → failed bucket + a review row carried for the queue CSV."""
    monkeypatch.setattr(b, "_load_profiles", lambda p: {"BADX": {"sector": "Z", "industry": "Q"}})
    def boom(reg, prof, tax): raise RuntimeError("LLM down")
    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"], uni_syms=["AAA", "BADX"],
                  classify_fn=boom, sent=sent)
    assert res.failed == ["BADX"] and res.error is None         # caught, not fatal
    assert any(r["symbol"] == "BADX" for r in res._failed_rows)
    assert sent and sent[0][1] == "group"


def test_weekly_sync_no_drift_still_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "_load_profiles", lambda p: {})
    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"], uni_syms=["AAA"],
                  classify_fn=lambda *a: {}, sent=sent)
    assert res.drift_in == [] and res.auto_saved == []
    assert sent and "无新增" in sent[0][0] and sent[0][1] == "group"   # D2: even no-drift pushes
