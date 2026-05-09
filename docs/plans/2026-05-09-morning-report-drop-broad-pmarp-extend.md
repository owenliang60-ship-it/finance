# Plan: 晨报移除 Broad Universe 筛选 + PMARP 三类信号

**日期**: 2026-05-09
**作者**: CC（在 Boss 指令下）
**状态**: v3 — Boss 第三轮反馈修订后审批

## 修订历史
- v1 (2026-05-09): 初稿
- v2 (2026-05-09): Boss 第一轮反馈 5 处修订（S2 解耦 / post-filter 主逻辑 / fail-fast / RKLB fixture / subtitle）
- v3 (2026-05-09): Boss 第二轮反馈 4 处修订：
  - **[P1]** Dollar Volume 图片版受 LAYER_ORDER 副作用 — `_normalize_dv_items` 也要 filter
  - [P1] S2 新测试 fixture 5 只 < min_symbols=50 不可行 — 改 60 只 + e2e 路径 mock
  - [P2] `assert` 在 -O 模式被移除 — 改用 `raise ValueError` 做生产 fail-fast
  - [P2] `--symbols` override 行为：**Boss 决策 override 全入 pool 层**（ad-hoc watchlist 视为特权），下游 fail-fast 不需要 override 例外分支

## 背景与动机

Boss 指示晨报做两处调整：
1. **删除所有 broad universe 的筛选**，扫描池缩到 extend ($10B+) 即可
2. **PMARP 信号增加 upcross 98% 和 downcross 98%**，与现有 upcross 2% 合并展示

直接动机：广扫层（$1B-$10B 的 broad universe）信号噪声大，Boss 不再需要看；PMARP 极强进出（98% 上下穿）也是有价值的择时信号，应进入晨报。

## 改动范围

### 文件
- `scripts/morning_report.py` — 主修改
- `tests/test_morning_report.py` — 测试调整

### 不动
- `scripts/broad_market_scan.py` — broad scan 模块本身保留（其他地方还在用：weekly cron / `save_broad_scan_hits` 历史数据等）
- `src/indicators/pmarp.py` — `analyze_pmarp` 已经返回 4 类穿越，不动
- `_compute_breadth_s2_status_from_price_frames` + `_load_market_db_broad_price_frames` 保留（Section 0 大盘择时仍需 broad MA20 breadth，市场广度指标 ≠ 选股筛选）

### 关键架构原则（v2 新增）

**Section 0 大盘择时的 breadth S2 输入必须与 Section 1-3 的选股扫描 universe 解耦。**

S2 = Broad MA20 参与度（市场广度），是历史校准的择时阈值；选股扫描 universe 缩到 extend 后绝不能让 S2 重新解释为 "Extend MA20 参与度"，否则历史回测的 30% 上穿阈值意义全变。

## 详细 Diff（按文件）

### A. `scripts/morning_report.py`

**A1. Layer constants（行 51-56）+ 防泄漏（v2 [P1] 修订）**

```python
# Before
LAYER_ORDER = ["pool", "extend", "broad"]
LAYER_LABELS = {"pool": "Pool", "extend": "Extend ($10B+)", "broad": "Broad ($1B-$10B)"}
# After
LAYER_ORDER = ["pool", "extend"]
LAYER_LABELS = {"pool": "Pool", "extend": "Extend ($10B+)"}
```

`_layer_for_symbol` 兜底仍返回 "broad" 不动（防御性），但下游必须 fail-fast（v3 [P2] 修订：`assert` → `raise`）：

```python
# _enrich_with_layer 边界
enriched["layer"] = _layer_for_symbol(symbol, metadata, pool_symbols)
if enriched["layer"] not in {"pool", "extend"}:
    raise ValueError(
        f"layer leak: {symbol} classified as {enriched['layer']!r}; "
        f"expected pool|extend after universe post-filter"
    )
```

为什么不用 `assert`：python `-O` / `PYTHONOPTIMIZE=1` 会移除 assert，cron 脚本如果在 -O 模式下跑就丧失保护；语义上 assert 也偏向开发期断言，不适合数据边界。

`_group_by_layer` / `_rows_by_layer_and_bucket` 保留 `setdefault` 兜底（向后兼容），但增加日志：检测到非 pool/extend 时 `logger.warning("layer leak: %s", layer)`，避免静默丢失。

> Boss 反馈 [P1] 原话："如果 LAYER_ORDER=['pool','extend']，低市值 row 会消失，扫描数还可能照算"。修法：filter 在 enrich 边界，不让 row 进入下游。

