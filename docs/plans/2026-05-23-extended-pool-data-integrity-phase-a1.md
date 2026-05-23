# Extended Pool Data Integrity Fix — Phase A1

> P0 数据完整性 bug 修复：FMP screener 默认 limit=1000 导致扩展池 533 ≈ "top-1000 minus ETF/Fund"，而非配置的 $10B+ 全集 (~949)。
>
> **方案 C 拆分**：A1 = bug 修复 + 池刷新 + 验证；A2 = 416 只新增票 concept 补分类（独立 plan）；A3 = 周频 concept-build cron 接入（独立 plan，依赖 A2）。

- **状态**：草案，待 Boss 审阅
- **作者**：CC (主)
- **日期**：2026-05-23
- **触发**：2026-05-21 Boss 问"概念词表里 TTMI 的 L1-L3"，发现 TTMI（市值 $17.59B）不在 concept registry / extended pool / 任何 universe
- **依赖**：无
- **后续**：A2 (concept 补分类) → A3 (周频 concept cron 接入)
- **关联文档**：
  - 触发追踪：`docs/issues/029-extended-pool-screener-limit-truncated.md`（本 plan 同步落档）
  - 治理前置：`docs/plans/2026-05-09-extended-pool-weekly-refresh.md`（修治理漏洞，没碰 limit truncation）
  - 北极星：数据层（Layer 1）数据完整性 hygiene fix

---

## 0. 范围与边界（Section 1）

### In Scope

1. `src/data/fmp_client.py:get_large_cap_stocks()` 加 `limit=SCREENER_DEFAULT_LIMIT=5000` 默认参数 + truncation sentinel warning
2. `src/data/extended_universe_manager.py:MIN_COUNT_FLOOR` 400 → 800
3. `terminal/tools/fmp_tools.py:GetLargeCapStocksTool.execute()` 同步透传 `limit`
4. 单元 + 集成测试：5 个新增 unit + 2 个新增/扩展 integration
5. 云端手动触发 `python -m src.data.extended_universe_manager --refresh`
6. 本地 `sync_to_cloud.sh --pull` 同步 `data/pool/extended_universe.json`
7. `verify_forward_coverage.py --scope all --min-date 2026-05-23` 验证 coverage
8. 落档 `docs/issues/029-extended-pool-screener-limit-truncated.md`
9. Tainted-by-Pool-533 research backlog 段落

### Out of Scope（明确不做，交给 A2/A3 或后续 plan）

- ❌ ~416 只新增 concept 补分类（→ A2 独立 plan）
- ❌ 周频 concept-build cron 接入（→ A3 独立 plan）
- ❌ 重跑下游研究（RS backtest / PMARP factor study）
- ❌ `get_screener_page()` 审计——已确认分页逻辑正确
- ❌ `scripts/check_fmp_api.py` 自定义函数——是 API 健康检查，$100B 阈值 top-1000 之内
- ❌ MarketData / FRED / Adanos 等其他第三方 API 的同类 limit 普查（记入下游 backlog）

### 影响面（grep 已核对）

| 调用点 | source line | 阈值 | A1 修复后效果 |
|------|----|----|------|
| `pool_manager.refresh_universe()` 通用池 | `src/data/pool_manager.py:150` | $100B | 不变（top-1000 内） |
| `pool_manager.refresh_universe()` 科技扩池 | `src/data/pool_manager.py:159` | $10B | **自动受益**——下次池刷新拿到完整科技 universe |
| `extended_universe_manager.refresh_extended_universe()` | `src/data/extended_universe_manager.py:87` | $10B | **A1 主修复点**——533 → ~949 |
| `scripts/rs_universe_scan.py:fetch_universe()` | `scripts/rs_universe_scan.py:64` | $10B default | **自动受益**——下次 RS scan universe ~1000 → ~1797 |
| `scripts/check_fmp_api.py:46` (custom func, 独立) | `scripts/check_fmp_api.py:46` | $100B | 不受影响（top-1000 内）|

---

## 1. 核心代码改动（Section 2）

> Plan 伪代码必须对齐真实代码——每个引用已 grep 核对并标 source line。

### 1.1 `src/data/fmp_client.py:69-83` get_large_cap_stocks 加 limit + sentinel

