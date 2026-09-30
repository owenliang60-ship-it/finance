# 景气内核口径改动验收（Boss 2026-09-30 ①②④）

分支 `prosperity/kernel-decisions`，基线 main `6c9b3e55`。快照：`data/backups/prosperity/phase0-accepted-input-20260929/market.db`（只读）。`code_version` 由 `47e5c151177bec57` 变为 `fa8ab1fc048d0b7e`（预期内）；四个方案的 `scheme_hash` 不变。

## 改了什么

| 项 | 规则 | 位置 |
|---|---|---|
| ① | 修正幅度 = 固定财季（2–4 季）一致预期变化合计 × 4 ÷ 实际季数 ÷ 股价；原始合计和季度列表照旧保留 | `factors.py` expectation_factors |
| ② | PE 护栏行业分位只用 NTM E/P > 0；E/P ≤ 0 挂 `ntm_loss`，同时有负修正或负 surprise 直接挂 `pe_redflag`（不看分位与行业规模） | `kernel.py` score_board |
| ④ | EPS 季度配到点时可见的三表财季：最近且 ±45 天内、一一对应，平局或两季争同一财报则不配；因子层（SUE、ΔSUE、4 季平均、当前季对齐）用配上的日期，一致预期仍用 FMP 日期；存档 `eps_window` 末尾加配对日期 | `street_eps.py` pair_statement_dates、`packet.py`、`factors.py` |

## 重跑命令

```bash
.venv/bin/python scripts/prosperity_score.py --as-of 2026-09-29 --mode live --observed-at 2026-09-29 --db "$DB" --out-dir live-0929
.venv/bin/python scripts/prosperity_score.py --as-of 2024-08-17 --mode replay --db "$DB" --out-dir replay-240817 --no-beta
```

main 与分支各跑一遍，`scripts/compare.py <dir>`（`<dir>/main/…`、`<dir>/new/…`）逐行对比，结果在 `board_diff.json`。board JSONL 未提交（体积大）。审查修复后重跑，8 份榜单与修复前逐字节相同。

## 对比结果（与 main 逐行）

- **前 40 名**：live 四个方案不变；回放只有 F1-exfin 换一只（SPOT 进、WSM 出，间接影响）。
- **① 直接影响**：live 2 只（CRH、ESLT，季数不足 4）；回放期没有修正数据。
- **② 直接影响**：F1 红旗 77 → 83，`ntm_loss` 25 只，其中 15 只挂红旗（即原先 15 个负 E/P 红旗）。新增红旗 7 个是盈利股里估值最贵且有负修正/负 surprise 的（AMZN、TKO、MEDP、MTD、HUM、FWONK、ESS）；少掉的 LNT 是因为 ETR（④ 后进入排名）加入了公用事业分位。
- **④ 直接影响**：live eps_sue 变化 9 只（AMCR、AZO、COST、ETR、GSK、KR、PAG、PEP、TECK），COST BELOW → FULL，AMCR、ETR 观察 → 排名；回放 AMCR、FERG、KR 当前季对齐后 surprise 与 4 季增速恢复，BIP 观察 → 排名。
- **间接影响**：冷启动参数按当周截面重估，F1 live 中位分数变动 0.008；NBIX、AMP、ARM 因毛利率斜率档位边界重算移动 2–5 分；FSLR 50.003 → 49.996 跨过 FULL 线。

## ④ 的同比样本统计（`scripts/yoy_obs.py`）

SUE 窗口（当前 + 前 8 季）能配上的同比对数，FMP 日期 vs 配对日期：

| 榜期 | 变多 | 变少 | 不变 |
|---|---|---|---|
| live 2026-09-29 | 10 | 1（TECK） | 898 |
| 回放 2024-08-17 | 6 | 2（AEM、KR） | 749 |

变少的 3 只都是 FMP 三表日期本身错：TECK 2024-10-23（应为 09-30）、AEM 2023-01-27（应为 2022-12-31）、KR 2021-04-28（应为 5 月下旬）。全库孤立错日期 8 个（`scripts/isolated.py`：前后一年同季都对不上、前后两年彼此相差约两年），当前成员 4 个：RIO、AEM、TECK、KR。同一错日期也在扭曲这些股票的三表因子（TECK 两季被当成 115/69 天），应在数据层用现有财季修复处理，不在 ④ 里加例外。

## code review high（2026-09-30）

8 条，逐条核实后：

| # | 结论 | 处理 |
|---|---|---|
| 1 窗口内部分 EPS 季度配不上，混用两种日期可能拆散同比对（FERG、BIP 类稳定漂移） | 理论成立；实测 3 个榜期混用窗口 4–5 只，混用后同比对均不少于 FMP 日期（BIP 6→8）；建议的“有一季配不上就全退回 FMP”会让这几只的改进全部消失（`scripts/mix.py`） | 未改 |
| 2 consensus 的 TTM 连续性仍按 FMP 日期 | 理论成立；实测 live 与 2024-08-17 回放 0 只受影响（`scripts/ttm.py`）；TTM/NTM 与 FMP 预期同一套日期 | 未改 |
| 3 拆股审计仍按 FMP 日期 ±20 天对 GAAP，漂移大的股票逃过检查 | 原有限制，非本次引入 | 未改，记为后续 |
| 4 两个 EPS 季度争同一财报时两个都不配 | 按设计：退回原行为（FMP 日期），与批准的“一一对应”一致 | 未改 |
| 5 revision 折算除以季数无保护 | `revision_inputs` 保证有 delta 时 ≥2 季；违反时报错比静默更安全 | 未改 |
| 6 预期亏损红旗不看行业规模；汇总只数 pe_redflag | 前半为 ② 设计（当前无降级方案注册）；后半成立 | 汇总表加“预期亏损”列；加测试 |
| 7 配对对全部财报日期排序 | 成立（小） | 改为取最近两个 |
| 8 COST 夹具从另一个测试模块导入 | 成立 | 移到 `tests/prosperity_fixtures.py` |

## 测试

- 景气测试：276 passed。
- 全量：worktree 4670 passed / 12 failed，12 个都在 `test_morning_report.py`、`test_breadth_buy_quality.py`，缺实盘数据导致；软链接实盘数据后这两个文件 187 passed、1 skipped。main 全量 4677 passed / 1 failed（`TestVolumeConcentrationFreezeDateParity`，原有问题，与本次无关）。
