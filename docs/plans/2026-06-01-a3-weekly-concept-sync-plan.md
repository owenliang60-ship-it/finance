# A3 Weekly Concept Sync — Implementation Plan

> **For agentic workers:** 按 task 顺序执行。Task 1-5 是 Python，**严格串行**（同改 `scripts/build_company_concept_registry.py`）。Task 6-8 改不同文件可并行。每个 task TDD：写失败测试 → 跑红 → 实现 → 跑绿 → commit。
>
> **执行边界（Boss 离开期间）**：只交付**代码 + 测试 + branch commit**。**不**部署云端 cron、**不**对生产/本地 `market.db` 跑 bootstrap 或 weekly-sync、**不** sync、**不** merge/push。这些是部署事项，记 ongoing.md 等 Boss 回来。

**Goal:** 给 concept registry 加一个周频 `--weekly-sync` 子命令 + cron 接入，让 registry 跟随 extended_universe 漂移自动对齐——确定性（rule/anchor）drift-in 自动增量落库，LLM 进 review 队列，CSV ⇔ DB 一致性自检，非阻塞 + Telegram 摘要。

**Architecture:** 在 `scripts/build_company_concept_registry.py` 加 `weekly_sync()` 函数 + `--weekly-sync` CLI，**复用**现有 `_classify_v2` / `refresh_profiles` / `_row_to_csv` / `_row_to_db` / `save_to_market_db` / `_write_review_manifest` / `_backup_sqlite`。增量 upsert 不 `rebuild_concept_tree`（不 wipe）。cron wrapper 加非阻塞 step 7；`sync_to_cloud.sh` 加 canonical CSV pull。

**Tech Stack:** Python 3.10（云端）/ 3.12（本地）兼容；SQLite (WAL)；pytest；bash cron。

**北极星对齐:** 数据层（concept registry = 分析层展示地基）的周频治理自动化（`docs/design/north-star.md` 第一层；无 R 编号体系）。

**上位设计:** `docs/plans/2026-06-01-a3-weekly-concept-sync-design.md` v2（D1-D4 决策 + §4.1 一致性策略 + 6 findings 修订）。

---

## 已验证的复用点（全部 grep 核对，标 source line）

| 复用对象 | 位置 | 契约 |
|----------|------|------|
| `ConceptRegistry(taxonomy_path=, watchlist_path=)` | build:1398-1401 | 构造 registry，taxonomy/watchlist 路径 |
| `_classify_v2(registry, profile, taxonomy) -> dict` | build:233-262 | classify + LLM drop-in；返回 source ∈ {manual,rule,llm,llm_failed,llm_fallback}；l1/l2/l3 是 **concept_id** |
| `refresh_profiles(symbols: list[str], profiles_path) -> int` | build:166-230 | FMP profile 拉取，**可传子集**（drift-in），temp+os.replace 原子，1 FMP/symbol |
| `_row_to_csv(row, profile, reason, mcap_usd, concepts_by_id) -> dict` | build:335-373 | classify row → 16 列 CSV dict；l1/l2/l3 写 **label**（id→label via concepts_by_id） |
| `_row_to_db(row) -> dict` | build:417-431 | classify row → upsert 输入 shape（primary/secondary_concept_id=l1/l2，theme_ids=l3） |
| `save_to_market_db(rows, store, db_path) -> int` | build:1178-1203 | 构 display_tags + `upsert_company_concepts`；**不 rebuild、不 backup**（caller 负责 backup） |
| `_write_review_manifest(csv_path, symbols) -> Path` | build:836-853 | 写 `symbols` schema manifest |
| `_load_review_manifest(csv_path) -> set\|None` | build:856-870 | 读 `symbols`（**Task 1 加 full_universe fallback**） |
| `_manifest_path_for(csv_path) -> Path` | build:832 | `<stem>_manifest.json` |
| `_backup_sqlite(db_path, label) -> Path\|None` | build (WAL-safe) | `src.backup(dst)` WAL 安全快照 |
| `_load_universe(path) -> list[str]` | build:1208-1220 | extended_universe.json 是 `{"symbols":[...]}` 格式 |
| `write_review_csv(rows=, csv_path=, taxonomy=, profiles=, market_caps=)` | build:376-414 | 写 review 队列 CSV |
| `MarketStore(db_path).upsert_company_concepts(rows) -> int` | market_store:1669 | `INSERT OR REPLACE` 按 symbol 隔离；FK 要 concepts 表 populated；theme_ids 必 level=3 |
| `send_message(text, channel="group")` | src/telegram_bot.py:70 | 群组推送；`split_message` 拆长文 |
| `REVIEW_CSV_FIELDS`（16 字段） | build:74-91 | CSV 列定义 |
| `SOFT_REVIEW_CONFIDENCE_THRESHOLD = 0.7` / rule conf=0.7 | build:56 / company_concepts:135 | rule 行恒 0.7（非 soft），按 source 过滤即可 |