**A1b. Section 0 S2 输入解耦（v2 [P0] 关键修订）**

`build_market_timing_factor_report(price_frames=...)` 当前签名接受 `price_frames`，主流程传入 `build_market_signal_report` 的 `price_frames`（即扫描 universe 的价格帧）。这次改造后扫描池缩到 extend → S2 会被悄悄换成 Extend MA20 breadth。

修法（二选一，倾向方案 B）：

- **方案 A（小改）**：`build_market_signal_report` 调用 timing 时显式传 `price_frames=None`，强制 `_compute_breadth_s2_status_from_price_frames` 走 market.db broad fallback。`min_symbols=50` 兜底保留作为额外保险。
- **方案 B（推荐，结构更清楚）**：`build_market_timing_factor_report` 内部不再接受 `price_frames` 参数（或参数标记 deprecated），始终调用 `_load_market_db_broad_price_frames()` 获取 broad frames。S2 输入与扫描 universe 完全解耦。

**采用方案 B**。改动点：

```python
# 原签名
def build_market_timing_factor_report(price_frames: dict[str, object] | None = None) -> dict:
    breadth = _compute_breadth_s2_status_from_price_frames(price_frames or {})

# 新签名
def build_market_timing_factor_report() -> dict:
    """S2 always uses broad universe ($1B+) from market.db, decoupled from selection-scan universe."""
    broad_frames = _load_market_db_broad_price_frames()
    breadth = _compute_breadth_s2_status_from_price_frames(
        broad_frames,
        allow_market_db_fallback=False,  # 已经直接给 broad，不需要再 fallback
        source="market_db_broad_price_frames",
    )
```

调用方（行 834）：`build_market_timing_factor_report(price_frames=price_frames)` → `build_market_timing_factor_report()`

**新测试（v3 [P1] 修订）**：
- `test_market_timing_factor_uses_broad_db_regardless_of_scan_frames`
  - 设计要点：方案 B 删了 `price_frames` 参数，函数签名变 `build_market_timing_factor_report()`，测试不能直接传扫描 frames
  - 改成 e2e 路径：mock `_load_market_db_broad_price_frames` 返回 **60 只 broad fixture**（≥ min_symbols=50），构造 60% MA20 上穿；同时 mock `load_price_frames` 返回 5 只 extend fixture（故意构造 0% 上穿）
  - 通过 `build_market_signal_report()` 端到端调用，断言 `result["market_timing_factor"]["breadth_s2"]["source"] == "market_db_broad_price_frames"` 且 `current ≈ 0.6`（来自 broad fixture，证明扫描 frames 不影响 S2）

> Boss 反馈 [P1] 原话："`min_symbols=50`，5 只会返回 insufficient" — 修正 fixture 量级；"如果方案 B 删除 price_frames 参数，测试不要写'传入扫描 frames'" — 改成 mock `load_price_frames` 路径。

**A2. PMARP 信号收集 + criteria（行 776-784, 850-853）**

```python
PMARP_SIGNAL_LABELS = {
    "oversold_recovery": "上穿2%",
    "bullish_breakout": "上穿98%",
    "momentum_fading": "下穿98%",
}
pmarp_raw = []
for symbol, frame in price_dict.items():
    result = analyze_pmarp(frame)
    if result.get("signal") in PMARP_SIGNAL_LABELS:
        pmarp_raw.append({
            "symbol": symbol,
            "value": result.get("current"),
            "previous": result.get("previous"),
            "signal": result.get("signal"),
        })

# return 中 criteria 改为
"criteria": "PMARP 上穿2% / 上穿98% / 下穿98%",
```

排序：先按 `signal` 类型分组（顺序: bullish_breakout → oversold_recovery → momentum_fading），组内按 value 排序。

**A3. PMARP section 展示（行 978-995）**

`format_section_layered_pmarp` 增加"信号"列：

```python
"标的 | 概念 | 业务角色 | 信号 | 当前 | 变化 | 市值"
```

每行显示对应的中文信号标签。

**A4. 删除 broad scan 数据流 + post-filter 是主逻辑（v2 [P1] 修订）+ override 全入 pool 层（v3 [P2] 修订）**

- 行 720-726: import 删除 `BROAD_SCAN_RETURN_THRESHOLD, BROAD_SCAN_RVOL_THRESHOLD, scan_candidates`（保留 `fetch_universe_metadata, load_price_frames`）
- 行 747: `min_mcap_b=1.0` → `min_mcap_b=10.0`（参数虽然在 market_db 模式下被忽略，但保留语义文档作用，避免日后切回 yf_screen 时遗漏）

