# PI 换源 Google Sheet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 执行时第一步把本 plan 拷贝到 `docs/plans/2026-06-10-pi-sheet-book.md`（项目惯例位置）。

**Goal:** PI（Portfolio Intelligence 每晚 Telegram PDF 推送）的股票持仓 + 现金 + 总资产分母从过期的 company.db 换成 Boss 的 Google Sheet（book of record），期权区块继续读 company.db，其他全部不变。

**Architecture:** 新增纯内存 reader `portfolio/holdings/sheet_book.py`（xlsx export + openpyxl，零 DB 写入），在 `run_intelligence()` 入口把持仓加载替换为 sheet 解析 + company.db best-effort enrichment；定价按 sheet `Market` 列路由（US→MarketData live 不变、HK→yfinance 不变、KR/Crypto/篮子/LEAPS→sheet 价格）；NAV/weights 内联计算（原 mgr 方法 store-bound 不可用，manager.py:564/573 已核实）。

**Tech Stack:** Python（云端 3.10.12 — 禁用 f-string 反斜杠/match-case 等 3.12 特性）、openpyxl（新依赖）、requests（已有）、pytest。

**北极星对齐:** Portfolio Desk（CIO-A 副轨 PI 日推），不改层定义，只修数据源正确性。

---

## 已拍板决策（Boss confirmed 2026-06-10）

1. 期权区块继续读 company.db（对冲期权仍持有，MarketData live quotes 零改动）
2. sheet 的 `NVDA LEAPS`/`TSM LEAPS` 行保留在股票区块按 sheet 价格展示（company.db 无记录，sheet 是唯一来源）
3. 总资产分母从 Sheet26 `total` 动态读，`.env` `PORTFOLIO_TOTAL_CAPITAL_USD` 作 fallback
4. 报告格式/信号逻辑/推送渠道/cron 全不变；P3 纯净性（sheet 数据纯内存）；terminal/exposure/benchmark 等其他 company.db 消费者本次不动

## 关键事实（已逐行核实）

- sheet 读法：`https://docs.google.com/spreadsheets/d/{ID}/export?format=xlsx`，ID 存 `.env` `PORTFOLIO_SHEET_ID`（**bearer secret，repo 是 PUBLIC，绝不进 tracked 文件/日志/异常消息**）。云端可直连 Google（已验证 302），openpyxl 未装，云端 `.env` 缺 `PORTFOLIO_SHEET_ID`（部署时手动加）
- `Summary_OSV` 列：Market / Stock Ticker / Last Price（已 USD）/ Shares / Cost (Per Share) / Mkt Value / Category。ticker 空或 Shares≤0 → 过滤；`HKG:0700` 剥前缀；`DRAM` 是无交易所价格的自定义篮子（Market=US）
- `Portfolio Summary`：A 列 label `现金合计` → B 列值；`Sheet26`：B 列 label `total` → C 列值（有前导空列）
- `is_hk_ticker()`（pi:55）= `isdigit()` 会把 KR `000660` 误判为 HK → 路由改用 Market 列；函数本身保留（test_intelligence.py:515-519 依赖）
- `mgr.get_total_nav`/`refresh_prices`（manager.py:562-578）内部重读 company.db → 绕过，内联计算
- `format_report` pi:465 硬编码 `of $5M`（本来就错）→ 动态化
- 失败语义：sheet 下载/解析失败 → 发 Telegram ⚠️ + raise。**绝不静默发"无持仓"、绝不回退 company.db 旧数据**（旧数据是错的，比不发更危险）
- 测试 fixture 全部用合成假数据（虚构 ticker/金额）——真实持仓数字也不进 repo

## 风险自证

最大风险 = sheet 结构漂移（Boss 改列名/挪行）。缓解：header 按列名探测、label 精确匹配、关键字段缺失即 SheetBookError → Telegram 告警 + 非零退出，无静默降级。为什么不是更简单的"继续手动维护 company.db"：5-09 以来实际偏离（现金与持仓均已大幅偏离真实 book，多只已清仓标的仍挂 OPEN）证明手动双写不可持续。
被否决的替代方案：sheet→company.db 同步导入（违反 P3 所有权：company.db 本地独占写入）；每 tab CSV gviz（3 个请求 + 数字格式化风险 + 与文档化读法不一致）。

---

### Task 0: Worktree + 基线

**Files:** 无代码改动