**关键不变量**（design §5）：weekly-sync deterministic filter = `source ∈ {manual, rule}`（anchor 的 source=="manual"），**不用** `needs_review==0` 门（build:594 那个会含高置信 LLM 行）。rule/anchor concept_id 100% 在 taxonomy（无 FK 风险）；LLM 越界概念被 review_queue 隔离，永不进增量 upsert。

---

## 计划元信息（writing-plans 标准项）

**Confidence:** 高。所有复用点签名 + 行号已 grep 核对（含 Boss 两轮 review 修正的 `save_to_market_db` keyword-only、classify anchor 顺序、openrsync 限制）；新增逻辑全部依赖注入、可纯单测，不触生产数据。主要残余不确定 = 云端首跑（部署阶段，已隔离到「部署待办」，Boss 在场执行）。

**架构图 / 业务流程图 / Alternatives / Risks（完整版）:** 见上位 design `docs/plans/2026-06-01-a3-weekly-concept-sync-design.md` §3 架构 mermaid、§4.1 一致性策略、§5 流程、§7 方案对比（A/B/C，否决理由）、§9 风险表。本 plan 不重画，只补实现期新增风险：

| 实现期风险 | 缓解 |
|------------|------|
| `_weekly_sync_persist` 前向引用（Task 2 用，Task 3 定义） | module-level 运行时解析；Task 2 测试 `store_factory=None` 不触发；Task 3 定义后全链路集成测试覆盖 |
| 增量 upsert 假设 drift-in 只映射已有 concept | rule/anchor concept_id 100% 在 taxonomy（Agent D 核实）；LLM 越界 → review_queue，永不进 upsert；FK 违例会 fail-closed 非污染 |
| 首跑 base CSV 与云端 DB 不一致 → preflight 卡死 | 这是**设计意图**（fail-closed）；部署待办要求 bootstrap 后验证 symbol 集 == DB 后才 live |
| LLM `claude -p` 抽风（A2 已知） | 7a 隔离：classify 异常 → failed bucket（有 artifact），不碰 DB；7b 只落确定性行 |

---

## File Structure

| 文件 | 操作 | 责任 |
|------|------|------|
| `scripts/build_company_concept_registry.py` | Modify | 加 `_read_csv_symbols` / `_normalize_review_csv` / `_db_tag_symbols` / `_append_csv_atomic` / `weekly_sync` / `--weekly-sync` CLI；改 `_load_review_manifest`（issue 031） |
| `tests/test_a3_weekly_concept_sync.py` | Create | weekly-sync 全套单测（drift/split/一致性/落库/队列） |
| `tests/test_concept_manifest_fallback.py` | Create | issue 031 loader fallback 回归 |
| `scripts/broad_universe_cron_wrapper.sh` | Modify | 加 `run_step_nonblocking` + weekly_refresh step 7（**不部署**） |
| `sync_to_cloud.sh` | Modify | pull 加 canonical CSV + manifest（pull-only） |
| `docs/runbooks/a3-llm-queue-review.md` | Create | 手动 LLM 队列 review-apply 防 clobber runbook |
| `docs/issues/030-*.md` / `031-*.md` | Modify | status 更新（A3 已实现） |
| `.claude/ongoing.md` | Modify | A3 状态 + 部署待办 |

---

## Task 1: 基础 helpers + issue 031 loader fallback

**Files:** Modify `scripts/build_company_concept_registry.py`；Create `tests/test_concept_manifest_fallback.py` + 在 `tests/test_a3_weekly_concept_sync.py` 起头放 helper 测试。

- [ ] **Step 1: issue 031 — `_load_review_manifest` 加 fallback**

`scripts/build_company_concept_registry.py:869` 改：
```python
    syms = data.get("symbols") or data.get("full_universe") or []
```
（原 `data.get("symbols") or []`）

- [ ] **Step 2: 加 4 个 module-level helper**（放在 `_manifest_path_for` 附近）