**改动前**（line 69-83，grep 核对）：
```python
def get_large_cap_stocks(self, market_cap_threshold: int) -> List[Dict]:
    """获取大市值股票列表"""
    params = {
        "marketCapMoreThan": market_cap_threshold,
        "exchange": "NYSE,NASDAQ",
        "isActivelyTrading": "true",
    }
    data = self._request("company-screener", params)

    if not data:
        return []

    # 过滤 ETF 和基金
    stocks = [s for s in data if not s.get("isEtf") and not s.get("isFund")]
    return stocks
```

**改动后**：
```python
# Module-level constant for testability
SCREENER_DEFAULT_LIMIT = 5000  # FMP screener page cap; ~2.8x $10B+ 全集 (1797 as of 2026-05-21)

def get_large_cap_stocks(
    self,
    market_cap_threshold: int,
    limit: int = SCREENER_DEFAULT_LIMIT,
) -> List[Dict]:
    """获取大市值股票列表

    Args:
        market_cap_threshold: 最小市值（美元）
        limit: FMP screener page size. Default 5000 覆盖当前 $10B+ 全 US universe.
            FMP 默认 limit=1000，必须显式传 limit 否则按 marketCap 降序截断。
            若未来 $10B+ 全集 > 5000，调高此 anchor 并审计 sentinel 日志。

    Returns:
        过滤掉 ETF/Fund 后的股票列表
    """
    params = {
        "marketCapMoreThan": market_cap_threshold,
        "exchange": "NYSE,NASDAQ",
        "isActivelyTrading": "true",
        "limit": limit,
    }
    data = self._request("company-screener", params)

    if not data:
        return []

    # Sentinel: 返回行数精确等于 limit = 大概率被 page 截断
    if isinstance(data, list) and len(data) == limit:
        logger.warning(
            "FMP screener returned exactly limit=%d rows for marketCapMoreThan=%d; "
            "possible truncation, increase limit or switch to get_screener_page().",
            limit, market_cap_threshold,
        )

    # 过滤 ETF 和基金
    stocks = [s for s in data if not s.get("isEtf") and not s.get("isFund")]
    return stocks
```

### 1.2 `src/data/extended_universe_manager.py:40` MIN_COUNT_FLOOR 400 → 800

```python
# Before (line 40):
MIN_COUNT_FLOOR = 400  # 73% of current 548; tune via `min_count_floor` kwarg

# After:
MIN_COUNT_FLOOR = 800  # ~84% of A1 刷新后预期 baseline ~949；防 future regression
                       # （screener truncation / FMP empty return / 池骤减都卡在此 floor）
```

**注**：4 个现有 `TestRefreshFloorGuard` 测试用 `min_count_floor=0` 显式 override，不受影响。

同步更新 docstring 注释 (line 36-39)：
```python
# Sanity floor: FMP screener API failure can return [] silently. Raise rather
# than overwrite the cache when returned count < floor (preserves old cache for
# next cron retry). Default 800 = ~84% of A1 刷新后预期 ~949; tune via
# `min_count_floor` kwarg in tests/dev paths.
```

argparse help 字符串 (line 158-159) 自动用新值，无需改：
```python
help="Refresh extended_universe.json from FMP screener (raises if "
     f"returned count < {MIN_COUNT_FLOOR})",
```

### 1.3 `terminal/tools/fmp_tools.py:215-227` 工具注册表透传 limit

```python
# Before (line 215-227):
def execute(self, market_cap_threshold: int) -> List[Dict]:
    """Execute: get large-cap stocks."""
    return self._execute_client_method(
        "get_large_cap_stocks", market_cap_threshold=market_cap_threshold
    )

# After:
from src.data.fmp_client import SCREENER_DEFAULT_LIMIT  # 单源常量

def execute(
    self,
    market_cap_threshold: int,
    limit: int = SCREENER_DEFAULT_LIMIT,
) -> List[Dict]:
    """Execute: get large-cap stocks."""
    return self._execute_client_method(
        "get_large_cap_stocks",
        market_cap_threshold=market_cap_threshold,
        limit=limit,
    )
```

注：`ToolMetadata` 不需要改——FMP tool registry 走的是 method delegation，不强校验签名。

---

## 2. 部署 + 数据刷新流程（Section 3）

