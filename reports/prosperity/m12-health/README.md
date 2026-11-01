# 景气 M12 体检报告 · 验收证据（2026-11-01）

Plan：`docs/plans/2026-11-01-prosperity-m12-health-report.md`；规则：北极星 §4.3（2026-11-01 修订收益口径与生成时机）。

## 输入快照（只读）

| 文件 | 来源 | SHA256 |
|---|---|---|
| `prosperity.db` | `scripts/sync_prosperity.py --db /tmp/m12/prosperity.db`，2026-11-01 14:08Z 完成（sha256 + quick_check + 候选校验通过）；与 M10 验收快照同一哈希 | `ca0aa05a7cfccf753799137d3bbe1df3d44004fe8f51e794533faf586ca09ef6` |
| `market.db` | 主仓库 `data/market.db` 用 SQLite backup API 做的 WAL 安全副本，2026-11-01 13:57Z；quick_check ok；SPY 最新 2026-10-30 | `e60d78c3e02ae94944d6023f49feb3860de46f2da23edb74aed5c28d24231051` |
| `premium_pool.json` | 云端 `data/pool/premium_pool.json` 只读副本（as_of 2026-10-30，47 只，universe 899） | `39f73473d78f4eef3c260b9f06d019cbf862b2bb13c556394d1dccc87ca2e60c` |

`code_version` = `13e5f9d5183f1b8e`（本分支未改）。

## 运行

```bash
PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
R=reports/prosperity/m12-health
"$PY" scripts/prosperity_health.py --db /tmp/m12/prosperity.db --market-db /tmp/m12/market.db --out $R/out
"$PY" $R/verify_health.py --db /tmp/m12/prosperity.db --market-db /tmp/m12/market.db --summary $R/out/summary.json --out $R
"$PY" $R/premium_check.py --db /tmp/m12/prosperity.db --market-db /tmp/m12/market.db --premium /tmp/m12/premium_pool.json --out $R
```

产物：`out/F1.html`、`out/F1-rank.html`、`out/F1-exfin.html`、`out/F0.html`（各 76–81 KB）、`out/summary.json`、
`verify.txt`、`split-sensitivity.txt`、`premium-check.txt`。

## A6 · 真实数据生成 — 通过

- 四个方案都生成成功，退出码 0。`/usr/bin/time -l`：229 s，峰值 RSS 1.27 GB（审查修复后重跑；只读 live 周榜，Premium 多读 `fmp_earnings`）。
- F1 已满期数 1/2/3 季 = **20 / 19 / 18**，进行中 1 / 2 / 3。
- 动量对照从 **2022-03-19** 期起；共同有效期 1/2/3 季 = 18 / 17 / 16 期，都从 2022-03-19 起，分别到 2026-05-30 / 2026-03-21 / 2025-11-29。
- Newey-West lag 1/2/3 季 = **1 / 2 / 3**（按实际重叠算）。
- BH 检验族 24 项，24 项都有 p 值。
- 拆股记录：F1 universe 内 108 条，三态为调整 8、已复权 84、未决 16。plan §2 的全表试跑是 115 条（调整 8、已复权 85、未决 22）。差的 7 条是不在任何冻结榜 universe 里的代码：GOOGL（已复权）、K、MOB、PARA ×3、TTD（都是窗口内无价格的未决记录）。
- 调整的 8 处，实际跳变日：APH 08-27、BKNG 04-06、CRWD 07-02、CTVA 10-01、CVNA 05-04、DD 06-18、FDX 05-27、KLAC 06-12（均为 2026 年）。
- 收益覆盖率（可排名行）：1/2/3 季 99.9% / 99.8% / 99.7%。未知 83 条，原因：`split_unresolved` 13/18/23、`ended_early` 6/6/5、`split_suspect` 2/3/4、`price_gap` 1/1/1。
- HTML：8 章各出现一次、顺序固定；头部三条固定说明都在；没有外部资源（src/href 指向外部 0 处）；两张内联 SVG。1200 px 与 390 px 宽度截图检查过：手机宽度下宽表在自身容器内横向滚动，页面没有横向溢出。

## A7a · 独立重算（F1）— 通过

`verify_health.py` 只用标准库 + sqlite3 + numpy，不 import `src`/`terminal`/`backtest`。交易日历用"一半成员有价格"规则（生产用 SPY），两者在全部窗口上**完全一致**。三态复权、收益与未知原因、基准、五档超额、Q1−Q5、Spearman IC、ICIR、标准差、NW lag 与 t、动量 Top 40、Top 40、重叠、HHI、换手、未知名单、拆股状态、24 项 BH 全部另写实现。

`verify.txt`：**checked 632 values; differences: 0**。

## A7b · 复权规则交叉检查 — 已报告（敏感性项，不设门槛）