```python
def _read_csv_symbols(csv_path: Path) -> set[str]:
    """Symbol set from a review CSV (any column order)."""
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8") as fh:
        return {
            (r.get("symbol") or "").strip().upper()
            for r in csv.DictReader(fh)
            if (r.get("symbol") or "").strip()
        }


def _db_tag_symbols(store: "MarketStore") -> set[str]:
    """Symbol set currently in company_concept_tags."""
    conn = store._get_conn()
    return {row[0].upper() for row in conn.execute("SELECT symbol FROM company_concept_tags")}


def _normalize_review_csv(src: Path, dst: Path) -> int:
    """Normalize any historical review CSV to the canonical 16-field schema.

    Handles the legacy 17-col header with a DUPLICATE `business_role` column
    (5/24, 5/30) by coalescing duplicates: first non-empty positional value wins.
    Writes via temp + os.replace.
    """
    with src.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise ValueError(f"empty CSV: {src}")
    header, *data = rows
    col_idx: dict[str, list[int]] = {}
    for i, name in enumerate(header):
        col_idx.setdefault(name.strip(), []).append(i)
    out: list[dict[str, str]] = []
    for raw in data:
        rec: dict[str, str] = {}
        for field in REVIEW_CSV_FIELDS:
            val = ""
            for i in col_idx.get(field, []):
                if i < len(raw) and raw[i].strip():
                    val = raw[i]
                    break
            rec[field] = val
        out.append(rec)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        w.writerows(out)
    os.replace(tmp, dst)
    return len(out)


def _append_csv_atomic(csv_path: Path, new_csv_rows: list[dict]) -> None:
    """Append rows to a review CSV atomically, normalizing to REVIEW_CSV_FIELDS."""
    existing: list[dict] = []
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    combined = existing + list(new_csv_rows)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in combined:
            w.writerow({k: row.get(k, "") for k in REVIEW_CSV_FIELDS})
    os.replace(tmp, csv_path)
```
确认文件顶部已 `import os` + `import csv`（grep；若缺补上）。

- [ ] **Step 3: issue 031 回归测试** `tests/test_concept_manifest_fallback.py`

```python
import json
from pathlib import Path
from scripts.build_company_concept_registry import _load_review_manifest, _manifest_path_for


def test_loader_reads_canonical_symbols(tmp_path):
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("symbol\nAAA\n", encoding="utf-8")
    _manifest_path_for(csv_path).write_text(
        json.dumps({"symbols": ["AAA", "BBB"]}), encoding="utf-8"
    )
    assert _load_review_manifest(csv_path) == {"AAA", "BBB"}


def test_loader_falls_back_to_full_universe(tmp_path):
    """Legacy full_universe-schema manifest (issue 031) is now honored."""
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("symbol\nAAA\n", encoding="utf-8")
    _manifest_path_for(csv_path).write_text(
        json.dumps({"full_universe": ["AAA", "BBB"]}), encoding="utf-8"
    )
    assert _load_review_manifest(csv_path) == {"AAA", "BBB"}
```

- [ ] **Step 4: helper 测试**（`tests/test_a3_weekly_concept_sync.py` 顶部）

```python
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
```

- [ ] **Step 5: 跑红 → 实现已在 Step 1-2 → 跑绿**

Run: `.venv/bin/python -m pytest tests/test_concept_manifest_fallback.py tests/test_a3_weekly_concept_sync.py -v`
Expected: PASS（4 测试）

- [ ] **Step 6: Commit**

```bash
git add scripts/build_company_concept_registry.py tests/test_concept_manifest_fallback.py tests/test_a3_weekly_concept_sync.py
git commit -m "feat(concept): A3 Task1 — manifest fallback (issue031) + csv helpers"
```

---

## Task 2: `weekly_sync()` 核心（drift 检测 + 分类 + 拆分）

**Files:** Modify `scripts/build_company_concept_registry.py`；测试加到 `tests/test_a3_weekly_concept_sync.py`。

- [ ] **Step 1: 定义结果 dataclass + 核心函数**（依赖注入便于测试）

