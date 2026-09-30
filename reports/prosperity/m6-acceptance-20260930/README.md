# 景气 M5/M6 验收证据（2026-09-30）

快照：`data/backups/prosperity/phase0-accepted-input-20260929/market.db`（SHA 3caffdb7…，只读）。分支 `prosperity/m5-m6-kernel`，基线 main `31fcdf38`。

## Task 2：SUE 公共函数抽取（D9 当前报告不变）

`d9-sue-refactor/`：抽取前（`d9-coverage-before`）与抽取后（`d9-coverage-after`）对同一快照跑 D9 当前报告（as_of 2026-09-29，915 只），两份 JSON 顶层只有 `generated_at` 不同，其余逐字段相同（`code_sha` 也相同）。

## Task 2b：输入包 `listing_date`（D-2 A）

`listing-date-live-0929/summary-2026-09-29.json`：live 915 包，0 泄漏，0 错误；`listing_date_known` = 901。

- NVDA 1999-01-22、MSFT 1986-03-13、COST 1986-07-09；CRWV 2025-03-28（547 天，次新）
- 不满 730 天的 18 只：VG、SAIL、CRWV、CRCL、CHYM、JBS、AMRZ、FIG、Q、MICC、MDLN、FPS、SUNB、PAYP、CBRS、INIO、QNT、SPCX
- 上市日未知 14 只（ipoDate 缺失，或市值历史早于 ipoDate，多为分拆）：BMNR、BSP、CR、CRBG、DTM、FDXF、HLN、HONA、MAIR、PS、SOLV、TKO、VLTO、WSE。按 D-2 当非次新处理，走净利率闸门

## Task 8：真实快照验收（计划"验收标准"第 3–8 条）

命令（worktree 内，`--db` 指向上面的快照）：

```bash
.venv/bin/python scripts/prosperity_score.py --as-of 2026-09-29 --mode live --observed-at 2026-09-29 --db "$DB" --out-dir live-0929
.venv/bin/python scripts/prosperity_score.py --as-of 2024-08-17 --mode replay --db "$DB" --out-dir replay-240817 --no-beta
.venv/bin/python independent_check.py --db "$DB" --board live-0929/board-F1-2026-09-29.jsonl > independent_check.json
```

`code_version` = `c453bf09028a8e6f`（code review 修复后重跑；修复前 `633a9e810722aedc` 的四个方案榜单与修复后逐行比对，分数、评级、排名、徽章完全相同，只有回放中 33 只无财报股票的 EPS 缺失原因由 `eps_behind_current` 改为 `no_current_fiscal`）。board JSONL 未提交（体积大，可用上面命令重建）；summary JSON/MD 已提交。

### 第 3 条 live（as_of 2026-09-29）：通过

退出码 0；成员 915、建包 915、单股错误 0；`future_leak_count` 0；`params_bootstrap` true。

| 方案 | scheme_hash | 排名/观察/剔除 | STRICT/FULL/BELOW | 降级原因 | pe_redflag | F1 前 40 最大行业 |
|---|---|---|---|---|---|---|
| F1 | 3f268258d997bbdc | 864/51/0 | 63/204/597 | net_margin_down 332、ntm_not_above_ttm 117、new_listing_gate 11 | 77 | Energy 37.5% |
| F1-rank | 0c2c2949bf3574c2 | 864/51/0 | 121/170/573 | 同 F1 | 77 | Energy 50% |
| F1-exfin | ae1a0f9df6555408 | 687/29/199 | 53/181/453 | 270 / 72 / 10 | 55 | Energy 42.5% |
| F0 | 8a5f1685b88139f8 | 890/25/0 | 56/204/630 | net_margin_down 345、new_listing_gate 13 | 76 | Energy 35% |

- 观察原因（F1）：low_coverage 44、insufficient_quarters 7；live 没有 `identity_unverified`
- 豁免：F1/F1-rank/F0 各 153 行的 `gm_yoy`、`gm_level`、`fcf_margin_yoy` 记 `industry_exempt`；本期没有 `degenerate_cross_section`
- 因子覆盖（原始口径，915 行）：revenue_accel 872、eps_accel 793、revenue_yoy 900、eps_sue 807、growth_4q 755、gm_yoy 900、gm_level 910、fcf_margin_yoy 828、surprise 734、revision 737。缺失原因分布见 `live-0929/summary-2026-09-29.md`
- F1 前 40 名单见 `live-0929/summary-2026-09-29.md`

### 第 4 条 点名样本：全部符合预期（FER 附说明）

| 样本 | 预期 | 实际 | 结论 |
|---|---|---|---|
| NVDA / MSFT / AAPL | F1 已排名；10 个因子除 revision 外都有值；无 ntm_not_above_ttm | 三只都已排名（STRICT 67.3 / BELOW 48.3 / FULL 60.8），10 个因子全有值（revision 也有），无 NTM 降级 | 通过 |
| JPM | 方案口径 gm_yoy、gm_level、fcf_margin_yoy 为空，原因 industry_exempt；原始值保留；F1 已排名；F1-exfin 剔除 | 三项 `industry_exempt`；factor_row 保留 gm_level 66.5、gm_yoy 6.39；F1 STRICT 72.0；F1-exfin `excluded` | 通过 |
| V | F1-exfin 不剔除 | F1-exfin 已排名（BELOW，net_margin_down） | 通过 |
| KLAC / ANET | eps_sue 有值，依赖窗口里有 eps_split_rescaled 季度但不拦截 | eps_sue 1.278 / 8.612；两只的 SUE 依赖窗口（最近 13 季）里各有 5 个 `eps_split_rescaled` 季度 | 通过 |
| COIN / FER | eps_sue 缺失，原因 eps_conflicting_quarter；其他因子照常 | 两只都是 `eps_conflicting_quarter`。COIN 其余因子正常计算，已排名 | 通过 |
| TSM | surprise、revision 缺失，原因 unit_unverified；徽章 ntm_gate_unknown | 两项 `unit_unverified`；徽章含 `ntm_gate_unknown`；F1 STRICT 71.8 | 通过 |
| BHP | revenue_yoy 有值并挂 semiannual_period；revenue_accel 缺失，原因 no_prior_quarter | revenue_yoy 18.69 + `semiannual_period`；revenue_accel `no_prior_quarter` | 通过（BHP 覆盖率不足，状态为观察） |
| CRWV | listing_days < 730，按次新三关判定，不走净利率闸门 | listing_days 550；降级原因 `new_listing_gate`、`ntm_not_above_ttm`，没有 net_margin_down | 通过 |
| COST | revenue_yoy 经天数折算，与独立复算一致 | 11.104276，与独立复算差 0（本季与基期都是 112 天） | 通过 |

