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
| Theme Engine P2 (40 new tests, 718 total) | 2026-02-15 | +1,493 |
| RS Backtest Engine (69 new tests, 787 total) | 2026-02-16 | +3,143 |
| Factor Study Framework (51 new tests, 838 total) | 2026-02-16 | +3,769 |
| Alpha Debate + Agent Memory (60 new tests, 898 total) | 2026-02-20 | +1,800 |
| **当前** | 2026-02-20 | **~175 files, 898 tests** |

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

### Theme Engine P2 (DONE 2026-02-15)
- Engine A (量价动量) + Engine B (注意力量化) 信号合并
- `terminal/theme_pool.py`: 池子动态扩展管理器 (source=attention, 只增不减, max_new=10 安全阀)
- `scripts/scan_themes.py`: 周频主线扫描入口 (--no-expand / --dry-run / --top-n)
- 信号合并: converged (双引擎共振) / momentum_only / narrative_only
- 主题热力图: 按 THEME_KEYWORDS_SEED 归类
- 7 Section 终端报告 + JSON 存档 (data/scans/theme_*.json)
- `pool_manager.py`: `_get_non_screener_stocks()` 保留 analysis + attention 源
- 4 files changed + 3 new files, +1493 lines, 40 new tests, 718 total pass

### RS Backtest Engine (DONE 2026-02-16)
- 策略回测引擎: RS 排名 → Top-N 选股 → 等权/市值加权 → 定期再平衡 → NAV 跟踪
- BacktestEngine + PortfolioState + Rebalancer + BacktestMetrics (Sharpe/MDD/Calmar)
- 参数优化器 + HTML 报告 (暗色主题 + Chart.js)
- 美股 + 币圈双适配器 (USStocksAdapter / CryptoAdapter)
- 11 files +3143 lines, 69 new tests, 787 total pass

### Factor Study Framework (DONE 2026-02-16)
- 通用因子有效性研究: 任意因子插入 → 双轨分析 (IC + 事件研究)
- Track 1: Spearman IC → IC_IR → 分位数单调性 → IC 衰减曲线
- Track 2: 4 信号类型 (threshold/cross_up/cross_down/sustained) → t-test
- 8 因子适配器 (RS_B/C, PMARP, RVOL, DV_Accel, RVOL_Sustained, Crypto_RS_B/C)
- 参数网格扫描 + HTML 报告 (Chart.js) + CSV 导出
- CLI: `scripts/run_factor_study.py --market us_stocks --factor RS_Rating_B`
- 10 files +3769 lines, 51 new tests, 838 total pass
- 币圈实测结论: RS_C cross_down_5 at 3d 有显著均值回归 (+2.39%, t=14.85)

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

### Alpha Debate + Agent Memory (DONE 2026-02-20)
- 灵感来源: TradingAgents (双层辩论), Dexter (Scratchpad 审计), ai-hedge-fund (agent 架构)
- Alpha Debate (Phase 4): 索罗斯 vs 马克斯终极辩论 — 行动派 vs 耐心派
  - `knowledge/alpha/debate.py`: prompt 生成器 (2 轮辩论 + 裁判综合)
  - conviction_modifier (0.5-1.5) 覆盖 bet 层的（辩论为最终裁决）
  - final_action: 执行 / 搁置 / 放弃
- Agent Memory: 跨会话经验积累
  - `terminal/memory.py`: 情境提取 + SQLite 存储 + 检索 + prompt 注入
  - `company_store.py`: schema 迁移 (situation_summary + 3 debate 列)
  - 历史分析自动存储 → 下次同 ticker 分析时注入 past_experiences
- Scratchpad: 3 个新事件类型 (debate_round, debate_synthesis, memory_retrieval)
- Pipeline: Phase 0-1-2-3 → **Phase 4 (Alpha Debate)** → compile
- HTML: 紫色 regime-box 辩论卡片
- Heptabase: card_content 增加辩论结论 section
- Deep Analysis Skill: Phase 4 agent 调度 + Phase 5 assembly
- 12 files changed + 4 new files, ~+1800 lines, 57 new tests, 775 total pass