第二种分类法用事件窗口首尾收盘比，不用单日阈值。与生产规则分歧 10 条，逐条价格见 `split-sensitivity.txt`：BDX 2026-02-10、BHP、BN、BWA、EHC、GE ×2、J（生产判已复权，另一法判未决或需调整）；IBM（生产判未决，另一法判已复权）；MNST 2026-08-11（生产判未决：窗口内 94→47→94→45 三次跳变；另一法判需调整）。

| 处理分歧记录的方式 | F1 五档平均超额最大变化 | Q1−Q5 均值最大变化 |
|---|---|---|
| 全部记未知 | 0.034 pp | 0.009 pp |
| 按另一法处理 | 0.031 pp | 0.011 pp |

结论：复权规则的取舍对报告结论没有可见影响（最大 0.03 pp，Q1−Q5 均值为 2.4–5.9 pp）。

## A8 · Premium 适配器 — 通过

`premium-check.txt`：10-31 live 周榜 universe 899 只，SHA256 与生产 Premium 文件一致；as_of 2026-10-30 重算 47 只，**与生产 47 只完全相同**（两边独有均为 0）。

点时截断用引擎自己的 `statement_known_on`（审查 #1 修复：占位日期不再当披露日）。A8 比的是当前数据，查不出点时问题，所以另加了单元测试（SAIL 型占位日期）。

另记一处限制：早期几期 Premium 只数很少（2021-09-04 期 4 只，基本面可评估率 5.2%：点时截断后多数公司不足 5 个季度）。2021 年两期不在共同有效期内（动量缺失）；但 2022-03-19、2022-06-04、2022-09-03 三期只有 4–5 只，它们在共同有效期内，Premium 对照这几期的值波动大。报告第 3 章逐期列出 Premium 只数与覆盖率。

## A9 · 回归 — 通过

- `code_version()` = `13e5f9d5183f1b8e`，未变。
- 景气全套：`pytest tests/test_prosperity_*.py tests/test_run_prosperity_wrapper.py -q` → **530 passed**（M10 时 492；+38 为 M12 新测试，含审查修复后新增 9 个）。
- 全量：同一解释器（`.venv` Python 3.13）、同一命令 `pytest -q -rfE --tb=no -p no:cacheprovider`。
  基线 = main `4feaa8d2` 的 detached worktree：`baseline-pytest.txt` 12 failed / 4242 passed / 4 skipped。
  本分支 `e28d835e`（审查修复后）：`branch-pytest.txt` 12 failed / 4280 passed / 4 skipped。
  `failure-diff.txt`：**新增 0，消失 0**，共有 12 个。与 M10 验收列出的 12 个相同：7 个 `test_breadth_buy_quality`（裸 worktree 无研究数据文件）+ 5 个 `test_morning_report`（概念注册表依赖 live data），与 M12 无关。

## F1 结果摘要（供阅读，不作上线门槛）

| 持有期 | Q1 超额 | Q5 超额 | Q1−Q5（95% 区间） | p_bh | IC 均值 | ICIR | 本方案 Top 40 超额 |
|---|---|---|---|---|---|---|---|
| 1 季 | +1.91% | −0.51% | +2.42%（1.06%, 3.78%） | 0.009 | 0.033 | 0.53 | +3.56% |
| 2 季 | +4.47% | −1.05% | +5.53%（2.33%, 8.72%） | 0.009 | 0.043 | 0.61 | +9.00% |
| 3 季 | +5.64% | −0.21% | +5.85%（1.27%, 10.43%） | 0.033 | 0.039 | 0.54 | +11.40% |

- 五档**不单调**：Q1 明显领先，Q2–Q5 之间没有稳定顺序（3 季 Q3 −1.95% 低于 Q5 −0.21%）。信号集中在头部一档。
- 共同有效期 16 期 3 季超额：本方案 Top 40 +10.9%，动量 Top 40 +8.6%，Premium +23.0%（approximate；2022 年三期只有 4–5 只）。扩展池等权 3 季平均收益 8.6%。审查修复前 Premium 为 +22.4%（有 59 个期×股票用到未公布季度），修复后变化 +0.6 pp，方向不变。
- 与动量 Top 40 平均重叠 9.2 只；Top 40 最大行业平均占 27.6%，HHI 0.177；相邻季末榜换手 69%。
- 3 季最差 STRICT：SNAP −80%、TXG −76%（2021-09-04 期），LYFT −72%（2022-03-19），SRPT −71%（2024-08-31）。
- 预期族：surprise 21 期有 z，revision 只有 1 期（2026-08-29），均为 UNKNOWN。

## 审查修复后重跑（2026-11-01，`/code-review high` 10 条，见 plan §13 第二轮）

同一份输入快照，修复提交 `9e79fb46` 后重跑：A6 生成 rc 0（229 s）；A7a `verify.txt` 632 值差异 0；A7b 敏感性不变（0.034 / 0.031 pp）；A8 Premium 47 只仍与生产一致。分档、IC、动量、结构章节的数字与修复前相同；只有 Premium 对照变化（见上方摘要）。