- [ ] **Step 1: 用 superpowers:using-git-worktrees 建隔离 worktree**，分支名 `feature/pi-sheet-book`
- [ ] **Step 2: 记录测试基线**（worktree 无 live data，部分 registry 依赖测试基线本来就 fail——先记下来，结束时只要求不新增 fail）

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/ -q 2>&1 | tail -3`
（worktree 没有自己的 .venv，一律用主库绝对路径 python）
Expected: 记录 pass/fail 数

- [ ] **Step 3: 拷贝本 plan 到 `docs/plans/2026-06-10-pi-sheet-book.md` 并 commit**

```bash
git add docs/plans/2026-06-10-pi-sheet-book.md
git commit -m "docs: PI sheet-book 换源 plan"
```

---

### Task 1: openpyxl 依赖

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: requirements.txt 追加一行**

```
openpyxl>=3.1
```

- [ ] **Step 2: 确认主 venv 已有 openpyxl（本地已装过）**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -c "import openpyxl; print(openpyxl.__version__)"`
Expected: 打印版本号（如 3.1.x）。若 ModuleNotFoundError：`/Users/owen/CC workspace/Finance/.venv/bin/pip install "openpyxl>=3.1"`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add openpyxl dependency for sheet book reader"
```

---

### Task 2: sheet_book 解析器（TDD）

**Files:**
- Create: `tests/test_portfolio/test_sheet_book.py`
- Create: `portfolio/holdings/sheet_book.py`

- [ ] **Step 1: 写失败测试（含合成 fixture builder，全假数据）**

```python
"""Tests for portfolio.holdings.sheet_book — synthetic fixtures only.

PRIVACY: repo is public. Never put real tickers' real amounts or the real
sheet ID in this file. All data below is fictional.
"""
import datetime as dt
import io

import openpyxl
import pytest

from portfolio.holdings.sheet_book import (
    SheetBookError,
    parse_sheet_book,
)

HEADER = [
    "Market", "Stock Ticker", "Google Price", "Manual Price", "Manual Price $",
    "Last Price", "Last DPS", "Yield on Cost", "Last Price Yield", "Shares",
    "Cost", "Cost (Per Share)", "Unrealized Gain/Loss", "Unrealized Gain/Loss (%)",
    "Realized Gain/Loss", "Momentums Collected", "Total Gain/Loss", "Mkt Value",
    "Category",
]

FETCHED_AT = dt.datetime(2026, 6, 10, 12, 0, 0)


def _row(market, ticker, last_price, shares, cost_ps, mkt_value, category):
    r = [None] * len(HEADER)
    r[0], r[1], r[5], r[9], r[11], r[17], r[18] = (
        market, ticker, last_price, shares, cost_ps, mkt_value, category)
    return r


DEFAULT_ROWS = [
    _row("US", "AAA", 10.0, 100, 8.0, 1000.0, "Sentiment"),
    _row("KR", "000001", 50.0, 20, 55.0, 1000.0, "Fundamental"),
    _row("HK", "HKG:0001", 5.0, 200, 4.5, 1000.0, "Value"),
    _row("US", "BBB LEAPS", 30.0, 10, 25.0, 300.0, "LTH"),
    _row("US", "BSKT", 20.0, 50, 15.0, 1000.0, "Fundamental"),
    _row("KR", "000002", 12.0, 0, 0.0, 0.0, "Momentum"),   # closed -> filtered
    _row("Crypto", "", 0.0, 0, 0.0, 0.0, ""),               # template row -> filtered
]


def build_fixture(rows=None, cash_label="现金合计", cash=111000.0,
                  include_sheet26=True, total_label="total", total=1000000.0,
                  include_summary=True, noise_rows=1):
    wb = openpyxl.Workbook()
    ws = wb.active
    if include_summary:
        ws.title = "Summary_OSV"
        for _ in range(noise_rows):
            ws.append(["junk note row"])
        ws.append(HEADER)
        for r in (rows if rows is not None else DEFAULT_ROWS):
            ws.append(r)
    else:
        ws.title = "Other"
    ps = wb.create_sheet("Portfolio Summary")
    ps.append(["持仓市值", 999.0])
    ps.append([cash_label, cash])
    if include_sheet26:
        s26 = wb.create_sheet("Sheet26")
        s26.append([None, "美股", 500000.0, 0.5])
        s26.append([None, total_label, total, 1])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestParseHoldings:
    def test_parses_and_filters(self):
        book = parse_sheet_book(build_fixture(), FETCHED_AT)
        syms = {h.symbol for h in book.holdings}
        assert syms == {"AAA", "000001", "0001", "BBB LEAPS", "BSKT"}
        assert book.fetched_at == FETCHED_AT

    def test_hkg_prefix_stripped_and_fields(self):
        book = parse_sheet_book(build_fixture(), FETCHED_AT)
        hk = {h.symbol: h for h in book.holdings}["0001"]
        assert hk.raw_ticker == "HKG:0001"
        assert hk.market == "HK"
        assert hk.shares == 200
        assert hk.cost_per_share == 4.5
        assert hk.sheet_price == 5.0
        assert hk.category == "Value"

    def test_leaps_flag(self):
        book = parse_sheet_book(build_fixture(), FETCHED_AT)
        flags = {h.symbol: h.is_leaps for h in book.holdings}
        assert flags["BBB LEAPS"] is True
        assert flags["AAA"] is False

    def test_markets_and_sheet_prices_sidecars(self):
        book = parse_sheet_book(build_fixture(), FETCHED_AT)
        assert book.markets()["000001"] == "KR"
        assert book.sheet_prices()["BSKT"] == 20.0

    def test_duplicate_ticker_merged_weighted(self):
        rows = [
            _row("US", "AAA", 10.0, 100, 8.0, 1000.0, "Sentiment"),
            _row("US", "AAA", 10.0, 100, 12.0, 1000.0, "Sentiment"),
        ]
        book = parse_sheet_book(build_fixture(rows=rows), FETCHED_AT)
        assert len(book.holdings) == 1
        h = book.holdings[0]
        assert h.shares == 200
        assert h.cost_per_share == pytest.approx(10.0)
        assert h.market_value == 2000.0

    def test_empty_holdings_raises(self):
        rows = [_row("KR", "000002", 12.0, 0, 0.0, 0.0, "Momentum")]
        with pytest.raises(SheetBookError, match="no holdings"):
            parse_sheet_book(build_fixture(rows=rows), FETCHED_AT)

    def test_missing_summary_tab_raises(self):
        with pytest.raises(SheetBookError, match="Summary_OSV"):
            parse_sheet_book(build_fixture(include_summary=False), FETCHED_AT)