**默认路径（无 override）**必须 post-filter：

```python
universe_cache = fetch_universe_metadata(as_of_date=..., min_mcap_b=10.0)
raw_metadata = universe_cache.get("stocks", {})
# Post-filter: market_db 模式下 fetch 忽略 min_mcap_b 用 $1B 阈值，必须本地强制 ≥$10B
metadata = {
    sym: meta for sym, meta in raw_metadata.items()
    if (meta.get("marketCap") or 0) >= EXTENDED_LAYER_MIN_MCAP
}
# Pool 兜底（pool 标的不论市值都要扫）
for sym in pool_symbols:
    if sym not in metadata:
        metadata[sym] = {
            "marketCap": raw_metadata.get(sym, {}).get("marketCap"),
            "shortName": sym, "longName": sym, "exchange": "DB",
        }
symbols = sorted(metadata.keys())
```

**Override 路径（v3 新增）**：override 提供的所有 symbol 强制视为 pool（ad-hoc watchlist 特权）。修改方式：

```python
if symbols_override:
    symbols = sorted({s.strip().upper() for s in symbols_override if s.strip()})
    # ... bulk_caps + metadata 构造同原逻辑
    # v3 新增：override 模式下把所有 symbol 都并入 pool_symbols，绕过 mcap 分层
    pool_symbols = pool_symbols | set(symbols)
    logger.info("override mode: %d symbols treated as pool layer", len(symbols))
```

这样 `_layer_for_symbol` 会一律返回 `"pool"`，下游 fail-fast 不会触发，broad 标的（如 OKLO）调试时正常渲染到 Pool 桶。

`--symbols` 帮助文本更新：`"指定股票代码，逗号分隔（override 模式：所有指定标的视为 pool 层，绕过 mcap 分层）"`

- 行 759, 761-773: 删除 `broad_scan = scan_candidates(...)` + db_rows + `save_broad_scan_hits` 调用
- 行 794-808: `signal_symbols`/`broad_hits` 不再 include broad triggered
- 行 842-849: return dict 删除 `broad_scan` key

**新测试 [P1]**：`test_build_market_signal_report_filters_to_extend_plus_pool`
- mock `fetch_universe_metadata` 返回 3 只：A (8B), B (12B), C (50B)
- mock `get_symbols()` 返回 `{"D"}`，D 的 marketCap=2B（pool 兜底场景）
- mock `load_price_frames` 拦截调用，捕获参数
- 断言 `load_price_frames` 接收的 symbols set == `{B, C, D}`（A 被 filter 掉，D 因 pool 保留）

**新测试 [v3 P2]**：`test_build_market_signal_report_override_promotes_all_to_pool`
- 调用 `build_market_signal_report(symbols_override=["OKLO"])`，mock OKLO mcap=8B
- **强制制造 hit**（避免空断言）：monkeypatch `analyze_pmarp` 让 OKLO 返回 `signal="oversold_recovery", current=2.5, previous=1.7`
- 断言：
  - `result["pmarp"]["hits"]` 至少有一条 OKLO 记录
  - 该 hit 的 `layer == "pool"`（不是 broad）
  - `result["layer_counts"]["pool"] == 1`，`result["layer_counts"]["extend"] == 0`
- 通过这个测试锁定 override 等同 pool 的语义

**A5. 删除 Section 1 渲染 + 视觉 subtitle 修订（v2 [P2] 修订）**

- 行 959-975: 删除 `format_section_broad_signal` 函数
- 行 1257-1278: `build_morning_visual_sections` 删除 `01_broad_signal` block
- 行 1926: `format_morning_report` 删除 broad section 调用
- 行 1212: `common_subtitle = "信号日 {} | Pool / Extend / Broad 分层，层内按题材聚类"` → `"信号日 {} | Pool / Extend 分层，层内按题材聚类"`
- 行 932 + 行 1229: 表头 `"S2参与度(broad)"` 保留不变（这一列就是 broad MA20 breadth，标注准确）

**B 节测试断言新增**：grep 确认 `"Broad" not in result` for 选股 sections 的 subtitle，但 Section 0 的 `"S2参与度(broad)"` 应保留。

**A7. Dollar Volume 也 filter（v3 [P1] 新增）**