```python
from dataclasses import dataclass, field


@dataclass
class WeeklySyncResult:
    drift_in: list[str] = field(default_factory=list)
    churn_out: list[str] = field(default_factory=list)
    auto_saved: list[str] = field(default_factory=list)   # deterministic
    queued: list[str] = field(default_factory=list)        # llm/needs review
    failed: list[str] = field(default_factory=list)        # no profile / classify error
    error: str | None = None                               # fatal (fail-closed)

    def summary_text(self) -> str:
        if self.error:
            return f"⚠️ Concept 周刷失败: {self.error}"
        parts = [
            "🏷️ Concept 周刷",
            f"自动落库 {len(self.auto_saved)} / 待审 {len(self.queued)}"
            f" / churn-out {len(self.churn_out)} / 失败 {len(self.failed)}",
        ]
        if not self.drift_in:
            parts = ["🏷️ Concept 周刷：本周无新增票"]
        return " · ".join(parts)


def _failed_review_row(sym: str) -> dict:
    """Minimal review row for a drift-in symbol we couldn't classify (error)."""
    return {"symbol": sym, "l1": None, "l2": None, "l3_themes": [],
            "business_role": "", "confidence": 0.0, "source": "sync_failed",
            "evidence": "weekly_sync: classify failed", "needs_review": 1}


def weekly_sync(
    *,
    registry: "ConceptRegistry",
    taxonomy: dict,
    canonical_csv: Path,
    extended_universe_path: Path,
    profiles_path: Path,
    market_db_path: Path,
    queue_dir: Path,
    run_date: str,
    classify_fn=_classify_v2,
    refresh_fn=refresh_profiles,
    store_factory=None,
    telegram_fn=None,
) -> WeeklySyncResult:
    """7a 分类 → 7b 增量落库（preflight/postflight）→ 7c 队列 + Telegram(必发).

    Deterministic ({manual,rule}) auto-saved incrementally; LLM/failed queued.
    Design §5 + D2(每周必推)/D3(churn KEEP). Single try/except/finally so the
    Telegram summary fires on success, no-drift, AND fatal error.
    `_load_profiles` confirmed at build:1223; `_classify_v2` hits anchor by
    SYMBOL first (company_concepts:123) — so classify BEFORE any profile gate
    (P1.5), else anchor drift-in would be wrongly dropped.
    """
    res = WeeklySyncResult()
    res._deterministic = []   # list[(row, profile)] → persist
    res._queue = []           # list[(row, profile)] → review CSV
    res._failed_rows = []     # list[dict] → review CSV (failed bucket gets an artifact, P1.4)
    try:
        base = _read_csv_symbols(canonical_csv)
        universe = {s.upper() for s in _load_universe(extended_universe_path)}
        res.drift_in = sorted(universe - base)
        res.churn_out = sorted(base - universe)   # KEEP (D3): not deleted

        if res.drift_in:
            refresh_fn(res.drift_in, profiles_path=profiles_path)   # FMP, delta only
            profiles = _load_profiles(profiles_path)
            for sym in res.drift_in:
                profile = dict(profiles.get(sym) or {})
                profile.setdefault("symbol", sym)
                try:
                    row = classify_fn(registry, profile, taxonomy)   # anchor-by-symbol works w/o profile
                except Exception as exc:                              # P1.3: never bubble out
                    logger.warning("classify failed for %s: %s", sym, exc)
                    res.failed.append(sym)
                    res._failed_rows.append(_failed_review_row(sym))
                    continue
                row["symbol"] = sym
                if row.get("source") in ("manual", "rule"):          # deterministic (NOT needs_review gate)
                    res._deterministic.append((row, profile))
                    res.auto_saved.append(sym)
                else:                                                 # llm / llm_fallback / etc → queue
                    res._queue.append((row, profile))
                    res.queued.append(sym)

            # coverage self-check (issue 030 automated): nothing silently dropped
            accounted = set(res.auto_saved) | set(res.queued) | set(res.failed)
            if accounted != set(res.drift_in):
                res.error = f"coverage gap: drift_in={len(res.drift_in)} accounted={len(accounted)}"
            elif store_factory is not None:
                _weekly_sync_persist(                                 # defined in Task 3
                    res, canonical_csv=canonical_csv, profiles_path=profiles_path,
                    market_db_path=market_db_path, queue_dir=queue_dir,
                    taxonomy=taxonomy, run_date=run_date, store_factory=store_factory)
    except Exception as exc:                                          # P1.3 fatal → still notify
        logger.exception("weekly_sync fatal")
        res.error = f"weekly_sync fatal: {exc}"
    finally:                                                          # P1.2: always push (D2)
        if telegram_fn is not None:
            try:
                telegram_fn(res.summary_text(), channel="group")
            except Exception as exc:
                logger.warning("weekly_sync telegram failed: %s", exc)
    return res
```
> `_weekly_sync_persist` 在 Task 3 定义（module-level，运行时解析，前向引用 OK；Task 2 测试用 `store_factory=None` 不触发该分支）。

- [ ] **Step 2: 单测**（mock classify/refresh，不碰真实 FMP/DB）

```python
from pathlib import Path
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
```

- [ ] **Step 3: 跑绿**