```mermaid
flowchart TD
    A[本地 worktree 修代码 + 测试] --> B[Boss review + merge to main]
    B --> C[git push origin main]
    C --> D[云端 git pull]
    D --> E[云端手动触发<br/>python -m src.data.extended_universe_manager --refresh]
    E --> F{count >= 800 floor?}
    F -->|YES, 写 cache ~949| G[本地 sync_to_cloud.sh --pull]
    F -->|NO, raise RuntimeError| X[告警 + 排查]
    G --> H[本地跑 verify_forward_coverage.py<br/>--scope all --min-date 2026-05-23]
    H --> I[确认核心池 / 扩展池 forward EPS 覆盖率符合预期]
    I --> J[A1 完成 → 启动 A2 plan]
```

### 2.1 完整命令清单

```bash
# 0) 本地开 worktree
git worktree add .worktrees/extended-pool-data-integrity-a1 feature/extended-pool-data-integrity-a1
cd ".worktrees/extended-pool-data-integrity-a1"

# 1) 改代码 + 跑测试
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest \
    tests/test_fmp_client_mcap.py \
    tests/test_extended_universe_manager.py \
    -v
# 期望: 既有测试全绿 + 5+1 个新测试通过

# 2) commit
git add -A
git commit -m "fix(fmp): screener limit truncation — get_large_cap_stocks default limit=5000 + sentinel"

# 3) 回主 worktree merge（Boss 拍板后执行，遵守 no-merge-without-asking）
cd "/Users/owen/CC workspace/Finance"
git merge --no-ff feature/extended-pool-data-integrity-a1

# 4) Boss 拍板后 push
git push origin main

# 5) 云端 pull + refresh（用 -b 192.168.1.121 workaround，记忆已记录）
ssh -4 -b 192.168.1.121 aliyun \
    "cd /root/workspace/Finance && git pull --ff-only && \
     python3 -m src.data.extended_universe_manager --refresh 2>&1 | tee /tmp/refresh_extended_$(date +%Y%m%d).log"
# 期望: log 末尾有 "Extended universe refreshed: ~949 symbols"，无 "possible truncation"

# 6) 本地同步 universe.json（双端 merge 关系，P3 所有权模型）
./sync_to_cloud.sh --pull
# 检查 data/pool/extended_universe.json 的 count 字段从 533 → ~949
# 验证 TTMI 入池: grep TTMI data/pool/extended_universe.json

# 7) verify
"/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/verify_forward_coverage.py \
    --scope all --min-date 2026-05-23
# 期望: exit 0；若有 416 新增票还无 forward EPS 历史数据，会在下次 Sat 10:15 cron 后 100%
```

### 2.2 验收清单

| # | 验收项 | 命令 / 入口 | 期望 |
|---|---|---|---|
| V1 | 单元测试全绿 | `pytest tests/test_fmp_client_mcap.py tests/test_extended_universe_manager.py -v` | 既有 + 6 新增全 pass |
| V2 | 全套回归测试无退化 | `pytest tests/ -q` | ~1951 + 6 新增 pass, 0 regression |
| V3 | 云端 refresh 数字符合预期 | `tail /tmp/refresh_extended_*.log` | "refreshed: ~949 symbols"（容忍 ±50） |
| V4 | 无 truncation warning | 同 log | 不应出现 "possible truncation"（5000 >> 1797） |
| V5 | Cache 写入 | `cat data/pool/extended_universe.json \| python -c 'import sys,json; print(json.load(sys.stdin)["count"])'` after pull | count 从 533 → ~949 |
| V6 | TTMI 入池（核心样本） | `grep TTMI data/pool/extended_universe.json` | 存在 |
| V7 | Forward coverage 通过 | `verify_forward_coverage.py --scope all --min-date 2026-05-23` exit code | 0 |
| V8 | Sentinel 不误报 | `grep "possible truncation" logs/`（refresh 后） | 无命中 |

### 2.3 已知降级路径 / 回滚

- **云端 refresh count < 800**：旧 cache 保留不动（floor guard 预期行为）。排查 FMP API + 重试，不强行覆盖
- **云端 refresh count > 5000**：sentinel warning 入日志。**本次不预防性调高 SCREENER_DEFAULT_LIMIT**，记入下游 backlog
- **代码回滚**：`git revert` 合并 commit；fmp_client + floor 改动均为可逆。已写入的 949 symbols 不会回退到 533（池扩大本身是 net positive，无需回滚数据）

