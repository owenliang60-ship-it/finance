# Changelog — 未来资本 AI 交易台

> 项目发展记录，从 L1 (MEMORY.md) 精简时移出的完整 Phase Status 历史。

---

## Build History

| 里程碑 | 日期 | 增量 |
|--------|------|------|
| Phase 1: Valuation → Finance 合并 + Desk 骨架 | 2026-02-06 | — |
| Phase 2 P0: 4 desks (92 files, 9006 lines) | 2026-02-07 | +9,006 |
| Terminal 编排层 (7 files, 1462 lines) | 2026-02-07 | +1,462 |
| FRED Macro Pipeline (42 files, 103 tests) | 2026-02-09 | +9,837 |
| P0 FMP Enrichment (13 new tests) | 2026-02-09 | +137 |
| P1 Benchmark + Correlation (14 new tests, 130 total) | 2026-02-09 | +397 |
| Macro Briefing Layer (40 new tests, 179 total) | 2026-02-09 | +773 |
| Alpha Layer (L2) (53 new tests, 232 total) | 2026-02-09 | +1,505 |
| Data Freshness + Pool Fix (27 new tests, 259 total) | 2026-02-09 | +661 |
| Macro Briefing Fix (261 tests) | 2026-02-09 | fix |
| P1 Heptabase Sync (11 new tests, 272 total) | 2026-02-09 | +474 |
| Deep Analysis Pipeline v2 (22 new tests, 297 total) | 2026-02-10 | +2,163 |
| Analysis Freshness System (29 new tests, 326 total) | 2026-02-10 | +1,256 |
| HTML Report Visualization (73 new tests, 402 total) | 2026-02-10 | +2,015 |
| HTML Debate Format Fix (408 total) | 2026-02-10 | fix |
| Macro Briefing Extraction (404 total) | 2026-02-11 | -68 net |
| Phase 2+3 Agent-ization (16 new tests, 412 total) | 2026-02-11 | +467 |
| Slim Deep Context (6 new tests, 418 total) | 2026-02-11 | +325 |
| Unified Company DB (40 new tests, 466 total) | 2026-02-13 | +2,300 |
| Auto Deep Analysis Batch (426 total) | 2026-02-12 | script |
| Attention Engine (76 new tests, 545 total) | 2026-02-13 | +3,109 |
| Momentum Engine + 晨报 (Session 19) | 2026-02-13 | cloud |
| 聚类优化 (6 new tests, 647 total) | 2026-02-14 | optimize |
| Pool Cleanup (7 new tests, 651 total) | 2026-02-15 | cleanup |
| Data Guardian (27 new tests, 678 total) | 2026-02-15 | +1,157 |
| **当前** | 2026-02-15 | **~150 files, 678 tests** |

---

## Phase Status 详细记录

### Phase 1 — 物理合并 (DONE 2026-02-06)
- Valuation workspace → Finance workspace 合并
- Desk 骨架建立 (Data/Research/Risk/Trading/Portfolio/Knowledge)

### Phase 2 P0 — Desk 基建 (DONE 2026-02-07)
- 92 files, 9006 lines across 4 desks
- Terminal Layer: 7 files, 1462 lines

### FRED Macro Pipeline (DONE 2026-02-09)
- 42 files, +9837 lines, 103 tests pass
- 16 FRED series, MacroSnapshot dataclass, regime decision tree
- Cache: 4h trading / 12h non-trading TTL

### P0 FMP Enrichment (DONE 2026-02-09)
- pipeline.py +137 lines, 13 new tests
- 4 FMP enrichment tools: estimates, earnings calendar, insider trades, news

### P1 Benchmark + Correlation (DONE 2026-02-09)
- 7 files +397 lines, 14 new tests, 130 total pass
- Pairwise return correlation matrix, JSON cache

### Macro Briefing Layer (DONE 2026-02-09)
- 6 files +773 lines, 40 new tests, 179 total pass
- 5 cross-asset signal detectors (carry trade unwind, credit stress, liquidity drain, reflation, risk rally)
- Pure rules (no LLM), millisecond execution

### Alpha Layer L2 (DONE 2026-02-09)
- 10 files +1505 lines, 53 new tests, 232 total pass
- Red Team + Cycle Pendulum + Asymmetric Bet
- conviction_modifier (0.5-1.5) adjusts OPRMS timing_coeff

### Data Freshness + Pool Fix (DONE 2026-02-09)
- 9 files +661 lines, 27 new tests, 259 total pass
- get_price_df(max_age_days=3) auto-refreshes stale CSV
- collect_data() calls get_realtime_price(), >2% deviation auto-replace