Run: `.venv/bin/python -m pytest tests/test_a3_weekly_concept_sync.py -v -k weekly_sync`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/build_company_concept_registry.py tests/test_a3_weekly_concept_sync.py
git commit -m "feat(concept): A3 Task2 — weekly_sync drift detect + classify split"
```

---

## Task 3: 落库 + 一致性（7b/7c save path）

**Files:** Modify `scripts/build_company_concept_registry.py`；测试加到 `tests/test_a3_weekly_concept_sync.py`。

- [ ] **Step 1: 加 `_weekly_sync_persist()`**（被 `weekly_sync` 在 split 成功后调用）

```python
def _weekly_sync_persist(
    res: "WeeklySyncResult",
    *,
    canonical_csv: Path,
    profiles_path: Path,
    market_db_path: Path,
    queue_dir: Path,
    taxonomy: dict,
    run_date: str,
    store_factory,
) -> None:
    """7b deterministic save (incremental, preflight/postflight) + 7c queue CSV."""
    concepts_by_id = {c["concept_id"]: c["label"] for c in taxonomy.get("concepts", [])}
    profiles = _load_profiles(profiles_path)

    store = store_factory()
    # preflight: canonical CSV ⇔ DB lockstep before mutating
    csv_syms, db_syms = _read_csv_symbols(canonical_csv), _db_tag_symbols(store)
    if csv_syms != db_syms:
        res.error = (f"preflight lockstep broken: csv-only={sorted(csv_syms - db_syms)[:5]} "
                     f"db-only={sorted(db_syms - csv_syms)[:5]}")
        return

    if res._deterministic:
        _backup_sqlite(store.db_path, "pre-weekly-sync")
        db_rows = [_row_to_db(row) for row, _prof in res._deterministic]
        save_to_market_db(rows=db_rows, store=store, market_db_path=market_db_path)  # P1.1 keyword-only
        csv_rows = [
            _row_to_csv(row, dict(profiles.get(row["symbol"].upper()) or {}), "ok", None, concepts_by_id)
            for row, _prof in res._deterministic
        ]
        _append_csv_atomic(canonical_csv, csv_rows)
        _write_review_manifest(canonical_csv, _read_csv_symbols(canonical_csv))
        # postflight: re-verify lockstep
        if _read_csv_symbols(canonical_csv) != _db_tag_symbols(store):
            res.error = "postflight lockstep broken — backup retained, manual review"
            return

    # 7c: queued + failed both get a review artifact (P1.4 — no symbol left only in a counter)
    review_rows = [row for row, _prof in res._queue] + res._failed_rows
    if review_rows:
        review_profiles = {r["symbol"].upper(): dict(p) for r, p in res._queue}
        write_review_csv(
            rows=review_rows, csv_path=queue_dir / f"needs_review_{run_date}.csv",
            taxonomy=taxonomy, profiles=review_profiles,
        )