---

## 3. 文档落档（Section 4）

| 文档 | 路径 | 内容要点 |
|---|---|---|
| Issue 029 | `docs/issues/029-extended-pool-screener-limit-truncated.md` | 前因 + 根因 + 影响面表 + 修复方案 + 时序 + 教训 |
| Plan A1（本文件）| `docs/plans/2026-05-23-extended-pool-data-integrity-phase-a1.md` | 完整 plan + 设计 + checklist |
| Memory L2 | `.claude/memory/decisions-investing.md` | 加一条："任何调用第三方 screener / list API 拿全集语义时，必须显式传 page limit + 加 sentinel" |
| ongoing.md | `.claude/ongoing.md` | A1 plan merge 后从"活跃任务 🚨 P0"移到"最近完成"；新增 A2/A3 占位 |
| CHANGELOG | `docs/CHANGELOG.md` | 加一行：扩展池数据完整性修复，533→~949 |
| ARCHITECTURE.md | 主仓 `ARCHITECTURE.md` | 不改——extended pool 阈值/大小不在 ARCHITECTURE 里硬写 |
| CLAUDE.md | `Finance/CLAUDE.md` | 不改——extended pool 说明保持抽象 `$10B+`，不写具体 count |

### 3.1 Issue 029 内容草案

```markdown
# Issue 029: Extended Pool Screener Limit Truncated

## 触发
2026-05-21 Boss 问"概念词表里 TTMI 的 L1-L3"，发现 TTMI（市值 $17.59B）不在 concept registry / extended pool / 任何 universe。

## 根因
`src/data/fmp_client.py:get_large_cap_stocks()` 调用 FMP screener 时未传 `limit` 参数。FMP screener 默认 `limit=1000` 且按 marketCap 降序返回。

实际效果：扩展池等价于 **隐式 marketCap >= $24.41B 阈值**（top-1000 边界），而非配置的 `EXTENDED_UNIVERSE_MIN_MCAP_B = 10`。

## 影响面
- `extended_universe.json`: 533 only（应 ~949），漏 ~416 只 $10B-$24B 中盘
- `pool_manager` 科技扩池 `TECH_MARKET_CAP_THRESHOLD=$10B` 同样受影响
- `rs_universe_scan` default `--min-mcap=10` universe 阉割
- 下游因子研究 / RS backtest / concept registry 全部受污染

## 修复（commit hash 落定后填）
- `src/data/fmp_client.py`: 加 `limit=SCREENER_DEFAULT_LIMIT=5000` 默认参数 + truncation sentinel warning
- `src/data/extended_universe_manager.py`: `MIN_COUNT_FLOOR` 400→800
- `terminal/tools/fmp_tools.py`: tool registry execute() 透传 limit

## 教训
1. **任何调用第三方 screener / list API 拿"全集"语义时，必须显式传 page limit + 加 sentinel**
2. `MIN_COUNT_FLOOR` 设计目的是防 corruption，但救不了"被默默截断"的失败模式——floor 之上还需要 sentinel
3. 历史 commit `34535aa`（5/9 weekly refresh fix）只修了 cron 漏调 flag，没碰 limit truncation——**修复 governance 不等于修复正确性**

## 关联
- 5/9 plan `docs/plans/2026-05-09-extended-pool-weekly-refresh.md`（修治理漏洞）
- A2 follow-up: 416 只新增票 concept 补分类
- A3 follow-up: 周频 concept-build cron 接入
```

### 3.2 Tainted-by-Pool-533 Research Backlog

