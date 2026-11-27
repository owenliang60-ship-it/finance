"""Neon Step-5 independent audit tests for M3 (scripts/theme_basket_sync.py).

Targets three real-world defects caught during Step-5 real-data smoke & code audit:
1. Sub-$10B US stocks in watchlist/Sheet (e.g. GRAL, IREN) have neither security_master
   nor company_profile rows in market.db, and must be classified as ('us_equity', 1)
   so Step B labels them — while placeholder 'TEST' stays ('other', 0) and common US
   ETFs not in security_master/report_concepts (e.g. TQQQ from 'TQQQ PUT', IBIT, GLD)
   become ('etf', 0).
2. Step C commit_outbox & format_weekly_summary must preserve D4 pivot_notes / pivot
   symbols and surface unclassified_symbols when Step B submits a strict subset of
   missing_symbols (and GateError on out-of-scope pivot_notes).
3. scan_sheet_tickers_tolerant must accept comma-formatted string shares ('1,250') and
   reject non-finite float('inf') / float('nan') shares.
"""
import io
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import openpyxl
import pytest

from scripts import theme_basket_sync as sync
from src.data import theme_basket_store as store


def _make_workbook_bytes(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary_OSV"
    ws.append(["Stock Ticker", "Market", "Shares", "Last Price"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def test_unprofiled_real_us_watchlist_stock_is_us_equity_not_other():
    """GRAL ($4.9B NASDAQ watchlist stock) has sm_row=None and profile_meta=None."""
    assert sync.classify_asset_kind(
        "GRAL", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("us_equity", 1)
    assert sync.classify_asset_kind(
        "TEST", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("other", 0)
    assert sync.classify_asset_kind(
        "TQQQ", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("etf", 0)
    assert sync.classify_asset_kind(
        "IBIT", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("etf", 0)


def test_scan_sheet_handles_comma_shares_and_rejects_nonfinite():
    xlsx = _make_workbook_bytes([
        ("NASDAQ:NVDA", "US", "1,250", "#N/A"),
        ("TQQQ PUT", "US", "10", "#N/A"),
        ("BADINF", "US", float("inf"), 10.0),
        ("BADNAN", "US", float("nan"), 10.0),
        ("CLOSED", "US", "0", 10.0),
    ])
    got = sync.scan_sheet_tickers_tolerant(xlsx)
    assert got == [("NASDAQ:NVDA", "US"), ("TQQQ PUT", "US")]


def test_commit_outbox_surfaces_pivot_notes_and_unclassified_in_telegram(tmp_path):
    """D4 pivot advice + partial missing_symbols subset must reach cloud Telegram summary."""
    repo_root = Path(__file__).resolve().parents[1]
    baskets_path = repo_root / "config" / "theme_baskets" / "baskets.json"
    overrides_path = repo_root / "config" / "theme_baskets" / "overrides.json"
    exchange_dir = tmp_path / "exchange"
    dirs = sync.ensure_exchange_dirs(exchange_dir)

    today = "2026-11-27"
    delta = {
        "schema_version": 1,
        "generated_date": today,
        "generated_at": "2026-11-27T01:50:00Z",
        "market_db_status": "OK",
        "sheet_status": "OK",
        "warnings": [],
        "counts": {
            "candidates": 3,
            "existing": 1,
            "missing_symbols": 2,
            "auto_rows": 0,
            "pivot_candidates": 1,
        },
        "missing_symbols": [
            {
                "symbol": "GRAL",
                "cik": None,
                "asset_kind": "us_equity",
                "in_us_basket_calc": 1,
                "profile_sector": None,
                "profile_industry": None,
                "company_name_snapshot": None,
                "sources": ["overlay"],
            },
            {
                "symbol": "SKIPME",
                "cik": None,
                "asset_kind": "us_equity",
                "in_us_basket_calc": 1,
                "profile_sector": None,
                "profile_industry": None,
                "company_name_snapshot": None,
                "sources": ["overlay"],
            },
        ],
        "auto_rows": [],
        "pivot_candidates": [
            {
                "symbol": "PIVOT1",
                "old_profile_sector": "Consumer Cyclical",
                "old_profile_industry": "Retail",
                "new_profile_sector": "Technology",
                "new_profile_industry": "Cloud Computing",
            }
        ],
    }
    delta["delta_sha256"] = sync.compute_delta_sha256(delta)
    sync.atomic_write_json(dirs["inbox"] / "delta.json", delta)

    # First verify out-of-scope pivot_notes symbol is rejected by hard gate
    bad_payload = {
        "schema_version": 1,
        "generated_date": today,
        "delta_sha256": delta["delta_sha256"],
        "classifications": [],
        "pivot_notes": [{"symbol": "NOT_IN_PIVOTS", "note": "spoofed"}],
    }
    sync.atomic_write_json(dirs["outbox"] / "payload.json", bad_payload)
    rc_bad = sync.commit_outbox(
        exchange_dir=exchange_dir,
        baskets_path=baskets_path,
        overrides_path=overrides_path,
        market_db_path=tmp_path / "dummy_local.db",
        today=today,
        ssh_runner=lambda *a, **k: pytest.fail("gate should block before SSH"),
        skip_pull=True,
    )
    assert rc_bad == sync.EXIT_FAIL

    # Now write valid payload classifying GRAL, omitting SKIPME, and advising on PIVOT1
    good_payload = {
        "schema_version": 1,
        "generated_date": today,
        "delta_sha256": delta["delta_sha256"],
        "classifications": [
            {
                "symbol": "GRAL",
                "l2_id": "life_sci_tools_diagnostics",
                "cross_themes": [],
                "pi_bucket": "pi_pharma_bio",
                "business_note": "Liquid biopsy cancer screening",
                "agreement_status": "single_pass_weekly",
            }
        ],
        "pivot_notes": [
            {"symbol": "PIVOT1", "note": "建议复核是否转入云基础设施赛道"}
        ],
    }
    sync.atomic_write_json(dirs["outbox"] / "payload.json", good_payload)

    cloud_db = tmp_path / "cloud_market.db"
    lock_path = tmp_path / "writer.lock"
    captured_messages = []

    def fake_ssh(remote_args, stdin_bytes=None):
        assert remote_args == ["--apply-cloud-stdin"]
        env = json.loads(stdin_bytes.decode("utf-8"))
        rc, rep = sync.apply_cloud_envelope(
            env,
            db_path=cloud_db,
            lock_path=lock_path,
            allow_noncloud=True,
            send_telegram_fn=lambda msg, channel="private": captured_messages.append((channel, msg)),
        )
        return rc, json.dumps(rep).encode("utf-8"), b""

    rc_ok = sync.commit_outbox(
        exchange_dir=exchange_dir,
        baskets_path=baskets_path,
        overrides_path=overrides_path,
        market_db_path=tmp_path / "dummy_local.db",
        today=today,
        ssh_runner=fake_ssh,
        send_telegram=True,
        skip_pull=True,
    )
    assert rc_ok == sync.EXIT_OK
    assert len(captured_messages) == 1
    channel, text = captured_messages[0]
    assert channel == "private"
    assert "本地隔离 0" in text
    assert "SKIPME" in text
    assert "PIVOT1" in text
    assert "建议复核是否转入云基础设施赛道" in text

    import sqlite3
    with sqlite3.connect(str(cloud_db)) as conn:
        gral = conn.execute(
            "SELECT l2_id, pi_bucket, source FROM stock_themes WHERE symbol = 'GRAL'"
        ).fetchone()
    assert gral == ("life_sci_tools_diagnostics", "pi_pharma_bio", "agy_llm")


# ================================================================ Codex review #1-#12 (Boss 2026-11-27)

REPO = Path(__file__).resolve().parents[1]
BASKETS = REPO / "config" / "theme_baskets" / "baskets.json"
OVERRIDES = REPO / "config" / "theme_baskets" / "overrides.json"
ALIASES = REPO / "config" / "symbol_aliases.json"
DAY = "2026-11-29"
T0 = datetime(2026, 11, 29, 2, 0, 0, tzinfo=timezone.utc)
LOCK_BUSY = (75, {"ok": False, "error": "lock_busy"})
DEFAULT_SM = (("AAAX", "0009990001", 0), ("BBBX", "0009990002", 0), ("ZZETF", "0009990003", 1))

_MARKET_DDL = """
CREATE TABLE security_master (
    symbol TEXT PRIMARY KEY, cik TEXT, company_name TEXT, exchange TEXT,
    is_etf INTEGER NOT NULL DEFAULT 0, is_fund INTEGER NOT NULL DEFAULT 0,
    is_adr INTEGER NOT NULL DEFAULT 0, share_class_of TEXT,
    eligible INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE extended_membership (
    symbol TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
    reason TEXT NOT NULL DEFAULT 'screener', PRIMARY KEY (symbol, effective_from)
);
CREATE TABLE company_profile (symbol TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def _baskets_doc():
    return json.loads(BASKETS.read_text(encoding="utf-8"))


def _first_l2():
    return sorted(b["basket_id"] for b in _baskets_doc()["baskets"] if b["tier"] == "l2")[0]


def _local_market(tmp_path, *, sm=DEFAULT_SM, profiles=(), extended=None):
    """sm: (symbol, cik, is_etf); profiles: (symbol, cik); extended defaults to every sm symbol."""
    path = tmp_path / "local-market.db"
    with closing(sqlite3.connect(str(path))) as c:
        c.executescript(_MARKET_DDL)
        c.executemany(
            "INSERT INTO security_master VALUES (?,?,?,'NASDAQ',?,0,0,NULL,?,?,'2026-11-21T01:00:00Z')",
            [(s, cik, f"{s} Inc.", etf, 0 if etf else 1, "etf" if etf else "ok") for s, cik, etf in sm])
        ext = [s for s, _, _ in sm] if extended is None else list(extended)
        c.executemany("INSERT INTO extended_membership (symbol, effective_from) VALUES (?, '2026-08-21')",
                      [(s,) for s in ext])
        c.executemany("INSERT INTO company_profile VALUES (?, ?, '2026-11-21')", [
            (s, json.dumps({"symbol": s, "cik": cik, "companyName": f"{s} Inc.", "sector": "Technology",
                            "industry": "Software", "isEtf": False, "isFund": False}))
            for s, cik in profiles])
        c.commit()
    return path


def _company(tmp_path):
    path = tmp_path / "company.db"
    with closing(sqlite3.connect(str(path))) as c:
        c.execute("CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY, source TEXT DEFAULT '', "
                  "added_at TEXT NOT NULL)")
        c.commit()
    return path


def _detect(tmp_path, market, *, baskets=BASKETS, **kw):
    kw.setdefault("skip_sheet", True)
    return sync.detect_delta(market_db_path=market, company_db_path=_company(tmp_path),
                             exchange_dir=tmp_path / "xchg", baskets_path=baskets, overrides_path=OVERRIDES,
                             aliases_path=ALIASES, today=DAY, auto_pull_stale=False, **kw)


def _cls(symbol, **kw):
    item = {"symbol": symbol, "l2_id": _first_l2(), "cross_themes": [], "business_note": f"{symbol} note",
            "agreement_status": "single_pass_weekly"}
    item.update(kw)
    return item


def _outbox(tmp_path, delta, classifications):
    sync.atomic_write_json(tmp_path / "xchg" / "outbox" / "payload.json",
                           {"delta_sha256": delta["delta_sha256"], "classifications": classifications})


def _pending(tmp_path):
    return sorted((tmp_path / "xchg" / "pending").glob("*.json"))


class _Cloud:
    """Fake SSH: scripted (rc, reply) steps first, then a real apply_cloud_envelope on a tmp cloud db."""

    def __init__(self, tmp_path, script=(), on_call=None):
        self.db = tmp_path / "cloud" / "market.db"
        self.db.parent.mkdir(exist_ok=True)
        self.lock = tmp_path / "writer.lock"
        self.script, self.on_call, self.calls = list(script), on_call, []

    def __call__(self, remote_args, stdin_bytes=None):
        env = json.loads(stdin_bytes.decode("utf-8"))
        self.calls.append(env)
        if self.on_call is not None:
            self.on_call()
        if self.script:
            rc, reply = self.script.pop(0)
        else:  # one second per call: backup names have 1 s resolution
            rc, reply = sync.apply_cloud_envelope(
                env, db_path=self.db, lock_path=self.lock, allow_noncloud=True,
                send_telegram_fn=lambda *a, **k: None, now=T0 + timedelta(seconds=len(self.calls)))
        return rc, json.dumps(reply).encode("utf-8"), b""

    def rows(self):
        if not self.db.exists():
            return {}
        with closing(sqlite3.connect(str(self.db))) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'stock_themes'").fetchone():
                return {}
            return {r[0]: r[1:] for r in c.execute(
                "SELECT symbol, in_us_basket_calc, is_superseded, superseded_by, cik, business_note "
                "FROM stock_themes")}


def _commit(tmp_path, cloud, *, baskets=BASKETS):
    return sync.commit_outbox(exchange_dir=tmp_path / "xchg", baskets_path=baskets, overrides_path=OVERRIDES,
                              market_db_path=tmp_path / "local-market.db", today=DAY, ssh_runner=cloud,
                              send_telegram=False, skip_pull=True)


def _last_json(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _store_row(symbol, note):
    return {"symbol": symbol, "asset_kind": "us_equity", "in_us_basket_calc": 1, "l2_id": _first_l2(),
            "pi_bucket": "pi_other", "agreement_status": "single_pass_weekly", "cross_themes": [],
            "business_note": note}


def _envelope(rows):
    rows = [dict(r) for r in rows]
    return sync.build_envelope(rows=rows, payload_id=store.compute_payload_id(rows), baskets_doc=_baskets_doc(),
                               overrides_doc=json.loads(OVERRIDES.read_text(encoding="utf-8")),
                               send_telegram=False)


# ---------------------------------------------------------------- #1 local company.db / prosperity.db

@pytest.mark.parametrize("env, allow_noncloud", [("local", True), ("cloud", False)])
@pytest.mark.parametrize("target", [
    sync.DEFAULT_COMPANY_DB,
    sync.main_repo_root() / "data" / "company.db",
    sync.REPO_ROOT / "data" / "prosperity.db",
    sync.main_repo_root() / "data" / "prosperity.db",
])
def test_writer_refuses_company_and_prosperity_db_in_any_env(target, env, allow_noncloud, tmp_path,
                                                            monkeypatch):
    """Theme tables never go into company.db / prosperity.db, not even with FINANCE_ENV=cloud.

    Sentinels make a missing guard fail loudly without ever opening the real file.
    """
    monkeypatch.setenv("FINANCE_ENV", env)

    def sentinel(*args, **kwargs):
        raise AssertionError("must refuse before touching the target db")

    monkeypatch.setattr(sync, "_needs_backup", sentinel)
    monkeypatch.setattr(sync, "backup_sqlite_wal_safe", sentinel)
    monkeypatch.setattr(store, "open_writer", sentinel)

    def fingerprint():
        st = target.stat() if target.exists() else None
        return (st.st_size, st.st_mtime_ns) if st else None

    before = fingerprint()
    rc, res = sync.apply_cloud_envelope(_envelope([]), db_path=target, lock_path=tmp_path / "w.lock",
                                        allow_noncloud=allow_noncloud, send_telegram_fn=sentinel)
    assert rc == sync.EXIT_FAIL and res["error"].startswith("refusing")
    rc, res = sync.cloud_private_lock(db_path=target, lock_path=tmp_path / "w.lock", action="unlock",
                                      symbol="PRIV1", allow_noncloud=allow_noncloud)
    assert rc == sync.EXIT_FAIL and res["error"].startswith("refusing")
    assert fingerprint() == before


# ---------------------------------------------------------------- #2 CIK leading zeros

def test_norm_cik():
    assert sync._norm_cik("123456") == "0000123456"
    assert sync._norm_cik(" 0000123456 ") == "0000123456"
    assert sync._norm_cik("0000") is None
    assert sync._norm_cik("  ") is None
    assert sync._norm_cik(None) is None
    assert sync._norm_cik("1") == "0000000001"
    assert sync._norm_cik("0001045810") == "0001045810"
    for bad in ("ABC12", "N/A", "-", "12345678901", "\uff11\uff12\uff13", "12 34"):
        assert sync._norm_cik(bad) is None, bad


def test_non_numeric_cik_never_dedupes_two_companies(tmp_path):
    market = _local_market(tmp_path, sm=[("NAAA", "N/A", 0), ("NBBB", "N/A", 0)])
    delta = _detect(tmp_path, market)
    missing = {m["symbol"]: m for m in delta["missing_symbols"]}
    assert {"NAAA", "NBBB"} <= set(missing)
    assert missing["NAAA"]["cik"] is None and missing["NBBB"]["cik"] is None
    assert not any(r["is_superseded"] for r in delta["auto_rows"])


def test_same_cik_with_and_without_leading_zeros_keeps_one_active_row(tmp_path):
    market = _local_market(tmp_path, sm=[("CIKA", "123456", 0)], profiles=[("CIKB", "0000123456")],
                           extended=["CIKA", "CIKB"])
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls(m["symbol"]) for m in delta["missing_symbols"]])
    cloud = _Cloud(tmp_path)
    assert _commit(tmp_path, cloud) == sync.EXIT_OK
    pair = {s: r for s, r in cloud.rows().items() if s in ("CIKA", "CIKB")}
    assert len(pair) == 2
    assert sum(r[0] for r in pair.values()) == 1
    assert sorted(r[1] for r in pair.values()) == [0, 1]
    assert {r[3] for r in pair.values()} == {"0000123456"}


# ---------------------------------------------------------------- #3 BTC / ETH US ETFs

def test_btc_eth_without_metadata_are_us_etfs_but_crypto_market_stays_crypto():
    for symbol in ("BTC", "ETH"):
        assert sync.classify_asset_kind(symbol, sm_row=None, profile_meta=None,
                                        extra_etfs=frozenset()) == ("etf", 0)
    inst = sync.normalize_instrument("BTC", "CRYPTO", as_of_date=DAY, aliases=[], supplemental_aliases=[],
                                     share_class_map={})
    assert sync.classify_asset_kind(inst.canonical_symbol, sm_row=None, profile_meta=None,
                                    extra_etfs=frozenset()) == ("crypto", 0)
    assert sync.classify_asset_kind("GRAL", sm_row=None, profile_meta=None,
                                    extra_etfs=frozenset()) == ("us_equity", 1)


# ---------------------------------------------------------------- #4 claim outbox/payload.json

@pytest.mark.parametrize("script", [[], [LOCK_BUSY]], ids=["ok", "lock_busy"])
def test_commit_outbox_keeps_a_payload_written_during_ssh(tmp_path, script):
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX")])
    newer = tmp_path / "xchg" / "outbox" / "payload.json"
    second = {"delta_sha256": "f" * 64, "classifications": [], "marker": "second"}
    cloud = _Cloud(tmp_path, script=script, on_call=lambda: sync.atomic_write_json(newer, second))
    _commit(tmp_path, cloud)
    assert json.loads(newer.read_text(encoding="utf-8")) == second
    assert not list(newer.parent.glob(".inflight.*"))


@pytest.mark.parametrize("rehearsal_fails", [False, True], ids=["rehearsal_ok", "rehearsal_fails"])
def test_payload_written_during_rehearsal_survives(tmp_path, monkeypatch, rehearsal_fails):
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX")])
    outbox = tmp_path / "xchg" / "outbox"
    newer = outbox / "payload.json"
    second = {"delta_sha256": "e" * 64, "classifications": [], "marker": "written during rehearsal"}
    real = sync.rehearse

    def rehearse_and_race(*args, **kwargs):
        sync.atomic_write_json(newer, second)
        if rehearsal_fails:
            raise ValueError("dictionary broke")
        return real(*args, **kwargs)

    monkeypatch.setattr(sync, "rehearse", rehearse_and_race)
    cloud = _Cloud(tmp_path)
    rc = _commit(tmp_path, cloud)
    assert json.loads(newer.read_text(encoding="utf-8")) == second
    assert not list(outbox.glob(".inflight.*"))
    if rehearsal_fails:
        assert rc == sync.EXIT_FAIL and cloud.calls == []
        rejected = list(outbox.glob("rejected.*.json"))
        assert len(rejected) == 1
        assert json.loads(rejected[0].read_text(encoding="utf-8"))["classifications"][0]["symbol"] == "AAAX"
    else:
        assert rc == sync.EXIT_OK
        assert "AAAX" in cloud.rows()


def test_commit_outbox_without_payload_is_a_gate_error(tmp_path, capsys):
    sync.ensure_exchange_dirs(tmp_path / "xchg")
    assert _commit(tmp_path, _Cloud(tmp_path)) == sync.EXIT_FAIL
    assert _last_json(capsys)["error"].startswith("gate:")
    assert list((tmp_path / "xchg" / "outbox").iterdir()) == []


def test_detect_delta_keeps_inflight_claims(tmp_path):
    market = _local_market(tmp_path)
    outbox = tmp_path / "xchg" / "outbox"
    outbox.mkdir(parents=True)
    claim = outbox / ".inflight.1.2.json"
    claim.write_text("{}", encoding="utf-8")
    (outbox / "stale.json").write_text("{}", encoding="utf-8")
    _detect(tmp_path, market)
    assert claim.exists()
    assert not (outbox / "stale.json").exists()


# ---------------------------------------------------------------- #5 asset_kind_default_pi_bucket

@pytest.mark.parametrize("mutate", [
    lambda m: m.update(etf="pi_no_such_bucket"),
    lambda m: m.pop("other"),
    lambda m: m.update(etf="l1_ai_cloud_hw"),
], ids=["unknown_bucket", "missing_kind", "wrong_tier"])
def test_broken_asset_kind_default_pi_bucket_fails_before_ssh(tmp_path, mutate):
    doc = _baskets_doc()
    mutate(doc["asset_kind_default_pi_bucket"])
    bad = tmp_path / "baskets.json"
    bad.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    cloud = _Cloud(tmp_path)
    assert sync.sync_config(baskets_path=bad, overrides_path=OVERRIDES, market_db_path=tmp_path / "m.db",
                            ssh_runner=cloud, send_telegram=False, skip_pull=True) == sync.EXIT_FAIL
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market, baskets=bad)
    assert any(r["asset_kind"] == "etf" for r in delta["auto_rows"])
    _outbox(tmp_path, delta, [_cls("AAAX")])
    assert _commit(tmp_path, cloud, baskets=bad) == sync.EXIT_FAIL
    assert cloud.calls == []
    assert (tmp_path / "xchg" / "outbox" / "payload.json").exists()


# ---------------------------------------------------------------- #6 Sheet Market column

@pytest.mark.parametrize("ticker, market", [("BTC", ""), ("BTC", None), ("NVDA", "JP")])
def test_sheet_missing_or_unknown_market_marks_sheet_unknown(tmp_path, ticker, market):
    xlsx = _make_workbook_bytes([(ticker, market, 1, 10.0)])
    with pytest.raises(ValueError, match="Market"):
        sync.scan_sheet_tickers_tolerant(xlsx)
    delta = _detect(tmp_path, _local_market(tmp_path), skip_sheet=False, sheet_xlsx_fetcher=lambda: xlsx)
    assert delta["sheet_status"] == "UNKNOWN"
    assert ticker not in {r["symbol"] for r in delta["missing_symbols"] + delta["auto_rows"]}


def test_sheet_blank_market_is_inferred_from_an_explicit_prefix():
    xlsx = _make_workbook_bytes([("NASDAQ:NVDA", "", 1, 1.0), ("HKG:0700", None, 1, 1.0),
                                 ("603259.SS", "CN", 1, 1.0)])
    assert sync.scan_sheet_tickers_tolerant(xlsx) == [
        ("NASDAQ:NVDA", "US"), ("HKG:0700", "US"), ("603259.SS", "CN")]


# ---------------------------------------------------------------- #7 company.db without watchlist

@pytest.mark.parametrize("kind", ["no_watchlist_table", "missing_file"])
def test_detect_delta_tolerates_company_db_without_watchlist(tmp_path, capsys, kind):
    market = _local_market(tmp_path)
    company = tmp_path / "empty-company.db"
    if kind == "no_watchlist_table":
        with closing(sqlite3.connect(str(company))) as c:
            c.execute("CREATE TABLE companies (symbol TEXT)")
            c.commit()
    rc = sync.main(["--detect-delta", "--exchange-dir", str(tmp_path / "xchg"), "--market-db", str(market),
                    "--company-db", str(company), "--skip-sheet", "--no-pull"])
    assert rc == sync.EXIT_OK
    assert _last_json(capsys)["ok"] is True
    assert company.exists() is (kind == "no_watchlist_table")


# ---------------------------------------------------------------- #8 fail-closed model fields

@pytest.mark.parametrize("field, value", [
    ("pi_bucket", False), ("pi_bucket", ""), ("agreement_status", []), ("l2_id", 123),
    ("cross_themes", {}), ("business_note", 999),
])
def test_merge_classifications_rejects_bad_types_without_fallback(field, value):
    delta = {"missing_symbols": [{
        "symbol": "AAAX", "cik": None, "asset_kind": "us_equity", "in_us_basket_calc": 1,
        "profile_sector": None, "profile_industry": None, "company_name_snapshot": None}]}
    rows, rejected = sync.merge_classifications(delta, [_cls("AAAX", **{field: value})], _baskets_doc())
    assert rows == []
    assert [r[0] for r in rejected] == ["AAAX"]


def test_commit_outbox_quarantines_bad_type_rows_locally(tmp_path, capsys):
    market = _local_market(tmp_path, sm=DEFAULT_SM + (("CCCX", "0009990004", 0),))
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX", pi_bucket=False), _cls("BBBX", agreement_status=[]),
                              _cls("CCCX", cross_themes={})])
    cloud = _Cloud(tmp_path)
    assert _commit(tmp_path, cloud) == sync.EXIT_OK
    out = _last_json(capsys)
    assert {q[0] for q in out["local_quarantined"]} == {"AAAX", "BBBX", "CCCX"}
    assert not {"AAAX", "BBBX", "CCCX"} & set(cloud.rows())


# ---------------------------------------------------------------- #9 every cloud failure is parked

@pytest.mark.parametrize("rc, error", [
    (75, "lock_busy"),
    (1, "transport_failed: TimeoutExpired: ssh"),
    (1, "backup_failed: RuntimeError: insufficient disk"),
    (1, "apply_failed: OperationalError: database is locked"),
    (1, "apply_failed: ValueError: poison"),
    (1, "bad_envelope: KeyError: 'rows'"),
])
def test_every_cloud_failure_after_rehearsal_is_parked(tmp_path, capsys, rc, error):
    """The local gate and :memory: rehearsal already passed, so any cloud failure is parked for replay."""
    assert not hasattr(sync, "_is_retryable_cloud_failure")
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX")])
    cloud = _Cloud(tmp_path, script=[(rc, {"ok": False, "error": error})])
    assert _commit(tmp_path, cloud) == rc
    out = _last_json(capsys)
    assert out["parked"] is True and out["error"] == error
    assert [p.name for p in _pending(tmp_path)] == [out["payload_id"] + ".json"]
    assert list((tmp_path / "xchg" / "outbox").iterdir()) == []


# ---------------------------------------------------------------- #11 rows already queued in pending/

def test_next_commit_drops_rows_already_queued_in_pending(tmp_path, capsys):
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX")])
    cloud = _Cloud(tmp_path, script=[LOCK_BUSY])
    assert _commit(tmp_path, cloud) == sync.EXIT_LOCK_BUSY
    old = json.loads(_pending(tmp_path)[0].read_text(encoding="utf-8"))
    assert {"AAAX", "ZZETF"} <= {r["symbol"] for r in old["rows"]}

    delta2 = _detect(tmp_path, market)  # the local db was never pulled: AAAX is still missing
    assert {"AAAX", "BBBX"} <= {m["symbol"] for m in delta2["missing_symbols"]}
    _outbox(tmp_path, delta2, [_cls("AAAX", business_note="second opinion"), _cls("BBBX")])
    capsys.readouterr()
    assert _commit(tmp_path, cloud) == sync.EXIT_OK
    assert cloud.calls[1]["payload_id"] == old["payload_id"]
    assert [r["symbol"] for r in cloud.calls[2]["rows"]] == ["BBBX"]
    out = _last_json(capsys)
    assert out["cloud"]["quarantined"] == []
    assert out["replayed_quarantined"] == []
    rows = cloud.rows()
    assert {"AAAX", "BBBX", "ZZETF"} <= set(rows)
    assert rows["AAAX"][4] == "AAAX note"
    assert _pending(tmp_path) == []


def test_commit_with_every_row_already_queued_writes_no_new_package(tmp_path, capsys):
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX")])
    cloud = _Cloud(tmp_path, script=[LOCK_BUSY, LOCK_BUSY])
    assert _commit(tmp_path, cloud) == sync.EXIT_LOCK_BUSY
    first = _pending(tmp_path)

    delta2 = _detect(tmp_path, market)
    _outbox(tmp_path, delta2, [_cls("AAAX", business_note="second opinion")])
    capsys.readouterr()
    assert _commit(tmp_path, cloud) == sync.EXIT_LOCK_BUSY
    out = _last_json(capsys)
    assert out["rows"] == 0 and out["parked"] is True
    assert _pending(tmp_path) == first
    assert len(cloud.calls) == 2  # the second run only tried the queued package
    outbox = tmp_path / "xchg" / "outbox"
    assert not (outbox / "payload.json").exists() and not list(outbox.glob(".inflight.*"))


# ---------------------------------------------------------------- #12 replayed_quarantined

def test_replay_pending_reports_cloud_quarantine(tmp_path, capsys):
    cloud = _Cloud(tmp_path)
    rc, _, _ = cloud(["--apply-cloud-stdin"], json.dumps(_envelope([_store_row("PRIV1", "cloud copy")])).encode())
    assert rc == sync.EXIT_OK
    dirs = sync.ensure_exchange_dirs(tmp_path / "xchg")
    rows = [_store_row("PRIV1", "stale copy")]
    sync.atomic_write_json(dirs["pending"] / "stale.json", {
        "schema_version": 1, "payload_id": store.compute_payload_id(rows), "rows": rows,
        "created_at": "2026-11-29T00:00:00Z", "local_summary": {}})
    capsys.readouterr()
    rc = sync.replay_pending(exchange_dir=tmp_path / "xchg", baskets_path=BASKETS, overrides_path=OVERRIDES,
                             market_db_path=tmp_path / "m.db", ssh_runner=cloud, send_telegram=False,
                             skip_pull=True)
    assert rc == sync.EXIT_OK
    out = _last_json(capsys)
    assert [q[0] for q in out["replayed_quarantined"]] == ["PRIV1"]
    assert "already exists" in out["replayed_quarantined"][0][1]


# ---------------------------------------------------------------- round 2: #10 / #11 replay and parking

def test_row_quarantined_on_replay_is_resent_from_this_round(tmp_path, capsys):
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    dirs = sync.ensure_exchange_dirs(tmp_path / "xchg")
    stale = [dict(_store_row("AAAX", "stale copy"), l2_id="no_such_l2")]
    sync.atomic_write_json(dirs["pending"] / "old.json", {
        "schema_version": 1, "payload_id": store.compute_payload_id(stale), "rows": stale,
        "created_at": "2026-11-28T00:00:00Z", "local_summary": {}})
    _outbox(tmp_path, delta, [_cls("AAAX")])
    cloud = _Cloud(tmp_path)
    assert _commit(tmp_path, cloud) == sync.EXIT_OK
    out = _last_json(capsys)
    assert [q[0] for q in out["replayed_quarantined"]] == ["AAAX"]
    assert "AAAX" in {r["symbol"] for r in cloud.calls[1]["rows"]}
    assert out["cloud"]["quarantined"] == []
    assert cloud.rows()["AAAX"][4] == "AAAX note"


def test_zero_row_package_with_pivot_notes_is_parked(tmp_path, capsys):
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX")])
    cloud = _Cloud(tmp_path, script=[LOCK_BUSY, LOCK_BUSY])
    assert _commit(tmp_path, cloud) == sync.EXIT_LOCK_BUSY
    first = _pending(tmp_path)

    delta2 = _detect(tmp_path, market)
    delta2["pivot_candidates"] = [{"symbol": "PIVX", "old_profile_sector": "Energy",
                                   "old_profile_industry": "Oil & Gas", "new_profile_sector": "Technology",
                                   "new_profile_industry": "Software"}]
    delta2["delta_sha256"] = sync.compute_delta_sha256(delta2)
    sync.atomic_write_json(tmp_path / "xchg" / "inbox" / "delta.json", delta2)
    sync.atomic_write_json(tmp_path / "xchg" / "outbox" / "payload.json", {
        "delta_sha256": delta2["delta_sha256"], "classifications": [_cls("AAAX")],
        "pivot_notes": [{"symbol": "PIVX", "note": "建议复核是否转入软件赛道"}]})
    capsys.readouterr()
    assert _commit(tmp_path, cloud) == sync.EXIT_LOCK_BUSY
    out = _last_json(capsys)
    assert out["rows"] == 0 and out["parked"] is True
    new = [p for p in _pending(tmp_path) if p not in first]
    assert len(new) == 1
    notes = json.loads(new[0].read_text(encoding="utf-8"))["local_summary"]["pivot_notes"]
    assert notes == [{"symbol": "PIVX", "note": "建议复核是否转入软件赛道"}]


def test_parking_never_overwrites_a_corrupt_pending_file(tmp_path, capsys):
    market = _local_market(tmp_path)
    delta = _detect(tmp_path, market)
    _outbox(tmp_path, delta, [_cls("AAAX")])
    cloud = _Cloud(tmp_path, script=[LOCK_BUSY])
    assert _commit(tmp_path, cloud) == sync.EXIT_LOCK_BUSY
    [target] = _pending(tmp_path)
    target.write_text("{corrupt", encoding="utf-8")

    _outbox(tmp_path, delta, [_cls("AAAX")])  # the same payload again → the same payload_id
    capsys.readouterr()
    assert _commit(tmp_path, cloud) == sync.EXIT_FAIL
    out = _last_json(capsys)
    assert out["parked"] is True and out["payload_id"] + ".json" == target.name
    assert target.read_text(encoding="utf-8") == "{corrupt"
    others = [p for p in _pending(tmp_path) if p != target]
    assert len(others) == 1 and others[0].name.endswith("-" + target.name)
    assert sync._load_sealed(others[0])["payload_id"] == out["payload_id"]
    assert len(cloud.calls) == 1  # the corrupt file stopped the replay before any send