### Macro Briefing Fix (DONE 2026-02-09)
- Macro-Tactical lens removed → Stage 0 briefing only, 5 lenses

### P1 Heptabase Sync (DONE 2026-02-09)
- 3 files +474 lines, 11 new tests, 272 total pass
- MU + MSFT E2E 验证通过

### Deep Analysis Pipeline v2 (DONE 2026-02-10)
- 5 files +2163 lines, 22 new tests, 297 total pass
- File-driven orchestration: lens agents → synthesis agent → alpha agent → compile

### Analysis Freshness System (DONE 2026-02-10)
- 4 files +1256 lines, 29 new tests, 326 total pass
- AnalysisContext, FreshnessReport (GREEN/YELLOW/RED)
- check_freshness, timing refresh, evolution timeline

### HTML Report Visualization (DONE 2026-02-10)
- 3 files +2015 lines, 73 new tests, 402 total pass
- Section-aware builder, warm bright theme, 6 section builders

### HTML Debate Format Fix (DONE 2026-02-10)
- build_debate_section() 兼容 3 种 debate 格式

### Macro Briefing Extraction (DONE 2026-02-11)
- 8 files, -68 lines net, 404 total pass
- Stage 0 macro briefing 从 deep analysis pipeline 剥离
- 创建独立 /macro skill

### Phase 2+3 Agent-ization (DONE 2026-02-11)
- 3 files +467 lines, 16 new tests, 412 total pass
- build_synthesis_agent_prompt() + build_alpha_agent_prompt()
- Main Claude context: ~220-250KB → ~80-90KB

### Slim Deep Context (DONE 2026-02-11)
- 3 files +325 lines, 6 new tests, 418 total pass
- Prompts → files, compile → path, agents 后台轮询
- Main window peak: ~125KB → ~25KB (80% reduction)

### Unified Company DB (DONE 2026-02-13)
- 9 files +2300 lines, 40 new tests, 466 total pass
- SQLite CRUD (companies, oprms_ratings, analyses, kill_conditions)
- HTML Dashboard + migration script
- Migration result: 100 companies, 17 OPRMS, 14 analyses, 61 kill conditions

### Auto Deep Analysis Batch (DONE 2026-02-12)
- `scripts/auto_deep_analyze.sh` (711 行): 13 个独立 claude -p 调用/ticker
- RKLB E2E: 14m40s, DNA=B/Timing=B/3.5%

### Attention Engine (DONE 2026-02-13)
- 8 新文件 +3109 行, 76 新测试, 545 total pass
- SQLite attention.db (5 表) + Reddit/Finnhub/GT 三源采集 + Z-score 评分
- 26 主题 144 关键词覆盖 52 tickers

### 聚类优化 (DONE 2026-02-14)
- corr_window 60→30，聚类 Section F 重写
- 中文行业标签 + 变动展示

### Pool Cleanup (DONE 2026-02-15)
- cleanup_stale_data() 自动清理退出股票的 CSV + 基本面 JSON
- 删除死代码 ALLOWED_SECTORS/ALLOWED_INDUSTRIES + 遗留脚本

### Data Guardian (DONE 2026-02-15)
- 踩坑事件: cleanup 测试用假池清理了 103 只真实股票数据 → 催生本模块
- data_guardian.py: tar.gz 快照/恢复/保留策略 (max 10)
- pool_manager.py: cleanup 删除超 30% 自动熔断 + 删除前自动快照
- data_health.py: 8 项全链检查 (池完整性/覆盖率/新鲜度/一致性/DB)
- update_data.py: --check 参数 + 更新后自动健康检查
- sync 脚本: push 前/pull 后自动健康检查
- conftest.py: session 级 data/ 文件守卫
- 10 files changed, +1157 lines, 27 new tests, 678 total pass

---

## 已完成的 NEXT ACTION 历史

1. ~~P0: 实战验证~~ -- MU alpha 端到端通过
2. ~~P1: Heptabase 双向同步~~ -- MU+MSFT E2E 验证通过
3. ~~P1.5: Deep Analysis Pipeline v2~~ -- 已合并到 main
4. ~~P0: Deep Analysis 实战验证~~ -- MSFT + TSLA E2E 完成
5. ~~P1.8: Analysis Freshness System~~ -- 326 total pass
6. ~~P1.9: HTML Report Visualization~~ -- 402 total pass
7. ~~P1.95: Phase 2+3 Agent-ization~~ -- 412 total pass
8. ~~P1.96: Slim Deep Context~~ -- 418 total pass
9. ~~P0: Agent-ized Deep Analysis 实战验证~~ -- MU 11 agent E2E 通过
10. ~~P1.97: Unified Company DB~~ -- 466 total pass