| 研究 | 输出物 | 污染程度 | 重跑建议优先级 |
|---|---|---|---|
| RS Universe Scan 历史日报 | `data/scans/rs_universe_*.json` | 高——universe 缺 ~800 中盘股 | 不重跑——RS 是流量信号，下次 cron 自动用新 universe |
| PMARP factor study（4/22 hardening report）| `docs/research/2026-04-22-pmarp-extended-hardening.md` | 中——extended PIT $10B 阈值实际是 top-1000 | 中等：true survivorship overlay 已部分修复，全 universe 重跑边际收益 ongoing.md 已记录"低于进入组合层验证" |
| RS Backtest 4 组对比（3/31 session 55）| `.claude/memory/project_rs_regime_comparison.md` | 中——extended Sharpe 1.51 是 top-1000 上的 | 低：结论是状态描述，不影响后续决策；如未来用 extended Sharpe 数字做对比 baseline，先 invalidate 此数字 |
| Concept Registry 545+23 reviewed CSV | `reports/concept_registry/extended_pool_tags_2026-05-17.csv` | 低——CSV 本身正确；只是覆盖范围窄 | **由 A2 plan 处理**——416 只补分类 |
| Broad Breadth event-validity（5/1）| `docs/research/2026-05-01-breadth-event-validity.md` | 无——breadth 走的是 broad universe (2769)，不依赖 extended_universe.json | 不重跑 |
| Factor study 在 extended 池上的所有 IC/事件研究 | `reports/factor_study/*` | 中——universe 缺 ~800 中盘股 | 中：未来如果要把 extended 池因子结论升级到 production 信号，必须在 ~949 池重跑 |

**默认策略**：所有 533 池产出的"已发表"研究结论保留，但加 caveat tag `[universe: stale-533]`。新研究 from 2026-05-23 全部用 ~949。

---

## 4. 测试策略 + 错误处理（Section 5）

### 4.1 测试金字塔

```
       ┌────────────────────────────────┐
       │  E2E (云端 refresh)            │  1×: 手动 ssh 触发，验真实 FMP 行为
       └────────────────────────────────┘
     ┌────────────────────────────────────┐
     │  Integration                       │  2×: refresh_extended_universe()
     │  (mocked FMP, real cache file)     │     落 floor guard + truncation 路径
     └────────────────────────────────────┘
   ┌────────────────────────────────────────┐
   │  Unit                                  │  5×: fmp_client params/sentinel/limit
   │  (mocked _request)                     │
   └────────────────────────────────────────┘
```

### 4.2 单元测试（5 新增）

加入 `tests/test_fmp_client_mcap.py`（mcap 主题与 `get_large_cap_stocks` 直接相关，无需新建文件）。

| # | 测试 | 防退化场景 |
|---|---|---|
| U1 | `test_get_large_cap_stocks_passes_default_limit` | 默认 limit=5000 写进 params——本 bug 的"反证测试" |
| U2 | `test_get_large_cap_stocks_warns_on_exact_limit_match` | sentinel 在 `len(data)==limit` 时触发（mock 返回 `[{"symbol":f"S{i}"} for i in range(5000)]`，`caplog` 抓 "possible truncation"） |
| U3 | `test_get_large_cap_stocks_no_warn_when_below_limit` | sentinel 不误报（mock 返 1000 行，断言无 warning） |
| U4 | `test_get_large_cap_stocks_respects_custom_limit` | 调用 `limit=100`，验证 params 含 `limit=100` |
| U5 | `test_get_large_cap_stocks_no_warn_on_empty_response` | API 返 `None`/`[]` 时直接 return，不进 sentinel 分支 |

### 4.3 集成测试（1 新增 + 1 扩展）

加入 `tests/test_extended_universe_manager.py::TestRefreshFloorGuard`。

| # | 测试 | 防退化场景 |
|---|---|---|
| I1 | `test_aborts_when_below_new_floor_800` | mock 返回 700 个 symbol，断言 `RuntimeError` + cache 不变 |
| I2 | `test_refresh_writes_cache_when_sentinel_triggers` | mock 返回正好 5000 个 symbol，断言 cache 写入成功 + warning 落 caplog（warning 不阻塞） |

### 4.4 错误处理矩阵