`_normalize_dv_items`（行 1099-1136）目前会给所有 DV 排名打 layer，包括 broad 小盘（如 ARM 上市初期 / OKLO 暴涨日）。删 LAYER_ORDER broad 后图片版会静默丢失这些 row。

修法：在 `normalize` 函数返回前过滤掉 broad layer，与选股 sections 一致语义（"广扫筛选删除 = 整张晨报缩到 pool ∪ extend"）：

```python
def normalize(row: dict) -> dict | None:
    # ... 既有逻辑 ...
    item["layer"] = _layer_for_symbol(symbol, layer_meta, pool_symbols)
    if item["layer"] not in {"pool", "extend"}:
        logger.debug("DV row dropped (layer=%s, mcap=%s): %s",
                     item["layer"], item.get("marketCap"), symbol)
        return None
    return item

return {
    "rankings": [r for r in (normalize(row) for row in rankings) if r is not None],
    "new_faces": [r for r in (normalize(row) for row in new_faces) if r is not None],
}
```

> 行为含义：broad 小盘 DV 排名（即使 dollar volume 高）不再进入晨报；与 PMARP/DV/RVOL 选股口径一致。

**新测试 [v3 P1]**：
- `test_dv_section_filters_out_broad_layer`
  - 文本版：`format_section_d` 输入含 broad mcap 标的（如 OKLO mcap=8B）的 dv_result，断言 OKLO 不出现在输出
- `test_dv_visual_block_filters_out_broad_layer`
  - 视觉版：`build_morning_visual_sections` 输入同上 dv_result，断言 dollar_volume block 的 rows 不含 OKLO

**A6. 段落重新编号**

| 现 | 改 |
|----|----|
| 0. 大盘择时因子 | 0. 大盘择时因子 |
| 1. 广扫标准 | _删除_ |
| 2. PMARP 信号 | 1. PMARP 信号 |
| 3. 量能加速 | 2. 量能加速 |
| 4. RVOL 持续放量 | 3. RVOL 持续放量 |

视觉版 slug：`02_pmarp` → `01_pmarp`、`03_dv_acceleration` → `02_dv_acceleration`、`04_rvol_sustained` → `03_rvol_sustained`、`05_dollar_volume` → `04_dollar_volume`。

### B. `tests/test_morning_report.py`

**B1. Import 删除** `format_section_broad_signal`

**B2. `sample_market_signals()` 调整（v2 [P2] 修订）**
- 删除 `broad_scan` key
- `pmarp.criteria`: `"PMARP 上穿 2%"` → `"PMARP 上穿2% / 上穿98% / 下穿98%"`
- `pmarp.hits`: 增加两条新信号样本（bullish_breakout / momentum_fading），各含 `signal` 字段
- `rvol_sustained.hits` 中 `RKLB layer="broad", marketCap=8e9` → **改成 `layer="extend"` 且 `marketCap=12e9`**（marketCap 必须 ≥$10B 才能合理标 extend；不能让 8B 标 extend 制造语义错误测试样本）

**B3. 删除/调整测试**
- `test_broad_signal_groups_by_concept_bucket`、`test_broad_signal_missing_industry_uses_concept_bucket`、`test_bucketed_sections_do_not_truncate_with_more` → 全删
- `test_pmarp_layered_section` → 增加断言：3 类信号都出现 + "上穿2%/上穿98%/下穿98%" 中文标签
- `test_market_signal_report_contains_layered_sections_and_dollar_volume`:
  - 删 `assert "1. 广扫标准" in result`
  - `"2. PMARP 信号"` → `"1. PMARP 信号"`，依次类推
- `test_visual_sections_group_rows_by_layer_and_bucket`:
  - slug list 改为 `["00_market_timing_factor", "01_pmarp", "02_dv_acceleration", "03_rvol_sustained", "04_dollar_volume"]`
  - layer set 断言不再包含 "broad"
- `test_image_report_blocks_include_concept_column`: slug 集合改为新编号
- `test_render_visual_report_creates_one_png_per_section`: `len(paths) == 6` → `len(paths) == 5`

## 风险与权衡