```
> `_weekly_sync_persist` 由 Task 2 的 `weekly_sync` 在 coverage-check 通过后调用（已布线）。`save_to_market_db` 是 **keyword-only**（build:1177-1182 `def save_to_market_db(*, rows, store, market_db_path)`）。`write_review_csv` 的 `market_caps` 可省（默认 None）。

- [ ] **Step 2: 集成测试**（真实临时 SQLite + 真实 taxonomy + mock FMP/telegram）

```python
def test_weekly_sync_persists_deterministic_incrementally(tmp_path, monkeypatch):
    """End-to-end with a real temp MarketStore seeded via rebuild_concept_tree."""
    import json
    from src.data.market_store import MarketStore
    import scripts.build_company_concept_registry as b

    # real taxonomy + registry
    cfg = Path("config/concepts")
    taxonomy = json.loads((cfg / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
    registry = b.ConceptRegistry(taxonomy_path=cfg / "concept_taxonomy_v2.json",
                                 watchlist_path=cfg / "concept_watchlist.json")

    db = tmp_path / "market.db"
    store = MarketStore(db)
    store.rebuild_concept_tree(registry.concepts)           # populate concepts tree
    # seed one existing tag so base lockstep holds
    seed_l1 = taxonomy["concepts"][0]["concept_id"]
    store.upsert_company_concepts([{
        "symbol": "AAA", "primary_concept_id": seed_l1, "theme_ids": [],
        "display_tags": "", "business_role": "", "confidence": 1.0,
        "source": "manual", "evidence": "seed", "needs_review": 0}])

    canon = tmp_path / "canon.csv"
    # canonical CSV must match DB (lockstep): one row AAA
    _write(canon, b.REVIEW_CSV_FIELDS, [["ok", "AAA"] + [""] * 14])

    # pick a real (sector,industry) from industry_map that yields a rule hit
    imap = taxonomy["industry_map"]; key = next(iter(imap)); sector, industry = key.split("|", 1)
    uni = tmp_path / "uni.json"; uni.write_text(json.dumps({"symbols": ["AAA", "RULEX"]}), encoding="utf-8")

    monkeypatch.setattr(b, "_load_profiles",
        lambda p: {"RULEX": {"symbol": "RULEX", "sector": sector, "industry": industry,
                             "companyName": "Rule Co", "description": "x"}})

    sent = []
    res = b.weekly_sync(
        registry=registry, taxonomy=taxonomy, canonical_csv=canon,
        extended_universe_path=uni, profiles_path=tmp_path / "prof.json",
        market_db_path=db, queue_dir=tmp_path, run_date="2026-06-01",
        refresh_fn=lambda syms, profiles_path: 0,
        store_factory=lambda: store,
        telegram_fn=lambda text, channel: sent.append((text, channel)))

    assert res.error is None
    assert "RULEX" in res.auto_saved
    assert _db_tag_symbols(store) == {"AAA", "RULEX"}        # incremental, AAA preserved
    assert _read_csv_symbols(canon) == {"AAA", "RULEX"}      # CSV ⇔ DB lockstep
    assert sent and sent[0][1] == "group"                    # telegram group summary


def test_weekly_sync_preflight_fails_closed(tmp_path, monkeypatch):
    """CSV ⇔ DB mismatch at preflight → fail-closed, no mutation."""
    import json
    from src.data.market_store import MarketStore
    import scripts.build_company_concept_registry as b
    cfg = Path("config/concepts")
    taxonomy = json.loads((cfg / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
    registry = b.ConceptRegistry(taxonomy_path=cfg / "concept_taxonomy_v2.json",
                                 watchlist_path=cfg / "concept_watchlist.json")
    db = tmp_path / "m.db"; store = MarketStore(db); store.rebuild_concept_tree(registry.concepts)
    # DB empty but canonical CSV has a row → lockstep broken
    canon = tmp_path / "canon.csv"; _write(canon, b.REVIEW_CSV_FIELDS, [["ok", "ZZZ"] + [""] * 14])
    uni = tmp_path / "uni.json"; uni.write_text(json.dumps({"symbols": ["ZZZ", "NEW"]}), encoding="utf-8")
    monkeypatch.setattr(b, "_load_profiles",
        lambda p: {"NEW": {"symbol": "NEW", "sector": "Tech", "industry": "Semis"}})
    res = b.weekly_sync(
        registry=registry, taxonomy=taxonomy, canonical_csv=canon,
        extended_universe_path=uni, profiles_path=tmp_path / "p.json",
        market_db_path=db, queue_dir=tmp_path, run_date="2026-06-01",
        refresh_fn=lambda syms, profiles_path: 0, store_factory=lambda: store,
        classify_fn=lambda r, prof, tax: {"symbol": prof["symbol"], "source": "rule",
                                          "l1": registry.concepts[0]["concept_id"], "l2": None, "l3_themes": []})
    assert res.error and "preflight" in res.error
    assert _db_tag_symbols(store) == set()                   # no mutation
```

- [ ] **Step 3: 跑绿**

Run: `.venv/bin/python -m pytest tests/test_a3_weekly_concept_sync.py -v`
Expected: PASS（全部）

- [ ] **Step 4: Commit**

```bash
git add scripts/build_company_concept_registry.py tests/test_a3_weekly_concept_sync.py
git commit -m "feat(concept): A3 Task3 — incremental save + CSV⇔DB lockstep + queue"
```

---

## Task 4: `--weekly-sync` CLI 接入

**Files:** Modify `scripts/build_company_concept_registry.py`（argparse + main 分支）。

- [ ] **Step 1: argparse 加参数**（与现有 `--reclassify` 风格一致）

```python
    parser.add_argument("--weekly-sync", action="store_true",
                        help="Sync registry to current extended_universe drift (A3): "
                             "deterministic auto-save, LLM queue, Telegram summary.")
    parser.add_argument("--canonical-csv", type=Path, default=None,
                        help="Canonical reviewed CSV (default: reports/concept_registry/reviewed_current.csv)")
```

- [ ] **Step 2: main() 加分支**（在 registry 构造之后、其他模式之前；非阻塞由 cron 负责，这里正常 return）

```python
    if args.weekly_sync:
        import datetime as _dt
        canonical_csv = args.canonical_csv or (
            PROJECT_ROOT / "reports" / "concept_registry" / "reviewed_current.csv")
        taxonomy = json.loads(
            (cfg_dir / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
        res = weekly_sync(
            registry=registry, taxonomy=taxonomy, canonical_csv=canonical_csv,
            extended_universe_path=extended_universe_path, profiles_path=profiles_path,
            market_db_path=market_db_path, queue_dir=canonical_csv.parent,
            run_date=_dt.date.today().isoformat(),
            store_factory=_open_store, telegram_fn=send_message)
        print(res.summary_text())
        return 2 if res.error else 0
```
确认顶部已 `from src.telegram_bot import send_message`（缺则补，import 放文件顶 import 区）。

- [ ] **Step 3: CLI smoke 测试**（monkeypatch weekly_sync，验证布线 + 退出码）

```python
def test_cli_weekly_sync_wires_and_exits(monkeypatch, tmp_path):
    import scripts.build_company_concept_registry as b
    called = {}
    def fake_ws(**kw):
        called.update(kw); return b.WeeklySyncResult(drift_in=[], auto_saved=[])
    monkeypatch.setattr(b, "weekly_sync", fake_ws)
    monkeypatch.setattr(b, "send_message", lambda *a, **k: True)
    rc = b.main(["--weekly-sync", "--data-root", str(tmp_path)])
    assert rc == 0
    assert called["telegram_fn"] is b.send_message
    assert called["store_factory"] is not None
```
> grep 确认 `main` 是否接受 `argv` 参数（若签名是 `main()` 读 `sys.argv`，测试改用 `monkeypatch.setattr(sys, "argv", [...])`）。

- [ ] **Step 4: 跑绿 + Commit**

```bash
.venv/bin/python -m pytest tests/test_a3_weekly_concept_sync.py -v -k cli
git add scripts/build_company_concept_registry.py tests/test_a3_weekly_concept_sync.py
git commit -m "feat(concept): A3 Task4 — --weekly-sync CLI"
```

---

## Task 5: bootstrap canonical CSV（代码，**不对真实数据跑**）

**Files:** Modify `scripts/build_company_concept_registry.py`（加 `--bootstrap-canonical SRC`）。

- [ ] **Step 1: argparse + main 分支**

```python
    parser.add_argument("--bootstrap-canonical", type=Path, default=None,
                        help="Normalize SRC review CSV → canonical reviewed_current.csv "
                             "(one-time seed; verify symbol set vs DB before going live).")
```
```python
    if args.bootstrap_canonical:
        canonical_csv = args.canonical_csv or (
            PROJECT_ROOT / "reports" / "concept_registry" / "reviewed_current.csv")
        n = _normalize_review_csv(args.bootstrap_canonical, canonical_csv)
        manifest_syms = _read_csv_symbols(canonical_csv)
        _write_review_manifest(canonical_csv, manifest_syms)
        print(f"bootstrap: {n} rows -> {canonical_csv} ({len(manifest_syms)} symbols)")
        # verification hint (NOT auto-run against live DB)
        print("VERIFY before live: symbol set must == DB company_concept_tags symbols")
        return 0
```

- [ ] **Step 2: 测试**（用 fixture CSV，不碰真实 DB）

```python
def test_bootstrap_normalizes_and_writes_manifest(tmp_path):
    import scripts.build_company_concept_registry as b
    src = tmp_path / "legacy.csv"
    _write(src, ["review_reason", "symbol", "business_role"] + b.REVIEW_CSV_FIELDS[2:],
           [["ok", "AAA", "dup"] + [""] * 14])
    out = tmp_path / "canon.csv"
    rc = b.main(["--bootstrap-canonical", str(src), "--canonical-csv", str(out)])
    assert rc == 0
    assert b._read_csv_symbols(out) == {"AAA"}
    assert b._load_review_manifest(out) == {"AAA"}
```

- [ ] **Step 3: 跑绿 + Commit**

```bash
.venv/bin/python -m pytest tests/test_a3_weekly_concept_sync.py -v -k bootstrap
git add scripts/build_company_concept_registry.py tests/test_a3_weekly_concept_sync.py
git commit -m "feat(concept): A3 Task5 — --bootstrap-canonical seed"
```

---

## Task 6: cron wrapper 非阻塞 step 7（代码，**不部署**）

**Files:** Modify `scripts/broad_universe_cron_wrapper.sh`。

- [ ] **Step 1: 加 `run_step_nonblocking`**（紧跟现有 `run_step` 之后）

```bash
run_step_nonblocking() {
  local name="$1"
  shift
  log "BEGIN $name (nonblocking)"
  if "$@" >> "$LOG" 2>&1; then
    log "OK $name"
  else
    local rc=$?
    log "WARN $name rc=$rc (nonblocking, continuing)"
  fi
}
```

- [ ] **Step 2: weekly_refresh 末尾加 step 7**（在 `refresh_extended` 之后）

```bash
    run_step "refresh_extended" "$PYTHON" -m src.data.extended_universe_manager --refresh
    run_step_nonblocking "concept_weekly_sync" "$PYTHON" \
      scripts/build_company_concept_registry.py --weekly-sync
```

- [ ] **Step 3: 语法检查**

Run: `bash -n scripts/broad_universe_cron_wrapper.sh`
Expected: 无输出（语法 OK）

- [ ] **Step 4: Commit**

```bash
git add scripts/broad_universe_cron_wrapper.sh
git commit -m "feat(concept): A3 Task6 — non-blocking weekly concept_sync cron step (undeployed)"
```

---

## Task 7: sync_to_cloud.sh canonical CSV pull（代码）

**Files:** Modify `sync_to_cloud.sh`（pull_from_cloud，pull-only）。

- [ ] **Step 1: pull 加 canonical CSV + manifest**（在 fundamental/ rsync 之后，universe.json merge 之前）

```bash
    # 3b. pull canonical reviewed_current.csv + manifest 云端→本地 (云端独占写, 仅 pull)
    #     openrsync (macOS) 不支持 GNU --ignore-missing-args → ssh test 守卫 + scp
    info "拉取 concept_registry canonical CSV..."
    local _cc="reports/concept_registry"
    if ssh "$REMOTE_HOST" "test -f '$REMOTE_DIR/$_cc/reviewed_current.csv'"; then
        mkdir -p "$LOCAL_DIR/$_cc"
        scp "$REMOTE/$_cc/reviewed_current.csv" "$LOCAL_DIR/$_cc/reviewed_current.csv"
        scp "$REMOTE/$_cc/reviewed_current_manifest.json" \
            "$LOCAL_DIR/$_cc/reviewed_current_manifest.json" 2>/dev/null || \
            warn "canonical manifest 暂缺，跳过"
    else
        warn "canonical CSV 云端暂不存在（A3 首跑前正常），跳过"
    fi
```
> openrsync 不支持 `--ignore-missing-args`（本机实测，design finding P1.6）→ 用 `ssh "$REMOTE_HOST" "test -f ..."` 守卫 + `scp`（均可用）。不进 `check_file_size`（小 CSV 的 50% ratio 会误报）；不进 `push_to_cloud`（云端独占写）。`warn()` 已确认存在于 `sync_to_cloud.sh:31`。`REMOTE_HOST` ssh 若遇 LAN bind 问题，沿用脚本既有 ssh 调用模式（不在本 task 处理）。

- [ ] **Step 2: 语法检查**

Run: `bash -n sync_to_cloud.sh`
Expected: 无输出

- [ ] **Step 3: Commit**

```bash
git add sync_to_cloud.sh
git commit -m "feat(concept): A3 Task7 — pull canonical concept CSV (pull-only)"
```

---

## Task 8: runbook + issue/ongoing 更新

**Files:** Create `docs/runbooks/a3-llm-queue-review.md`；Modify `docs/issues/030-*.md`、`docs/issues/031-*.md`。（ongoing.md 由收尾统一更新。）

- [ ] **Step 1: 写 runbook**（防 clobber 的手动 review-apply 流程）

`docs/runbooks/a3-llm-queue-review.md` 内容要点：
1. weekly-sync 每周把 LLM/失败票写 `reports/concept_registry/needs_review_<date>.csv` 并 Telegram 通知。
2. Boss 编辑该队列 CSV（填/改 l1/l2/l3 label）。
3. **防 clobber 关键**：apply 前必须把 reviewed 队列行**合并进 canonical `reviewed_current.csv`**（不是单独 apply 队列），否则 `--read-reviewed-csv --save` 的 wipe+rebuild 会清掉 cron 自动落的行。
4. 在**云端**跑（market.db 云端独占）：`python3 scripts/build_company_concept_registry.py --read-reviewed-csv reports/concept_registry/reviewed_current.csv --save`。
5. WAL-safe backup + `sync_to_cloud.sh --pull` 回本地。
6. 偶尔 `git add reviewed_current.csv` 提交快照（走"push 先确认"）。

- [ ] **Step 2: 更新 issue 030/031 status**

- issue 030：status 加 `A3 已实现自动双向对账（coverage self-check），pin 手段退役`。
- issue 031：status 加 `loader fallback 已实现（Task 1），retroactive 修好历史 manifest`。

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/a3-llm-queue-review.md docs/issues/030-*.md docs/issues/031-*.md
git commit -m "docs(concept): A3 Task8 — LLM-queue runbook + issue030/031 status"
```

---

## 最终验证（workflow 末尾，非 task）

- [ ] 全量 targeted 回归：`.venv/bin/python -m pytest tests/test_a3_weekly_concept_sync.py tests/test_concept_manifest_fallback.py tests/test_build_concept_registry.py tests/test_company_concepts.py -v`
- [ ] 无回归：现有 concept registry 测试全过。
- [ ] `bash -n` 两个 shell 脚本。
- [ ] **不**跑 live weekly-sync / bootstrap / sync / cron 部署（部署事项 → ongoing.md）。

## 部署待办（Boss 回来后，记 ongoing.md）

1. 选定 bootstrap 源 CSV（最新 committed `extended_pool_tags_*.csv`），**云端**跑 `--bootstrap-canonical` 生成 `reviewed_current.csv`，**验证 symbol 集 == 云端 DB tags 集**（967）后才算 live。
2. 云端 crontab 接入（weekly_refresh 已含 step 7，确认 wrapper 已部署 + 一次手动 dry 验证）。
3. 首跑消化 5/30 的 19 deferred 票（验收用例，design §10.1）。
4. `sync_to_cloud.sh --pull` 验证 canonical CSV 回本地。