| 失败模式 | 检测 | 行为 | 告警 |
|---|---|---|---|
| FMP 返回 `None`（API down） | `if not data: return []` | 静默返空——caller 责任接住 | ❌（floor guard 在 caller 层 raise） |
| FMP 返回 < 800 行 | `if len(symbols) < min_count_floor: raise` | RuntimeError，cache 保留 | ✅ Telegram（cron wrapper） |
| FMP 返回 == 5000 行（truncation） | `if len(data) == limit: logger.warning(...)` | 写入 cache（不阻塞），warning 入日志 | ❌ warning-only |
| FMP 返回 > 5000 行 | N/A | N/A——FMP 强制 limit | N/A |
| FMP 返回乱序数据 | `sorted(set(...))` in caller | OK | ❌ |
| 网络超时 | `_request()` 内部 retry × 3 | 失败时 `_request` 返 None → 走 "FMP 返回 None" 分支 | ❌ |
| 云端 cron 跑 `--refresh` 时 RuntimeError | cron wrapper 抓 exit code | cron 标失败 + Telegram | ✅ 已现有机制 |

### 4.5 错误处理设计选择

**选择 1: Sentinel 是 warning 不是 raise**
理由：5000 是 anchor 不是 hard ceiling；万一未来 $10B+ 全集自然涨到 5000+，raise 会把云端 cron 卡死；warning 给 Boss 反应时间。

**选择 2: Floor guard raise 而非 warning**
理由：池骤减 = 数据完整性 vs 噪声完整性两难，宁可保留旧 cache（旧数据 > 损坏数据）。

**选择 3: 不加 retry on `< floor`**
理由：retry 会 mask FMP 临时故障，下次 cron（24h 内）会自然重试。**显式失败 > 自愈式失败**——后者会形成"数据看似正常但实际是上次成功结果"的幽灵 bug。

---

## 5. Follow-up Plans（Section 6）

### A2 — Concept Registry 416 只新增票补分类（独立 plan，依赖 A1）

- **触发**：A1 merge + 云端 refresh + 本地 sync 完成后，extended_universe.json 净增 ~416 票
- **路径**：`docs/plans/2026-05-2X-concept-registry-416-new-symbols-relodge.md`（A1 落地后建）
- **工作流**：参考 5/15-17 reviewed CSV——LLM prefill + Boss 手编 L3
- **Boss 偏好**：亲过 L3（5/17 工作流强度）
- **预估**：~$60-80 LLM cost + 1-2 sessions 审改
- **入库**：`company_concept_tags` 568 → ~984

### A3 — 周频 concept-build cron 接入（独立 plan，依赖 A2）

- **触发**：A2 reviewed CSV 落库后，验证 build pipeline 健康
- **路径**：`docs/plans/2026-05-2X-weekly-concept-build-cron.md`
- **内容**：`broad_universe_cron_wrapper.sh weekly_refresh` 末尾加 `build_company_concept_registry --reclassify` 步骤
- **边界**：cron 只跑 rule classifier + LLM fallback（不要求 Boss 审），所以 cron 只能保 hard fallback 行进 review queue；soft 手动入库仍需 Boss 决策——cron 不替代 reviewed CSV 工作流

---

## 6. 时序

```mermaid
gantt
    title Phase A1 Timeline
    dateFormat  YYYY-MM-DD
    section A1 — P0 修复
    开 worktree + 写代码         :a1, 2026-05-23, 1d
    pytest 全绿                  :a2, after a1, 1d
    Boss review + merge          :a3, after a2, 1d
    云端 pull + refresh          :a4, after a3, 1d
    本地 sync + verify           :a5, after a4, 1d
    Issue 029 + plan + CHANGELOG :a6, after a5, 1d
    section Follow-up
    A2 启动                      :b1, after a6, 2d
    A3 启动                      :c1, after b1, 7d
```

**乐观估算**：A1 一个 session 闭环（Boss 审 + merge + 部署 + verify）。**保守估算**：跨 2 sessions（写代码 + 测试 + Boss 审一天，部署 + verify 一天）。

---

## 7. 风险自证

