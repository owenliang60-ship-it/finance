"""update_fmp_forward 编排器测试：CLI 语义 / manifest 规则 / 失败 gate / 幂等。

全部使用 fake client/store：无网络、无真实 DB。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.update_fmp_forward import (  # noqa: E402
    ForwardRunSummary,
    parse_args,
    run_update,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fmp_forward"
SNAP = "2026-07-12"
CONFIG_DIR = Path(__file__).parent.parent / "config" / "baskets"


def _holdings_raw():
    return json.loads((FIXTURES / "etf_holdings.json").read_text())


def _quarter_raw():
    return json.loads((FIXTURES / "analyst_estimates_quarter.json").read_text())


def _annual_raw():
    return json.loads((FIXTURES / "analyst_estimates_annual.json").read_text())


def _earnings_raw():
    return json.loads((FIXTURES / "earnings.json").read_text())


class FakeClient:
    """记录调用顺序；按 symbol 配置响应/异常。"""

    def __init__(self, quarter=None, annual=None, earnings=None, holdings=None,
                 api_key="fake_key"):
        self.api_key = api_key
        self.calls = []          # (method, symbol, kwargs)
        self.quarter = quarter or {}
        self.annual = annual or {}
        self.earnings_map = earnings or {}
        self.holdings = holdings or {}

    def _resolve(self, table, symbol, default):
        value = table.get(symbol, default)
        if isinstance(value, Exception):
            raise value
        return value

    def get_analyst_estimates(self, symbol, period="quarter", limit=100):
        self.calls.append((f"estimates:{period}", symbol, {"limit": limit}))
        table = self.quarter if period == "quarter" else self.annual
        default = _quarter_raw() if period == "quarter" else _annual_raw()
        return self._resolve(table, symbol, default)

    def get_earnings(self, symbol, limit=8):
        self.calls.append(("earnings", symbol, {"limit": limit}))
        return self._resolve(self.earnings_map, symbol, _earnings_raw())

    def get_etf_holdings(self, symbol):
        self.calls.append(("holdings", symbol, {}))
        return self._resolve(self.holdings, symbol, _holdings_raw())


class FakeStore:
    """内存版 store：记录写调用，维护 manifest/holdings 状态。"""

    def __init__(self, weekly_exists=False):
        self.calls = []
        self.runs = {}
        self.holdings = {}
        self.estimates = {}
        self.earnings = {}
        self.weekly_exists = weekly_exists

    def has_fmp_weekly_estimates(self, snapshot_date):
        self.calls.append(("has_weekly", snapshot_date))
        return self.weekly_exists

    def get_fmp_forward_run(self, snapshot_date, run_kind="weekly"):
        run = self.runs.get((snapshot_date, run_kind))
        return dict(run) if run else None

    def upsert_fmp_forward_run(self, row):
        self.calls.append(("upsert_run", row.get("status")))
        key = (row["snapshot_date"], row["run_kind"])
        existing = self.runs.get(key)
        universe = row.get("target_universe")
        if existing:
            if universe is not None and sorted(set(universe)) != existing["target_universe"]:
                raise ValueError("target universe mismatch")
            existing.update({
                "status": row["status"],
                "quarter_success": row.get("quarter_success", 0),
                "quarter_failure_count": row.get("quarter_failure_count", 0),
                "completed_at": row.get("completed_at"),
                "summary_json": row.get("summary_json"),
            })
        else:
            normalized = sorted({s.upper() for s in universe})
            self.runs[key] = {
                "snapshot_date": row["snapshot_date"], "run_kind": row["run_kind"],
                "status": row["status"], "target_universe": normalized,
                "target_count": len(normalized),
                "quarter_success": row.get("quarter_success", 0),
                "quarter_failure_count": row.get("quarter_failure_count", 0),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "summary_json": row.get("summary_json"),
            }
        return 1

    def replace_fmp_etf_holdings(self, basket, snapshot_date, rows):
        self.calls.append(("replace_holdings", basket))
        self.holdings[(basket, snapshot_date)] = list(rows)
        return len(rows)

    def get_fmp_etf_holdings(self, basket, snapshot_date, included_only=False):
        rows = self.holdings.get((basket, snapshot_date), [])
        if included_only:
            rows = [r for r in rows if r["included"] == 1]
        return list(rows)

    def upsert_fmp_estimates(self, symbol, rows):
        self.calls.append(("upsert_estimates", symbol))
        self.estimates.setdefault(symbol, []).extend(rows)
        return len(rows)

    def replace_fmp_earnings(self, symbol, rows):
        self.calls.append(("replace_earnings", symbol))
        self.earnings[symbol] = list(rows)
        return len(rows)


def _args(**overrides):
    base = dict(mode="weekly", snapshot_date=SNAP, backfill_start="2021-01-01",
                symbols=None, resume=False, dry_run=False, no_telegram=True,
                data_root=None, config_dir=CONFIG_DIR)
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(args=None, client=None, store=None, core=None, extended=None,
         send=None):
    return run_update(
        args or _args(),
        client=client or FakeClient(),
        store=store if store is not None else FakeStore(),
        core_loader=core or (lambda: ["QS", "AAPL"]),
        extended_loader=extended or (lambda: ["AAPL", "MSFT", "NVDA"]),
        send_message_fn=send or Mock(),
    )


# ---- CLI 契约（exit 2 在任何 API/DB 初始化前）----

@pytest.mark.parametrize("argv", [
    ["--snapshot-date", SNAP],                                    # 缺 --mode
    ["--mode", "weekly", "--symbols", "AAPL"],                    # 非 dry-run 非 resume
    ["--mode", "weekly", "--resume"],                             # resume 无 symbols
    ["--mode", "weekly", "--resume", "--symbols", "A", "--dry-run"],  # resume+dry-run
    ["--mode", "weekly", "--backfill-start", "2021-01-01"],       # backfill-start 用于 weekly
    ["--mode", "weekly", "--snapshot-date", "07/12/2026"],        # 非 ISO 日期
])
def test_invalid_cli_combinations_exit_2(argv):
    with pytest.raises(SystemExit) as ei:
        parse_args(argv)
    assert ei.value.code == 2


def test_valid_cli_forms_parse():
    a = parse_args(["--mode", "weekly", "--snapshot-date", SNAP])
    assert a.mode == "weekly" and not a.dry_run
    b = parse_args(["--mode", "backfill", "--backfill-start", "2021-01-01"])
    assert b.mode == "backfill"
    c = parse_args(["--mode", "weekly", "--symbols", "AAPL,MU", "--dry-run"])
    assert c.symbols == ["AAPL", "MU"] and c.dry_run
    d = parse_args(["--mode", "weekly", "--resume", "--symbols", "AAPL",
                    "--snapshot-date", SNAP])
    assert d.resume and d.symbols == ["AAPL"]


# ---- 编排契约 ----

def test_call_order_holdings_then_per_symbol():  # case 1
    client = FakeClient()
    rc, _ = _run(client=client)
    assert rc == 0
    holdings_calls = [c for c in client.calls if c[0] == "holdings"]
    assert [c[1] for c in holdings_calls] == ["SPY", "QQQ", "SOXX", "IGV", "XLF"]
    first_symbol_idx = next(i for i, c in enumerate(client.calls)
                            if c[0].startswith("estimates"))
    assert all(client.calls.index(h) < first_symbol_idx for h in holdings_calls)
    # 每股顺序 quarter → annual → earnings
    per_symbol = [c[0] for c in client.calls[first_symbol_idx:first_symbol_idx + 3]]
    assert per_symbol == ["estimates:quarter", "estimates:annual", "earnings"]


def test_manifest_persisted_before_first_symbol_request():  # case 4
    events = []

    class OrderClient(FakeClient):
        def get_analyst_estimates(self, symbol, period="quarter", limit=100):
            events.append("symbol_request")
            return super().get_analyst_estimates(symbol, period, limit)

    class OrderStore(FakeStore):
        def upsert_fmp_forward_run(self, row):
            events.append(f"manifest:{row.get('status')}")
            return super().upsert_fmp_forward_run(row)

        def replace_fmp_etf_holdings(self, basket, snapshot_date, rows):
            events.append("persist_holdings")
            return super().replace_fmp_etf_holdings(basket, snapshot_date, rows)

    rc, _ = _run(client=OrderClient(), store=OrderStore())
    assert rc == 0
    first_manifest = events.index("manifest:running")
    first_symbol = events.index("symbol_request")
    first_holdings = events.index("persist_holdings")
    assert first_manifest < first_symbol
    assert first_manifest < first_holdings < first_symbol  # case 2 顺序


def test_universe_uses_injected_loaders_and_core_gap():  # case 3 + round-4/5
    store = FakeStore()
    rc, summary = _run(store=store,
                       core=lambda: ["QS"], extended=lambda: ["MSFT"])
    assert rc == 0
    run = store.runs[(SNAP, "weekly")]
    assert "QS" in run["target_universe"]      # 核心池非扩展池成员入 universe
    assert "NVMI" in run["target_universe"]    # 篮子 included 映射股
    assert summary.target_count == run["target_count"]


def test_empty_core_or_extended_loader_fails_fast():
    store = FakeStore()
    rc, _ = _run(store=store, core=lambda: [])
    assert rc != 0
    assert not store.runs and not store.holdings  # 零写入
    store2 = FakeStore()
    rc2, _ = _run(store=store2, extended=lambda: [])
    assert rc2 != 0 and not store2.runs


def test_weekly_limits():  # case 5
    client = FakeClient()
    _run(client=client)
    est = [c for c in client.calls if c[0].startswith("estimates")]
    earn = [c for c in client.calls if c[0] == "earnings"]
    assert all(c[2]["limit"] == 100 for c in est)
    assert all(c[2]["limit"] == 8 for c in earn)


def test_backfill_limits_and_kind():  # case 6
    client = FakeClient()
    store = FakeStore()
    rc, _ = _run(_args(mode="backfill"), client=client, store=store)
    assert rc == 0
    earn = [c for c in client.calls if c[0] == "earnings"]
    assert all(c[2]["limit"] == 100 for c in earn)
    all_rows = [r for rows in store.estimates.values() for r in rows]
    assert all_rows and all(r["snapshot_kind"] == "backfill" for r in all_rows)


def test_dry_run_zero_store_writes_and_no_db(tmp_path):  # case 7
    client = FakeClient()
    rc, summary = run_update(
        _args(dry_run=True, symbols=["AAPL", "MU"], data_root=tmp_path),
        client=client, store=None,
        core_loader=lambda: ["AAPL"], extended_loader=lambda: ["MSFT"],
        send_message_fn=Mock(),
    )
    assert rc == 0
    assert not (tmp_path / "market.db").exists()
    # dry-run 仍验证 holdings 契约
    assert [c[1] for c in client.calls if c[0] == "holdings"] == [
        "SPY", "QQQ", "SOXX", "IGV", "XLF"]
    # 只跑指定 symbols
    est_symbols = {c[1] for c in client.calls if c[0] == "estimates:quarter"}
    assert est_symbols == {"AAPL", "MU"}


def test_single_symbol_exception_recorded_and_continue():  # case 8
    from src.data.fmp_client import FMPResponseError

    client = FakeClient(quarter={"AAPL": FMPResponseError("boom")})
    store = FakeStore()
    rc, summary = _run(client=client, store=store,
                       core=lambda: ["AAPL", "MSFT", "NVDA", "QS", "TSLA"],
                       extended=lambda: ["MSFT", "NVDA", "QS", "TSLA"])
    assert "AAPL" in summary.quarter_failed
    assert "upsert_estimates" in {c[0] for c in store.calls
                                  if c[1] != "AAPL"}  # 后续 symbol 继续写入
    assert ("upsert_estimates", "AAPL") not in store.calls


def test_http_500_and_timeout_through_real_client():  # case 9（Task 2 承诺的回归）
    import requests as requests_lib

    from src.data.fmp_client import FMPClient
    fmp_mod = sys.modules["src.data.fmp_client"]  # 包 __init__ 导出同名单例，需从 sys.modules 取模块

    real = FMPClient(api_key="canary_" + "x", call_interval=0)
    bad_resp = Mock(status_code=500, text="upstream error")
    original_get = fmp_mod.requests.get

    def flaky_get(url, params=None, timeout=None):
        if "analyst-estimates" in url and params.get("symbol") == "AAPL":
            return bad_resp
        if "earnings" in url and params.get("symbol") == "MSFT":
            raise requests_lib.exceptions.Timeout("t")
        raise AssertionError("unexpected real HTTP call")

    class HybridClient(FakeClient):
        def get_analyst_estimates(self, symbol, period="quarter", limit=100):
            self.calls.append((f"estimates:{period}", symbol, {"limit": limit}))
            if symbol == "AAPL" and period == "quarter":
                fmp_mod.requests.get = flaky_get
                try:
                    return real.get_analyst_estimates(symbol, period, limit)
                finally:
                    fmp_mod.requests.get = original_get
            return super().get_analyst_estimates(symbol, period, limit)

        def get_earnings(self, symbol, limit=8):
            self.calls.append(("earnings", symbol, {"limit": limit}))
            if symbol == "MSFT":
                fmp_mod.requests.get = flaky_get
                try:
                    return real.get_earnings(symbol, limit)
                finally:
                    fmp_mod.requests.get = original_get
            return super().get_earnings(symbol, limit)

    store = FakeStore()
    rc, summary = _run(client=HybridClient(), store=store,
                       core=lambda: ["AAPL", "MSFT"],
                       extended=lambda: ["MSFT"])
    assert "AAPL" in summary.quarter_failed        # HTTP 500 → FMPResponseError
    assert "MSFT" in summary.earnings_failed       # timeout → FMPResponseError
    assert ("upsert_estimates", "AAPL") not in store.calls
    assert ("replace_earnings", "MSFT") not in store.calls


def test_valid_empty_payloads_are_failures_preserving_rows():  # case 10 + quarter_empty
    client = FakeClient(quarter={"AAPL": []}, earnings={"MSFT": []})
    store = FakeStore()
    rc, summary = _run(client=client, store=store,
                       core=lambda: ["AAPL", "MSFT"],
                       extended=lambda: ["MSFT"])
    assert "AAPL" in summary.quarter_empty         # round-5：单独记录
    assert "AAPL" not in summary.quarter_failed
    assert "MSFT" in summary.earnings_failed
    assert ("upsert_estimates", "AAPL") not in store.calls
    assert ("replace_earnings", "MSFT") not in store.calls


def test_empty_holdings_is_fatal_before_any_write():
    client = FakeClient(holdings={"QQQ": []})
    store = FakeStore()
    rc, _ = _run(client=client, store=store)
    assert rc != 0
    assert not store.runs and not store.holdings
    assert not [c for c in client.calls if c[0].startswith("estimates")]


def test_failure_rate_gate_over_20pct():  # case 11
    from src.data.fmp_client import FMPResponseError

    client = FakeClient(quarter={"AAPL": FMPResponseError("x"), "MSFT": []})
    store = FakeStore()
    rc, summary = _run(client=client, store=store,
                       core=lambda: ["AAPL", "MSFT", "NVDA", "QS", "TSLA"],
                       extended=lambda: ["MSFT"])
    # 5 targets, 2 critical failures = 40% > 20%
    assert rc == 1
    assert store.runs[(SNAP, "weekly")]["status"] == "failed"


def test_failure_rate_exactly_20pct_passes():  # case 12
    from src.data.fmp_client import FMPResponseError

    client = FakeClient(quarter={"AAPL": FMPResponseError("x")})
    store = FakeStore()
    rc, _ = _run(client=client, store=store,
                 core=lambda: ["AAPL", "MSFT", "NVDA", "QS", "TSLA"],
                 extended=lambda: ["MSFT"])
    # 5 targets, 1 failure = 20%，严格 > 才熔断
    assert rc == 0
    assert store.runs[(SNAP, "weekly")]["status"] == "running"  # 等 verifier


def test_resume_without_manifest_rejected():  # case 13
    store = FakeStore()
    client = FakeClient()
    rc, _ = _run(_args(resume=True, symbols=["AAPL"]), client=client, store=store)
    assert rc != 0
    assert not client.calls          # API 未被触碰
    assert not store.holdings and not store.runs


def test_resume_repairs_subset_preserving_denominator():  # case 14
    store = FakeStore()
    # 先跑 full run 建 manifest
    rc, _ = _run(store=store)
    assert rc == 0
    run_before = store.runs[(SNAP, "weekly")]
    universe_before = list(run_before["target_universe"])
    count_before = run_before["target_count"]

    client = FakeClient()
    rc2, summary = _run(_args(resume=True, symbols=["AAPL", "MSFT"]),
                        client=client, store=store)
    assert rc2 == 0
    assert not [c for c in client.calls if c[0] == "holdings"]  # 不刷新 holdings
    est_symbols = {c[1] for c in client.calls if c[0] == "estimates:quarter"}
    assert est_symbols == {"AAPL", "MSFT"}
    run_after = store.runs[(SNAP, "weekly")]
    assert run_after["target_universe"] == universe_before
    assert run_after["target_count"] == count_before


def test_resume_subset_must_belong_to_manifest():
    store = FakeStore()
    _run(store=store)
    client = FakeClient()
    rc, _ = _run(_args(resume=True, symbols=["ZZZZ"]), client=client, store=store)
    assert rc != 0
    assert not client.calls


def test_full_rerun_manifest_mismatch_zero_writes():  # case 15
    store = FakeStore()
    _run(store=store)
    calls_before = len(store.calls)
    # 池漂移 → 内存 universe 与 manifest 不一致
    client = FakeClient()
    rc, _ = _run(client=client, store=store,
                 core=lambda: ["QS", "AAPL", "NEWCO"],
                 extended=lambda: ["AAPL", "MSFT", "NVDA"])
    assert rc != 0
    writes_after = [c for c in store.calls[calls_before:]
                    if c[0] in ("upsert_estimates", "replace_earnings",
                                "replace_holdings", "upsert_run")]
    assert writes_after == []          # manifest 检查在任何写入之前
    assert not [c for c in client.calls if c[0].startswith("estimates")]


def test_same_snapshot_valid_rerun_idempotent_keys():  # case 16
    store = FakeStore()
    _run(store=store)
    rows_first = {k: len(v) for k, v in store.estimates.items()}
    _run(store=store)
    assert len(store.runs) == 1        # 同 PK manifest，不新增
    assert set(store.holdings.keys()) == {
        (b, SNAP) for b in ("SPY", "QQQ", "SOX", "IGV", "XLF")}
    # estimates 走同 (symbol, snapshot) 键重放（fake 用 append 模拟，行数翻倍即同键重写）
    assert {k: len(v) for k, v in store.estimates.items()} == {
        k: v * 2 for k, v in rows_first.items()}


def test_backfill_refuses_when_weekly_rows_exist():  # case 17
    client = FakeClient()
    store = FakeStore(weekly_exists=True)
    rc, _ = _run(_args(mode="backfill"), client=client, store=store)
    assert rc != 0
    assert not client.calls            # API 调用前拒绝


def test_no_secret_in_summary_or_errors():  # case 18
    from src.data.fmp_client import FMPResponseError

    secret = "canary_" + "orchestrator"
    client = FakeClient(quarter={"AAPL": FMPResponseError("failed")},
                        api_key=secret)
    sent = []
    rc, summary = _run(client=client, store=FakeStore(),
                       send=lambda msg, channel="private": sent.append(msg),
                       core=lambda: ["AAPL", "MSFT"],
                       extended=lambda: ["MSFT"])
    blob = json.dumps(summary.__dict__, default=str)
    assert secret not in blob
    assert all(secret not in m for m in sent)


def test_telegram_private_channel_and_suppression():  # case 19
    send = Mock()
    rc, _ = _run(_args(no_telegram=False), send=send)
    assert send.called
    assert all(kw.get("channel") == "private"
               for _, kw in send.call_args_list)
    send2 = Mock()
    _run(_args(no_telegram=True), send=send2)
    assert not send2.called


# ========== Task 8: verifier 接线 + 状态机 ==========

def _verify_pass(covered=None, expected=None, missing=None):
    def fn(snapshot_date, run_kind):
        exp = expected if expected is not None else 8
        cov = covered if covered is not None else exp
        return 0, {
            "ok": True, "snapshot_date": snapshot_date, "run_kind": run_kind,
            "universe": {"expected": exp, "covered_4q": cov, "pct": 100.0,
                         "missing": missing or []},
            "failures": [], "warnings": [],
        }
    return fn


def _verify_fail(missing=None, failures=None):
    def fn(snapshot_date, run_kind):
        return 1, {
            "ok": False, "snapshot_date": snapshot_date, "run_kind": run_kind,
            "universe": {"expected": 10, "covered_4q": 5, "pct": 50.0,
                         "missing": missing or ["M%02d" % i for i in range(30)]},
            "failures": failures or ["4Q coverage 50.0% < 90.0%"],
            "warnings": [],
        }
    return fn


def test_writer_pass_verifier_pass_returns_0_and_completes():  # case 1+2
    store = FakeStore()
    seen_status_during_verify = []

    def verify(snapshot_date, run_kind):
        seen_status_during_verify.append(
            store.runs[(snapshot_date, run_kind)]["status"])
        return _verify_pass()(snapshot_date, run_kind)

    rc, _ = run_update(
        _args(), client=FakeClient(), store=store,
        core_loader=lambda: ["QS", "AAPL"],
        extended_loader=lambda: ["AAPL", "MSFT", "NVDA"],
        send_message_fn=Mock(), verify_fn=verify,
    )
    assert rc == 0
    assert seen_status_during_verify == ["running"]  # verifier 前保持 running
    run = store.runs[(SNAP, "weekly")]
    assert run["status"] == "complete"               # 只有 verifier PASS 才 complete
    assert run["completed_at"]


def test_verifier_fail_returns_1_and_persists_failed():  # case 3
    store = FakeStore()
    rc, _ = run_update(
        _args(), client=FakeClient(), store=store,
        core_loader=lambda: ["QS", "AAPL"],
        extended_loader=lambda: ["AAPL", "MSFT", "NVDA"],
        send_message_fn=Mock(), verify_fn=_verify_fail(),
    )
    assert rc == 1
    assert store.runs[(SNAP, "weekly")]["status"] == "failed"


def test_critical_gate_failure_skips_verifier():  # case 4
    from src.data.fmp_client import FMPResponseError

    verify_calls = []

    def verify(snapshot_date, run_kind):
        verify_calls.append(snapshot_date)
        return 0, {}

    client = FakeClient(quarter={"AAPL": FMPResponseError("x"), "MSFT": []})
    store = FakeStore()
    rc, _ = run_update(
        _args(), client=client, store=store,
        core_loader=lambda: ["AAPL", "MSFT", "NVDA", "QS", "TSLA"],
        extended_loader=lambda: ["MSFT"],
        send_message_fn=Mock(), verify_fn=verify,
    )
    assert rc == 1
    assert verify_calls == []
    assert store.runs[(SNAP, "weekly")]["status"] == "failed"


def test_backfill_verifier_called_with_backfill_kind():  # case 5
    kinds = []

    def verify(snapshot_date, run_kind):
        kinds.append(run_kind)
        return _verify_pass()(snapshot_date, run_kind)

    store = FakeStore()
    rc, _ = run_update(
        _args(mode="backfill"), client=FakeClient(), store=store,
        core_loader=lambda: ["QS", "AAPL"],
        extended_loader=lambda: ["AAPL", "MSFT", "NVDA"],
        send_message_fn=Mock(), verify_fn=verify,
    )
    assert rc == 0
    assert kinds == ["backfill"]
    assert store.runs[(SNAP, "backfill")]["status"] == "complete"


def test_resume_recomputes_runwide_counts_from_verifier():  # case 6+7
    store = FakeStore()
    client = FakeClient(quarter={"AAPL": [], "MSFT": []})
    core10 = ["AAPL", "MSFT", "NVDA", "QS", "TSLA",
              "META", "AMZN", "GOOGL", "NFLX", "CRM"]
    rc, _ = run_update(
        _args(), client=client, store=store,
        core_loader=lambda: core10, extended_loader=lambda: ["MSFT"],
        send_message_fn=Mock(),
        verify_fn=_verify_fail(),   # 首跑 verifier FAIL → failed
    )
    assert rc == 1

    # resume 修复 AAPL；verifier 现在 PASS，run-wide 计数来自 full report
    expected_total = store.runs[(SNAP, "weekly")]["target_count"]
    rc2, _ = run_update(
        _args(resume=True, symbols=["AAPL"]), client=FakeClient(), store=store,
        core_loader=lambda: core10, extended_loader=lambda: ["MSFT"],
        send_message_fn=Mock(),
        verify_fn=_verify_pass(covered=expected_total - 1,
                               expected=expected_total),
    )
    assert rc2 == 0
    run = store.runs[(SNAP, "weekly")]
    assert run["status"] == "complete"
    assert run["quarter_success"] == expected_total - 1      # covered_4q
    assert run["quarter_failure_count"] == 1                 # expected - covered
    state = json.loads(run["summary_json"])
    assert set(state["run_state"]["quarter_empty"]) == {"MSFT"}  # case 7
    assert len(state["attempts"]) == 2


def test_success_telegram_content_and_no_member_lists():  # case 8
    sent = []
    store = FakeStore()
    rc, summary = run_update(
        _args(no_telegram=False), client=FakeClient(), store=store,
        core_loader=lambda: ["QS", "AAPL"],
        extended_loader=lambda: ["AAPL", "MSFT", "NVDA"],
        send_message_fn=lambda msg, channel="private": sent.append(msg),
        verify_fn=_verify_pass(),
    )
    assert rc == 0 and sent
    msg = sent[-1]
    assert SNAP in msg
    for token in ("covered", "rows", "unmatched"):
        assert token in msg
    assert "duration" in msg or "s" in msg


def test_failure_telegram_top20_missing_only():  # case 9
    sent = []
    store = FakeStore()
    rc, _ = run_update(
        _args(no_telegram=False), client=FakeClient(), store=store,
        core_loader=lambda: ["QS", "AAPL"],
        extended_loader=lambda: ["AAPL", "MSFT", "NVDA"],
        send_message_fn=lambda msg, channel="private": sent.append(msg),
        verify_fn=_verify_fail(),  # 30 个 missing
    )
    assert rc == 1 and sent
    msg = sent[-1]
    assert "M00" in msg and "M19" in msg
    assert "M20" not in msg          # 只展示 top 20
    assert "coverage" in msg.lower() or "fail" in msg.lower()


def test_telegram_send_failure_does_not_fail_data_pass():  # case 10
    def broken_send(msg, channel="private"):
        raise RuntimeError("telegram down")

    store = FakeStore()
    rc, _ = run_update(
        _args(no_telegram=False), client=FakeClient(), store=store,
        core_loader=lambda: ["QS", "AAPL"],
        extended_loader=lambda: ["AAPL", "MSFT", "NVDA"],
        send_message_fn=broken_send, verify_fn=_verify_pass(),
    )
    assert rc == 0
    assert store.runs[(SNAP, "weekly")]["status"] == "complete"


def test_dry_run_reports_verifier_skipped(capsys=None):  # case 11
    verify_calls = []

    def verify(snapshot_date, run_kind):
        verify_calls.append(snapshot_date)
        return 0, {}

    rc, summary = run_update(
        _args(dry_run=True, symbols=["AAPL"]), client=FakeClient(), store=None,
        core_loader=lambda: ["AAPL"], extended_loader=lambda: ["MSFT"],
        send_message_fn=Mock(), verify_fn=verify,
    )
    assert rc == 0
    assert verify_calls == []        # dry-run 绝不调 verifier，也绝不假报 PASS


def test_run_state_quarter_empty_persisted_and_resume_merges():  # round-5 证据链
    client = FakeClient(quarter={"AAPL": [], "MSFT": []})
    store = FakeStore()
    rc, _ = _run(client=client, store=store,
                 core=lambda: ["AAPL", "MSFT", "NVDA", "QS", "TSLA",
                               "META", "AMZN", "GOOGL", "NFLX", "CRM"],
                 extended=lambda: ["MSFT"])
    assert rc == 0  # 2/10 = 20%，不熔断
    state = json.loads(store.runs[(SNAP, "weekly")]["summary_json"])
    assert set(state["run_state"]["quarter_empty"]) == {"AAPL", "MSFT"}
    assert len(state["attempts"]) == 1

    # resume 修复 AAPL（现在有数据），MSFT 未在修复子集 → 保留
    client2 = FakeClient()
    rc2, _ = _run(_args(resume=True, symbols=["AAPL"]),
                  client=client2, store=store)
    assert rc2 == 0
    state2 = json.loads(store.runs[(SNAP, "weekly")]["summary_json"])
    assert set(state2["run_state"]["quarter_empty"]) == {"MSFT"}
    assert len(state2["attempts"]) == 2  # append 而非替换