| 风险 | 说明 | 缓解 |
|------|------|------|
| ~~Section 0 breadth S2 数据源 fallback~~ | ~~v1 误以为 `min_symbols=50` 兜底足够~~ | **v2 修订**：fallback 仅在 `< 50` 才触发，extend 池 ~500 只远超阈值，会悄悄改 S2 口径。**修法**：`build_market_timing_factor_report` 不再接受 `price_frames`，内部强制走 `_load_market_db_broad_price_frames`，与扫描 universe 解耦（A1b） |
| Layer leak（broad row 静默隐藏） | 删除 LAYER_ORDER 的 broad 后，渲染只遍历 pool/extend，broad row 会消失但不报错 | **v2 修订**：在 `_enrich_with_layer` 边界 fail-fast (raise ValueError，v3 改 `assert` → `raise`)；`_group_by_layer` 加日志兜底 |
| Dollar Volume 图片版 layer leak | DV 走独立 `_normalize_dv_items` 路径，broad 小盘 DV row 会被静默丢 | **v3 修订**：`_normalize_dv_items` 内部 filter broad layer，与选股口径一致；新增文本+图像双测试 |
| `--symbols` override 路径低市值 | OKLO 等手动调试 8B 标的会触发 fail-fast | **v3 修订**：override 模式下所有 symbol 强制并入 `pool_symbols`，layer 一律 pool，绕过 mcap 分层 |
| `save_broad_scan_hits` 历史断点 | 晨报不再写 broad_scan_hits 表 | 仅 `scripts/broad_market_scan.py` 自己写入；独立 cron 不受影响 |
| company.db 中 broad 候选未更新 | 停用后 broad metadata 不再被晨报间接刷新 | 不影响 — pool/extend 已经独立有完整 metadata 流；broad metadata 不进 OPRMS |

## 验收标准（v2 修订）

```bash
.venv/bin/python -m pytest tests/test_morning_report.py tests/test_telegram_routing.py -v
.venv/bin/python -m compileall scripts/morning_report.py
.venv/bin/python -m scripts.morning_report --no-telegram --image-report --image-delivery pdf
```

具体断言：

1. **测试全绿**，且包含 6 个新增测试：
   - `test_market_timing_factor_uses_broad_db_regardless_of_scan_frames` ([P0] S2 解耦，60 只 broad fixture + e2e mock load_price_frames)
   - `test_build_market_signal_report_filters_to_extend_plus_pool` ([P1] post-filter)
   - `test_build_market_signal_report_override_promotes_all_to_pool` ([v3 P2] override 全入 pool)
   - `test_dv_section_filters_out_broad_layer` ([v3 P1] DV 文本版 broad filter)
   - `test_dv_visual_block_filters_out_broad_layer` ([v3 P1] DV 图片版 broad filter)
   - `test_pmarp_section_renders_three_signal_kinds` (PMARP 三类信号显示)
2. `compileall` 通过（仅挡语法错误 / 模块顶层 import 缺失；symbol 级残留靠 pytest collection + 第 5 步 grep 兜住）
3. dry-run 晨报包含：
   - `0. 大盘择时因子`（含 PMARP 2% + S2 参与度(broad)，行为不变）
   - `1. PMARP 信号`（criteria 含"上穿2% / 上穿98% / 下穿98%"，新增"信号"列）
   - `2. 量能加速`、`3. RVOL 持续放量`
   - 不再出现 `1. 广扫标准` 段落
   - subtitle 不再出现 `Pool / Extend / Broad 分层`（但 `S2参与度(broad)` 列保留）
4. PDF 生成 5 张图片（00 timing / 01 pmarp / 02 dv / 03 rvol / 04 dollar volume）
5. `grep "broad_market_scan\|scan_candidates\|BROAD_SCAN_RVOL_THRESHOLD\|BROAD_SCAN_RETURN_THRESHOLD\|format_section_broad_signal\|broad_scan" scripts/morning_report.py` 应只剩 `_load_market_db_broad_price_frames` / `BROAD_UNIVERSE_MIN_MCAP_USD` / `S2参与度(broad)` 的 S2 相关引用

## 实施步骤（v2 修订）

1. 创建 worktree（避免污染 main）
2. **先写测试 [TDD 顺序]**：先加三个新测试（P0 timing 解耦 + P1 universe filter + PMARP 三类）让它们 RED
3. 改动 `scripts/morning_report.py`：
   - A1b（解耦 timing report 的 S2 输入）— **先做这个，单独 commit，避免和后续改动混淆**
   - A1（layer constants + fail-fast）
   - A2-A3（PMARP 三类信号）
   - A4（universe post-filter）
   - A5-A6（删 Section 1 + 重编号 + subtitle）
4. 改动 `tests/test_morning_report.py`（B1-B3 fixture/断言调整）
5. 跑验收命令：pytest + compileall + dry-run image-report PDF
6. `grep` 确认无 broad scan 残留
7. 提交 + 推送（等 Boss 指示再 merge / 推云端）