| Q | A |
|---|---|
| **为什么不直接 hard-code `limit=5000`，不加 sentinel？** | Sentinel 是廉价保险——若未来 $10B+ 全集涨到 5000+，没 sentinel 就会重蹈 5/21 覆辙（数据看起来正常但实际被截断）。warning 不阻塞，只是日志线索 |
| **为什么不直接调高到 10000 一劳永逸？** | FMP plan 没明文支持 10000+，超 5000 行为不可预测；anchor 5000 是 1797 全集 × 2.8x safety margin 已充分 |
| **为什么不一次性 merge A1+A2+A3 减少 3 次 worktree 开销？** | P0 修复延迟交付 = 下游研究继续被污染；A2 416 只手编 L3 需要 Boss 大量时间，把它放进 critical path 会卡 main 几天；方案 C 是 Boss 已决策 |
| **为什么 floor 800 不是 900？** | ~949 全集 × ~84% = 800；900 = 95% buffer 太紧，扩展池自然波动 ±50 会误触发 raise；800 留 ~149 元 safety |
| **为什么不让 sentinel 同时 raise 出来？** | 见 4.5 选择 1——sentinel 是软告警，floor 才是硬门，两层防御互补 |
| **本 plan 最大失败模式是什么？** | (1) 云端 refresh count 不是 ~949 而是其他值（FMP 数据自然波动 / 估算有偏差）→ 不影响修复正确性，可能需调整验收数字。(2) 修完后 verify_forward_coverage 时间窗口外的 416 票还没有 forward EPS 历史（等下次 Sat 10:15 cron） |
| **会破坏什么现有功能吗？** | 不会。API 签名向后兼容；测试覆盖 5+1；现有 caller 全部沿用旧调用形式自动受益；唯一行为变化是池从 533→949，416 票首次进入数据流 |

---

## 8. 北极星对齐

| 北极星层 | 对应 |
|---|---|
| **数据层（Layer 1）** | A1 直接修数据完整性 bug，影响整个数据底座的"全集"语义 |
| **分析层（Layer 2）** | A1 落地后所有依赖 extended_universe 的因子研究 / RS backtest 在新池上自动跑（下次 cron）。不在本 plan 内重跑历史 |
| **策略层（Layer 3）** | OPRMS 评级走核心池（~130），不依赖 extended——不受影响 |
| **CIO 层（Layer 4）** | A 副轨（PI）走 holdings，不依赖 extended；B 主轨 gated on 分析层成熟度，本 plan 提升分析层数据完整性，间接支撑 |

本 plan 是数据层 hygiene fix，不引入新功能，不改变 north-star 层定义。属于数据正确性类的隐性需求修复。

---

## 9. 决策记录（brainstorm 阶段所有 Boss 拍板）

| # | 决策 | Boss 选 |
|---|---|---|
| 1 | 修复抽象层 | fmp_client 本体加 limit 参数 |
| 2 | concept 补分类节奏 | 同步补（经方案 C 后拆为 A2 独立 plan） |
| 3 | 下游研究复盘 | 仅列 backlog，不重跑 |
| 4 | RS scan 修复 | 默认抽象层受益，不额外验证 |
| 5 | 周频 cron | 接入本 plan（经方案 C 后拆为 A3 独立 plan） |
| 6 | Boss 参与度 | 亲过 L3（A2 plan 范围） |
| 7 | 执行编排 | 方案 C（A1 立即 / A2+A3 后续两轮）|

---

## 10. Implementation Checklist（writing-plans skill 会展开）

> 本节是高层 checklist，writing-plans skill 接手后展开为完整的 task-level plan。

- [ ] T1: 开 worktree `.worktrees/extended-pool-data-integrity-a1` (branch `feature/extended-pool-data-integrity-a1`)
- [ ] T2: 修改 `src/data/fmp_client.py` (line 69-83) — limit + sentinel
- [ ] T3: 修改 `src/data/extended_universe_manager.py` (line 36-40) — MIN_COUNT_FLOOR 800
- [ ] T4: 修改 `terminal/tools/fmp_tools.py` (line 215-227) — 透传 limit
- [ ] T5: 新增 5 个 unit tests
- [ ] T6: 扩展 1 个 + 新增 1 个 integration tests
- [ ] T7: 跑 targeted pytest 全绿
- [ ] T8: 跑全套 pytest 无退化
- [ ] T9: commit + Boss review + merge to main
- [ ] T10: push origin main
- [ ] T11: 云端 git pull + `--refresh`
- [ ] T12: 本地 `sync_to_cloud.sh --pull`
- [ ] T13: 跑 verify_forward_coverage.py
- [ ] T14: 落档 issue 029
- [ ] T15: 更新 `.claude/memory/decisions-investing.md` + ongoing.md + CHANGELOG
- [ ] T16: 创建 A2 plan 文件（占位，A1 完成后开始）