class TestCashAndCapital:
    def test_cash_parsed(self):
        book = parse_sheet_book(build_fixture(), FETCHED_AT)
        assert book.cash_usd == 111000.0

    def test_missing_cash_label_raises(self):
        with pytest.raises(SheetBookError, match="现金合计"):
            parse_sheet_book(build_fixture(cash_label="别的"), FETCHED_AT)

    def test_total_capital_from_sheet26(self):
        book = parse_sheet_book(build_fixture(), FETCHED_AT)
        assert book.total_capital_usd == 1000000.0

    def test_missing_total_returns_none(self):
        book = parse_sheet_book(build_fixture(total_label="not-total"), FETCHED_AT)
        assert book.total_capital_usd is None

    def test_missing_sheet26_tab_returns_none(self):
        book = parse_sheet_book(build_fixture(include_sheet26=False), FETCHED_AT)
        assert book.total_capital_usd is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/test_sheet_book.py -q`
Expected: collection error / ImportError（`portfolio.holdings.sheet_book` 不存在）

- [ ] **Step 3: 实现 `portfolio/holdings/sheet_book.py`**

```python
"""Google Sheet book-of-record reader (read-only, in-memory).

PRIVACY: the sheet ID is a bearer secret (this repo is public).
Never log or embed the sheet ID / URL in exception messages.

P3 purity: this module never writes to company.db / market.db.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SUMMARY_TAB = "Summary_OSV"
CASH_TAB = "Portfolio Summary"
CAPITAL_TAB = "Sheet26"
CASH_LABEL = "现金合计"
CAPITAL_LABEL = "total"
REQUIRED_COLUMNS = [
    "Market", "Stock Ticker", "Last Price", "Shares",
    "Cost (Per Share)", "Mkt Value", "Category",
]


class SheetBookError(Exception):
    """Sheet download/parse failure. Message must never contain sheet ID/URL."""


@dataclass
class SheetHolding:
    symbol: str            # normalized: "HKG:0700" -> "0700", upper/strip
    raw_ticker: str        # sheet original (debug only — never logged)
    market: str            # "US" / "KR" / "HK" / "Crypto"
    shares: float
    cost_per_share: float  # sheet "Cost (Per Share)"
    sheet_price: float     # sheet "Last Price" (already USD)
    market_value: float    # sheet "Mkt Value" (USD, informational)
    category: str          # sheet "Category" sleeve
    is_leaps: bool


@dataclass
class SheetBook:
    holdings: List[SheetHolding]
    cash_usd: float
    total_capital_usd: Optional[float]
    fetched_at: dt.datetime

    def markets(self) -> Dict[str, str]:
        return {h.symbol: h.market for h in self.holdings}

    def sheet_prices(self) -> Dict[str, float]:
        return {h.symbol: h.sheet_price for h in self.holdings}


def _to_float(value) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_symbol(raw: str) -> str:
    sym = str(raw).strip().upper()
    if sym.startswith("HKG:"):
        sym = sym[4:]
    return sym


def _parse_holdings(ws) -> List[SheetHolding]:
    header_map = None
    data_rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if header_map is None:
            if i >= 10:
                break
            cells = [str(c).strip() if c is not None else "" for c in row]
            if "Stock Ticker" in cells:
                header_map = {name: idx for idx, name in enumerate(cells) if name}
            continue
        data_rows.append(row)
    if header_map is None:
        raise SheetBookError(
            "header row with 'Stock Ticker' not found in %s" % SUMMARY_TAB)

    missing = [c for c in REQUIRED_COLUMNS if c not in header_map]
    if missing:
        raise SheetBookError(
            "missing columns in %s: %s" % (SUMMARY_TAB, ", ".join(missing)))

    def cell(row, name):
        idx = header_map[name]
        return row[idx] if idx < len(row) else None

    merged: Dict[str, SheetHolding] = {}
    for row in data_rows:
        raw_ticker = cell(row, "Stock Ticker")
        if raw_ticker is None or str(raw_ticker).strip() == "":
            continue
        shares = _to_float(cell(row, "Shares"))
        if shares <= 0:
            continue  # closed position (realized-only row)
        raw = str(raw_ticker).strip()
        sym = _normalize_symbol(raw)
        h = SheetHolding(
            symbol=sym,
            raw_ticker=raw,
            market=str(cell(row, "Market") or "").strip(),
            shares=shares,
            cost_per_share=_to_float(cell(row, "Cost (Per Share)")),
            sheet_price=_to_float(cell(row, "Last Price")),
            market_value=_to_float(cell(row, "Mkt Value")),
            category=str(cell(row, "Category") or "").strip(),
            is_leaps=sym.endswith(" LEAPS"),
        )
        if sym in merged:
            prev = merged[sym]
            total_shares = prev.shares + h.shares
            if total_shares > 0:
                prev.cost_per_share = (
                    prev.cost_per_share * prev.shares
                    + h.cost_per_share * h.shares
                ) / total_shares
            prev.shares = total_shares
            prev.market_value += h.market_value
            logger.warning("duplicate ticker merged: %s", sym)
        else:
            merged[sym] = h
    return list(merged.values())


def _parse_cash(ws) -> float:
    for row in ws.iter_rows(values_only=True):
        label = str(row[0]).strip() if row and row[0] is not None else ""
        if label == CASH_LABEL:
            value = _to_float(row[1] if len(row) > 1 else None)
            if value > 0:
                return value
            raise SheetBookError("cash value invalid in %s" % CASH_TAB)
    raise SheetBookError("label not found in %s: %s" % (CASH_TAB, CASH_LABEL))


def _parse_total_capital(ws) -> Optional[float]:
    for row in ws.iter_rows(values_only=True):
        if row is None or len(row) < 3 or row[1] is None:
            continue
        if str(row[1]).strip().lower() == CAPITAL_LABEL:
            value = _to_float(row[2])
            return value if value > 0 else None
    return None


def parse_sheet_book(xlsx_bytes: bytes, fetched_at: dt.datetime) -> SheetBook:
    import openpyxl

    wb = openpyxl.load_workbook(
        io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    try:
        if SUMMARY_TAB not in wb.sheetnames:
            raise SheetBookError("sheet tab missing: %s" % SUMMARY_TAB)
        if CASH_TAB not in wb.sheetnames:
            raise SheetBookError("sheet tab missing: %s" % CASH_TAB)

        holdings = _parse_holdings(wb[SUMMARY_TAB])
        if not holdings:
            raise SheetBookError("no holdings parsed from sheet")
        cash = _parse_cash(wb[CASH_TAB])
        total = (
            _parse_total_capital(wb[CAPITAL_TAB])
            if CAPITAL_TAB in wb.sheetnames else None
        )
    finally:
        wb.close()
    return SheetBook(
        holdings=holdings, cash_usd=cash,
        total_capital_usd=total, fetched_at=fetched_at,
    )


def load_sheet_book(sheet_id: Optional[str] = None,
                    timeout: float = 30.0) -> SheetBook:
    """Download the book-of-record workbook and parse it.

    Raises SheetBookError on any failure. Error messages never contain
    the sheet ID or URL (bearer secret, public repo).
    """
    import requests

    sheet_id = (sheet_id or os.environ.get("PORTFOLIO_SHEET_ID", "")).strip()
    if not sheet_id:
        raise SheetBookError("PORTFOLIO_SHEET_ID not configured")
    url = (
        "https://docs.google.com/spreadsheets/d/%s/export?format=xlsx"
        % sheet_id
    )
    try:
        resp = requests.get(url, timeout=timeout)
    except Exception as e:
        # requests exception messages may embed the URL — keep class name only
        raise SheetBookError("sheet download failed: %s" % type(e).__name__)
    if resp.status_code != 200:
        raise SheetBookError(
            "sheet download failed: HTTP %d" % resp.status_code)
    return parse_sheet_book(resp.content, dt.datetime.now())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/test_sheet_book.py -q`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add portfolio/holdings/sheet_book.py tests/test_portfolio/test_sheet_book.py
git commit -m "feat(portfolio): Google Sheet book-of-record reader (parse layer)"
```

---

### Task 3: load_sheet_book 下载层 + 隐私回归（TDD）

**Files:**
- Modify: `tests/test_portfolio/test_sheet_book.py`（追加测试类）

- [ ] **Step 1: 追加失败测试（download/env/隐私）**

```python
class TestLoadSheetBook:
    def test_missing_env_raises(self, monkeypatch):
        from portfolio.holdings.sheet_book import load_sheet_book
        monkeypatch.delenv("PORTFOLIO_SHEET_ID", raising=False)
        with pytest.raises(SheetBookError, match="not configured"):
            load_sheet_book()

    def test_http_error_message_has_no_sheet_id(self, monkeypatch):
        from portfolio.holdings.sheet_book import load_sheet_book
        fake_id = "FAKE_SHEET_ID_123"
        monkeypatch.setenv("PORTFOLIO_SHEET_ID", fake_id)

        class FakeResp:
            status_code = 404
            content = b""

        monkeypatch.setattr("requests.get", lambda url, timeout: FakeResp())
        with pytest.raises(SheetBookError) as ei:
            load_sheet_book()
        assert fake_id not in str(ei.value)

    def test_network_error_message_has_no_url(self, monkeypatch):
        from portfolio.holdings.sheet_book import load_sheet_book
        fake_id = "FAKE_SHEET_ID_123"
        monkeypatch.setenv("PORTFOLIO_SHEET_ID", fake_id)

        def boom(url, timeout):
            raise ConnectionError("https://docs.google.com/x/" + fake_id)

        monkeypatch.setattr("requests.get", boom)
        with pytest.raises(SheetBookError) as ei:
            load_sheet_book()
        assert fake_id not in str(ei.value)
        assert "docs.google.com" not in str(ei.value)

    def test_success_path(self, monkeypatch):
        from portfolio.holdings.sheet_book import load_sheet_book
        monkeypatch.setenv("PORTFOLIO_SHEET_ID", "FAKE")
        payload = build_fixture()

        class FakeResp:
            status_code = 200
            content = payload

        monkeypatch.setattr("requests.get", lambda url, timeout: FakeResp())
        book = load_sheet_book()
        assert book.cash_usd == 111000.0
        assert {h.symbol for h in book.holdings} >= {"AAA", "BSKT"}
```

- [ ] **Step 2: 跑测试**（实现已在 Task 2 一并写好，本步是验证下载层行为正确）

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/test_sheet_book.py -q`
Expected: 16 passed。若有 fail → 修 `load_sheet_book` 直到全绿

- [ ] **Step 3: Commit**

```bash
git add tests/test_portfolio/test_sheet_book.py
git commit -m "test(portfolio): sheet book download layer + privacy regression"
```

---

### Task 4: manager 公开 enrichment wrapper（TDD）

**Files:**
- Modify: `portfolio/holdings/manager.py`（在 `_enrich` 定义之前、`get_portfolio_summary` 之后插入，约 :624）
- Modify: `tests/test_portfolio/test_manager.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

```python
class TestEnrichHoldingRow:
    """Public wrapper used by sheet-sourced callers (PI)."""

    def test_wraps_enrich_with_company_metadata(self, tmp_path):
        from terminal.company_store import CompanyStore
        from portfolio.holdings.manager import PortfolioManager

        store = CompanyStore(db_path=tmp_path / "t.db")
        store.upsert_company("NVDA", company_name="NVIDIA", sector="Technology")
        mgr = PortfolioManager(store=store)
        row = {"symbol": "NVDA", "avg_cost": 10.0, "shares": 100,
               "open_date": "", "position_id": None, "status": "OPEN"}
        pos = mgr.enrich_holding_row(row)
        assert pos.symbol == "NVDA"
        assert pos.company_name == "NVIDIA"
        assert pos.sector == "Technology"
        assert pos.cost_basis == 10.0
        assert pos.shares == 100
        store.close()

    def test_unknown_symbol_best_effort(self, tmp_path):
        from terminal.company_store import CompanyStore
        from portfolio.holdings.manager import PortfolioManager

        store = CompanyStore(db_path=tmp_path / "t.db")
        mgr = PortfolioManager(store=store)
        row = {"symbol": "0001", "avg_cost": 4.5, "shares": 200,
               "open_date": "", "position_id": None, "status": "OPEN"}
        pos = mgr.enrich_holding_row(row)
        assert pos.symbol == "0001"
        assert pos.sector == ""  # not in companies table -> empty (PI falls back to Category)
        store.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/test_manager.py::TestEnrichHoldingRow -q`
Expected: FAIL — `AttributeError: ... no attribute 'enrich_holding_row'`

- [ ] **Step 3: manager.py 加 4 行 wrapper**（插在 `# ---- Enrichment ----` 注释之后、`def _enrich` 之前）

```python
    def enrich_holding_row(self, row: Dict) -> Position:
        """Public wrapper: build an enriched Position from a raw holding-row dict.

        Used by sheet-sourced callers (PI) that bypass company.db holdings.
        Required row keys: symbol / avg_cost / shares / open_date / position_id / status.
        """
        return self._enrich(row)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/test_manager.py -q`
Expected: 全绿（含原有测试）

- [ ] **Step 5: Commit**

```bash
git add portfolio/holdings/manager.py tests/test_portfolio/test_manager.py
git commit -m "feat(portfolio): public enrich_holding_row wrapper for sheet-sourced positions"
```

---

### Task 5: PI helper `_position_from_sheet`（TDD）

**Files:**
- Modify: `scripts/portfolio_intelligence.py`（`get_positions_as_of` 之后，约 :435）
- Modify: `tests/test_portfolio/test_intelligence.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

```python
class TestPositionFromSheet:
    def test_field_mapping_and_sector_fallback(self, tmp_path):
        from scripts.portfolio_intelligence import _position_from_sheet
        from portfolio.holdings.manager import PortfolioManager
        from portfolio.holdings.sheet_book import SheetHolding
        from terminal.company_store import CompanyStore

        store = CompanyStore(db_path=tmp_path / "t.db")
        mgr = PortfolioManager(store=store)
        h = SheetHolding(
            symbol="BSKT", raw_ticker="BSKT", market="US", shares=50,
            cost_per_share=15.0, sheet_price=20.0, market_value=1000.0,
            category="Fundamental", is_leaps=False)
        pos = _position_from_sheet(mgr, h)
        assert pos.symbol == "BSKT"
        assert pos.cost_basis == 15.0
        assert pos.shares == 50
        assert pos.sector == "Fundamental"  # companies 表查不到 -> fallback Category
        assert pos.status == "OPEN"
        store.close()

    def test_known_symbol_keeps_db_sector(self, tmp_path):
        from scripts.portfolio_intelligence import _position_from_sheet
        from portfolio.holdings.manager import PortfolioManager
        from portfolio.holdings.sheet_book import SheetHolding
        from terminal.company_store import CompanyStore

        store = CompanyStore(db_path=tmp_path / "t.db")
        store.upsert_company("AAA", company_name="Aaa Inc", sector="Technology")
        mgr = PortfolioManager(store=store)
        h = SheetHolding(
            symbol="AAA", raw_ticker="AAA", market="US", shares=100,
            cost_per_share=8.0, sheet_price=10.0, market_value=1000.0,
            category="Sentiment", is_leaps=False)
        pos = _position_from_sheet(mgr, h)
        assert pos.sector == "Technology"  # DB sector 优先于 Category
        assert pos.company_name == "Aaa Inc"
        store.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/test_intelligence.py::TestPositionFromSheet -q`
Expected: FAIL — ImportError `_position_from_sheet`

- [ ] **Step 3: 实现 helper（pi 约 :435，`get_positions_as_of` 之后）**

```python
SHEET_PRICED_US_SYMBOLS = {"DRAM"}  # 自定义篮子等无交易所价格的 US 行，直接用 sheet 价


def _position_from_sheet(mgr, holding):
    """Build an enriched Position from a SheetHolding (sheet = book of record)."""
    row = {
        "symbol": holding.symbol,
        "avg_cost": holding.cost_per_share,
        "shares": holding.shares,
        "open_date": "",
        "position_id": None,
        "status": "OPEN",
    }
    pos = mgr.enrich_holding_row(row)
    if not pos.sector:
        pos.sector = holding.category or "Unknown"
    return pos
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/test_intelligence.py::TestPositionFromSheet -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/portfolio_intelligence.py tests/test_portfolio/test_intelligence.py
git commit -m "feat(pi): _position_from_sheet helper with Category sector fallback"
```

---

### Task 6: run_intelligence 换源接线

**Files:**
- Modify: `scripts/portfolio_intelligence.py`（模块顶部 import + `run_intelligence()` 内 8 处 + `format_report` 1 处）
- Modify: `tests/test_portfolio/test_intelligence.py`（错误路径 + format_report 测试）

- [ ] **Step 1: 写失败测试（sheet 失败 → 告警 + raise；format_report 动态分母）**

```python
class TestSheetFailurePath:
    def test_sheet_failure_sends_alert_and_raises(self, monkeypatch):
        import scripts.portfolio_intelligence as pi
        from portfolio.holdings.sheet_book import SheetBookError

        sent = []

        def fake_load():
            raise SheetBookError("sheet tab missing: Summary_OSV")

        monkeypatch.setattr(pi, "load_sheet_book", fake_load)
        monkeypatch.setattr(
            pi, "_send_private_report",
            lambda msg, dry_run=False: (sent.append(msg), msg)[1])
        with pytest.raises(SheetBookError):
            pi.run_intelligence(dry_run=True)
        assert len(sent) == 1
        assert "失败" in sent[0]
        assert "Summary_OSV" in sent[0]


class TestFormatReportDynamicCapital:
    def test_no_hardcoded_5m(self):
        from scripts.portfolio_intelligence import format_report
        # synthetic figures (public repo — never mirror real book values here)
        summary = {
            "total_nav": 4200000, "total_capital": 8120000,
            "tracked_nav_total_pct": 0.517, "invested_value": 3100000,
            "invested_pct": 0.74, "invested_total_pct": 0.382,
            "cash": 800000, "cash_pct": 0.26, "cash_total_pct": 0.099,
            "total_pnl": 200000, "total_pnl_pct": 0.08,
            "total_positions": 14, "position_details": [],
            "option_details": [], "sector_warnings": [],
            "concentration": {}, "dna_distribution": "",
        }
        report = format_report([], summary, {})
        assert "of $5M" not in report
        assert "$8.12M" in report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest "tests/test_portfolio/test_intelligence.py::TestSheetFailurePath" "tests/test_portfolio/test_intelligence.py::TestFormatReportDynamicCapital" -q`
Expected: 2 failed（pi 没有 load_sheet_book 属性；`of $5M` 仍硬编码）

- [ ] **Step 3: 模块顶部加 import**（与现有顶部 import 放一起；sheet_book 内部 openpyxl/requests 都是惰性导入，模块级 import 无重量）

```python
from portfolio.holdings.sheet_book import SheetBookError, load_sheet_book
```

- [ ] **Step 4: 替换 run_intelligence 数据加载段（:1039-1043）**

原代码：
```python
    store = get_store()
    mgr = PortfolioManager(store=store)
    positions = mgr.load_holdings()
    option_positions = store.get_open_option_positions()
    cash = store.get_cash_balance()
```
替换为：
```python
    try:
        book = load_sheet_book()
    except SheetBookError as e:
        err_msg = (
            "⚠️ Portfolio Intelligence: Google Sheet 持仓读取失败\n"
            "{}\n本次报告未生成（不回退旧数据）".format(e)
        )
        logger.error("sheet book load failed: %s", e)
        _send_private_report(err_msg, dry_run=dry_run)
        raise

    store = get_store()
    mgr = PortfolioManager(store=store)
    positions = [_position_from_sheet(mgr, h) for h in book.holdings]
    markets = book.markets()
    sheet_prices = book.sheet_prices()
    leaps_symbols = {h.symbol for h in book.holdings if h.is_leaps}
    option_positions = store.get_open_option_positions()  # 期权仍读 company.db（Boss 拍板）
    cash = book.cash_usd
    total_capital = book.total_capital_usd or TOTAL_CAPITAL_USD
```

- [ ] **Step 5: HK / US 路由改用 Market 列**

:1058 原 `hk_symbols = [p.symbol for p in positions if is_hk_ticker(p.symbol)]` →
```python
    hk_symbols = [s for s, m in markets.items() if m == "HK"]
```
:1078 原 `us_symbols = [p.symbol for p in positions if not is_hk_ticker(p.symbol)]` →
```python
    us_symbols = [
        s for s, m in markets.items()
        if m == "US" and s not in leaps_symbols
        and s not in SHEET_PRICED_US_SYMBOLS
    ]
```
（`is_hk_ticker`/`to_yfinance_ticker` 函数保留不删，现有测试依赖。）

- [ ] **Step 6: 无价兜底改为 sheet 价格优先（:1125-1129）**

原代码：
```python
    # Mark symbols with no price at all
    for p in positions:
        if p.symbol not in price_latest and p.cost_basis > 0:
            price_latest[p.symbol] = p.cost_basis
            no_price_symbols.append(p.symbol)
```
替换为：
```python
    # Price fallback: live/T-1 (already in price_latest) -> sheet price -> cost
    sheet_priced_symbols = []
    for p in positions:
        if p.symbol in price_latest:
            continue
        sheet_price = sheet_prices.get(p.symbol, 0)
        if sheet_price > 0:
            price_latest[p.symbol] = sheet_price
            sheet_priced_symbols.append(p.symbol)
        elif p.cost_basis > 0:
            price_latest[p.symbol] = p.cost_basis
            no_price_symbols.append(p.symbol)
```

- [ ] **Step 7: NAV + weights 内联计算（:1152-1157）**

原代码：
```python
    # NAV + weights (now with HK + option prices)
    nav = mgr.get_total_nav(price_latest, option_prices)
    invested = nav - cash
    positions_refreshed = mgr.refresh_prices(price_latest, option_prices)

    weights = {p.symbol: p.current_weight for p in positions_refreshed}
```
替换为（与 manager.py:562-578 语义逐项等价，但数据源是 sheet positions；mgr 两个方法 store-bound 不可用）：
```python
    # NAV + weights — inline (mgr.get_total_nav/refresh_prices re-read company.db)
    option_mv = mgr.get_option_market_value(option_prices)  # options stay on company.db
    for p in positions:
        p.current_price = price_latest.get(p.symbol, 0)
    stock_mv = sum(p.market_value for p in positions)
    nav = stock_mv + option_mv + cash
    invested = nav - cash
    for p in positions:
        p.current_weight = (p.market_value / nav) if nav > 0 else 0
    positions_refreshed = positions

    weights = {p.symbol: p.current_weight for p in positions_refreshed}
```

- [ ] **Step 8: 分母换动态值**

:1200-1205 两处 `TOTAL_CAPITAL_USD` → `total_capital`：
```python
    position_details = build_stock_position_details(
        positions_refreshed, nav, total_capital
    )
    option_details = build_option_position_details(
        option_positions, option_prices, nav, total_capital
    )
```
summary dict（:1268-1277）四处：
```python
        "total_capital": total_capital,
        "tracked_nav_total_pct": nav / total_capital if total_capital else 0,
        ...
        "invested_total_pct": invested / total_capital if total_capital else 0,
        "cash": cash,
        "cash_pct": cash / nav if nav > 0 else 0,
        "cash_total_pct": cash / total_capital if total_capital else 0,
```
（保持其余 key 原样，只把 `TOTAL_CAPITAL_USD` 替换为 `total_capital`。）

- [ ] **Step 9: snapshot line 换 sheet 时间戳（:1291-1302）**

原代码：
```python
    et_now = datetime.now(ZoneInfo("America/New_York"))
    positions_as_of = get_positions_as_of(store)
    latest = positions_as_of["latest"]
    oldest_open_option = positions_as_of["oldest_open_option"]
    snapshot_line = (
        f"📍 NAV 快照 ET {et_now.strftime('%Y-%m-%d %H:%M')} "
        f"| positions as of {latest or 'unknown'} "
        f"| live {len(stock_live_result.prices)}/{len(us_symbols)} "
        f"| signals as of {signals_as_of or 'unknown'}"
    )
    if oldest_open_option and oldest_open_option != latest:
        snapshot_line += f" | oldest open option {oldest_open_option}"
```
替换为：
```python
    et_now = datetime.now(ZoneInfo("America/New_York"))
    positions_as_of = get_positions_as_of(store)
    oldest_open_option = positions_as_of["oldest_open_option"]
    book_ts = book.fetched_at.strftime("%Y-%m-%d %H:%M")
    snapshot_line = (
        f"📍 NAV 快照 ET {et_now.strftime('%Y-%m-%d %H:%M')} "
        f"| book(sheet) as of {book_ts} "
        f"| live {len(stock_live_result.prices)}/{len(us_symbols)} "
        f"| signals as of {signals_as_of or 'unknown'}"
    )
    if oldest_open_option:
        snapshot_line += f" | oldest open option {oldest_open_option}"
    if sheet_priced_symbols:
        snapshot_line += f" | sheet-priced: {','.join(sheet_priced_symbols)}"
```
（其后 fallback / opt / credit 片段不动。）

- [ ] **Step 10: format_report 动态分母（:463-466）**

原代码：
```python
        lines.append(
            f"追踪NAV: ${summary['total_nav']:,.0f} "
            f"({summary.get('tracked_nav_total_pct', 0):.1%} of $5M)"
        )
```
替换为（`total_capital` 局部变量已在 :461 存在）：
```python
        lines.append(
            f"追踪NAV: ${summary['total_nav']:,.0f} "
            f"({summary.get('tracked_nav_total_pct', 0):.1%} of "
            f"{_format_usd_compact(total_capital)})"
        )
```

- [ ] **Step 11: 跑新测试 + PI 全量测试**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/test_portfolio/ -q`
Expected: 全绿（对照 Task 0 基线，不允许新增 fail）

- [ ] **Step 12: 语法 + import 烟测**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -c "import scripts.portfolio_intelligence; import portfolio.holdings.sheet_book; print('ok')"`
Expected: `ok`

- [ ] **Step 13: Commit**

```bash
git add scripts/portfolio_intelligence.py tests/test_portfolio/test_intelligence.py
git commit -m "feat(pi): switch stock holdings/cash/capital source to Google Sheet book of record"
```

---

### Task 7: 本地验收（dry-run 对账 + 隐私扫描）

**Files:** 无代码改动（如发现 bug 回到对应 Task 修）

- [ ] **Step 1: 本地 dry-run（真实 sheet，MarketData 本地失败属预期 → US 走 T-1 → sheet 价兜底）**

Run: `cd <worktree> && set -a && source "/Users/owen/CC workspace/Finance/.env" && set +a && FINANCE_ENV=local "/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/portfolio_intelligence.py --dry-run --allow-local 2>&1 | tail -60`

Expected（逐项人工核对，对照当日 sheet）:
- 持仓清单与 sheet Summary_OSV 一致（含 LEAPS 两行、KR/HK/篮子）
- 现金 ≈ sheet `现金合计`；total capital ≈ Sheet26 `total`（不再是 $5M/$6.6M 静态值）
- KR 码不出现在 HK/yfinance 日志（路由 bug 已修）
- snapshot line 含 `book(sheet) as of` 和 `sheet-priced: ...`
- 期权区块与改前一致（company.db 期权全数展示）

- [ ] **Step 2: 隐私扫描（sheet ID 前 8 位不得出现在代码/测试/日志）**

Run: `SID=$(grep -o 'PORTFOLIO_SHEET_ID=.*' "/Users/owen/CC workspace/Finance/.env" | cut -d= -f2 | cut -c1-8) && grep -rn "$SID" --include="*.py" --include="*.txt" --include="*.md" . | grep -v ".env"`
Expected: 零命中

- [ ] **Step 3: 全仓测试烟测（确认无 import 级破坏）**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python -m pytest tests/ -q -x --co > /dev/null && echo "collect ok"`
Expected: `collect ok`（collection 无错误）；再跑 `pytest tests/test_portfolio/ tests/test_portfolio_store.py -q` 全绿

- [ ] **Step 4: 报告 Boss 验收结果，等 merge 指示**（铁律：不擅自 merge/push）

---

### Task 8: 部署（Boss 批准 merge 后）

**Files:** 云端 `/root/workspace/Finance`（代码经 git；secret 手动）

- [ ] **Step 1: merge 到 main + push**（仅在 Boss 明确批准后）
- [ ] **Step 2: 云端拉代码 + 装依赖**

Run: `ssh -4 aliyun "cd /root/workspace/Finance && git pull && pip3 install 'openpyxl>=3.1' && python3 -c 'import openpyxl; print(openpyxl.__version__)'"`
Expected: 打印版本号

- [ ] **Step 3: 云端 .env 手动加 `PORTFOLIO_SHEET_ID`**（值从本地 .env 取；绝不经 git/不写进任何 tracked 文件；用 stdin 方式避免命令行明文：`ssh -4 aliyun 'cat >> /root/workspace/Finance/.env'` 然后粘贴一行后 Ctrl-D）
- [ ] **Step 4: 云端 dry-run 验收**

Run: `ssh -4 aliyun "cd /root/workspace/Finance && set -a && source .env && set +a && python3 scripts/portfolio_intelligence.py --dry-run 2>&1 | tail -40"`
Expected: live quotes 命中数 == US 非 LEAPS 非篮子持仓数；现金/total 与 sheet 一致；期权区块与改前一致

- [ ] **Step 5: 首晚 cron 观察（22:00 SGT）**：Telegram PDF 与 sheet 对账；如有坑记 `docs/issues/`

---

## 已知遗留（本次 scope 外，明示不修）

- terminal `portfolio_status` / exposure / benchmark review 仍读过期 company.db——后续可复用 `sheet_book.py` 迁移
- company.db holdings/cash 表不动不删（其他消费者还在用）
- KR/Crypto/篮子/LEAPS 价格新鲜度 = sheet 导出时烘焙值（snapshot line 有 `sheet-priced` 标注）