说明：
- FER 的其余因子也缺失，但原因与 EPS 冲突无关。它被标为半年报公司，本季按季报、基期按半年报，天数对不上（`period_days_out_of_range`），且预期类因子是 `unit_unverified`。所以覆盖率不足，状态为观察。
- COST 的 `eps_sue` 缺失（`no_yoy_pair`）：FMP 给 COST 的 EPS 财季日期与财报日期差约 20 天（EPS 2026-08-10，财报 2026-08-30；上年 EPS 2025-08-31）。相邻年份差 344 天和 386 天，超出 365±20 天，按规则缺失。独立复算得到同样结果。live 全体 `no_yoy_pair` 11 只。

### 第 5 条 独立复算：通过

`independent_check.py` 只用 sqlite3 与 statistics。NVDA、MSFT、AAPL、COST、JPM 共 18 个比对项，差值全部为 0，退出码 0。明细见 `independent_check.json`。
- JPM：只比 revenue_yoy、gm_yoy。另两项照实列出：fcf_margin_yoy 在引擎里是 `fcf_zero_placeholder`（两季 capex 都是 0），F1 按豁免；eps_sue 两边都是 6.536
- COST eps_sue：两边都缺失（理由见上）

### 第 6 条 历史回放（as_of 2024-08-17）：通过

退出码 0，泄漏 0，成员 817，单股错误 0。`revision` 覆盖 0（原因 no_current_snapshot 660、unit_unverified 157）。`surprise` 614 个全部来自 `vendor_estimate`。
- D-9：带 `eps_split_retrospective` 的 5 只：ANET、APH、KLAC、MNST、ORLY（live 为 0）
- 观察原因：insufficient_quarters 77、low_coverage 58、identity_unverified 13
- 回放期没有 NTM 周快照，669 个已排名行全部挂 `ntm_gate_unknown`，PE 护栏也不会触发（`pe_redflag` 0）。历史榜实际只有两道闸门
- 数据问题（不在 M6 范围）：BMNR 以 F1 第 6 名出现在 2024-08-17 回放里。快照 `historical_market_cap` 记 BMNR 2024-08-16 市值 160 亿美元，当时实际只是微盘股，所以回放成员解析把它算进了 $10B 池。需要在 M1/D9 数据层排查同类市值错误

### 第 7 条 耗时

live 8.3 秒，回放 10.0 秒（含建包），远低于 5 分钟。

### 第 8 条 不写库

CLI 用 `MarketStore(db_path=..., read_only=True)`，`tests/test_prosperity_score_cli.py::test_cli_scores_all_schemes_read_only` 断言通过。

## code review high（2026-09-30）

10 条，逐条核实后：

| # | 结论 | 处理 |
|---|---|---|
| 1 `--as-of 20260929` 在 Python 3.11+ 被接受，原样字符串让泄漏检查失效 | 成立（复现：退出 0） | 两个 CLI 只接受 YYYY-MM-DD，否则退出 4；加测试 |
| 2 revision 分子随固定季度数（2–4）变化 | 成立但属口径：live 737 只里 734 只是 4 季，3 只 2–3 季；北极星定义即"固定财季一致预期变化 ÷ 股价" | 未改，交 Boss 定是否按 4 季折算 |
| 3 冻结包的 as_of/版本没有记进 BoardResult | 成立（M8 存档需要） | BoardResult 加 `frozen_as_of`、`frozen_params_version`；加测试 |
| 4 读取参数包不校验内容哈希 | 成立 | `params_from_dict` 重算，不符抛 `params_version_mismatch`；加测试 |
| 5 listing_date 规则与 `_listing_evidence` 在 NULL 市值行上可能分歧 | 快照里 NULL 市值行为 0；写法为计划 D-2 A 批准 | 未改 |
| 6 PE 护栏里预期亏损股（E/P 为负）总落在最低分位 | 成立但属口径：F1 的 77 个红旗里 15 个是 E/P 为负 | 未改，交 Boss 定 |
| 7 无当前财季时 EPS 缺失原因误记 `eps_behind_current` | 成立 | 改为 `no_current_fiscal`；加测试 |
| 8 未使用的常量与 min_quarters 进哈希 | `FAMILIES`、`EXPECTATION_KEYS` 删除；min_quarters 是北极星"登记所需历史"的一部分，保留在哈希里 | 部分采纳 |
| 9 rankable/usable 重复计算 | 成立但无影响（全流程 8–10 秒） | 未改 |
| 10 因子行失败的点名样本被记成"非成员" | 成立 | 非成员改按建包结果判断；加测试 |
