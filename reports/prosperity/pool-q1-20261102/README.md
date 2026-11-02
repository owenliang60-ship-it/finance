# 景气精选池第一档规则 · 验收证据（2026-11-02）

Plan：`docs/plans/2026-11-02-prosperity-pool-q1.md`；规则：北极星 §4.1（2026-11-02 修订）。

新规则：F1 最新周榜中，排名 ≤ ceil(可排名数 / 5)（第一档）、评级 STRICT/FULL、成交额前 100，三条都满足的全部进池。

## 输入快照（只读，与 M12 体检报告同一份）

| 文件 | SHA256 前 16 位 | 说明 |
|---|---|---|
| `/tmp/m12/prosperity.db` | `ca0aa05a7cfccf75` | 无 `-wal` 文件 |
| `/tmp/m12/market.db` | `e60d78c3e02ae949` | `-wal` 0 字节 |
| `trial/q1_liq.json` | `8f8a36df991e6574` | 试算基准，2026-11-02 16:04 在 main `b539dd18` 代码上生成，原样归档 |
| `trial/q1_liq.py` | `6c14705f4ad196b1` | 生成基准的脚本，原样归档（`ROOT` 指向当时的 main worktree） |

运行前后两个库的哈希不变。`code_version` = `13e5f9d5183f1b8e`（本分支未改）。

## 运行

```bash
PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
R=reports/prosperity/pool-q1-20261102
"$PY" reports/prosperity/m10-pool-20261101/acceptance.py --market-db /tmp/m12/market.db --db /tmp/m12/prosperity.db --out $R
/usr/bin/python3 -I $R/verify_pool.py --market-db /tmp/m12/market.db --db /tmp/m12/prosperity.db --pool $R/pool-20261031.json > $R/verify.txt
"$PY" $R/tamper_ter.py --market-db /tmp/m12/market.db --db /tmp/m12/prosperity.db > $R/tamper.txt
"$PY" $R/pool_returns.py --market-db /tmp/m12/market.db --db /tmp/m12/prosperity.db --baseline $R/trial/q1_liq.json --save $R/returns-computed.json > $R/returns.txt
```

## A2 · 10-31 真实池 — 通过

`pool-20261031.json`：36 只，STRICT 17，`tier_cut` 170（可排名 848），`eligible` 170，最大排名 159，窗口 2026-09-21 至 10-30，`source.content_hash` 前缀 `305f4393`。生产 `validate_prosperity_pool` 以 `now=2026-11-01T04:00Z` 完整校验通过。M10 旧规则副本（45 只）被新校验器按 `PoolCriteriaMismatch` 拒收。

## A3 · 独立核对 — 通过

`verify_pool.py` 只用 sqlite3，不 import 本仓库，用 SPY 交易日历。逐只比对代码、排名、评级、成交额排名，另比对 `tier_cut`、`ranked.covered`、`eligible`、`universe`。`verify.txt`：**expected 36 (STRICT 17); pool 36; differences: 0**。

篡改验收：末位成员排名改 171 → 差异 1、退出 1；删一个成员 → 差异 31、退出 1。

## A3b · 改人数负例（TER）— 通过

`tamper_ter.py` 生成 `pool-20261031-ter.json`（M11 plan A4 ④ 直接使用这个路径）：可排名数 848 → 899、切点 170 → 180，加入排名 178 的 TER（FULL，成交额第 96，字段取自原榜与生产 `liquidity_ranks`），共 37 只。

`tamper.txt`：
- 生产 `validate_prosperity_pool`：放行。证明光看文件拦不住。
- `board_mismatch`：返回 `'ranked'`。
- `verify_pool.py`：退出 1，3 处差异（只数、ranked、tier_cut）。

## A4 · 回放与收益 — 通过

- `history.json`：22 期只数依次为 21、24、31、21、23、21、18、25、24、31、24、30、32、25、31、30、28、34、31、36、34、36，与 plan §2 逐期一致。早期 5 期（2021-09 至 2022-09）可排名覆盖率 0.834–0.891，被 0.90 覆盖率门拒收，这是 M10 起的既定行为，这里只核对只数。
- `pool_returns.py`：21 张 F1 季末榜，用生产 `build_pool` 在同日周榜上选池，用 M12 函数算收益。和试算基准按 (持有期, as_of) 逐项比对：每期只数、每期超额，以及 n、mean、lo、hi、t、p、跑赢次数。整数要求完全相同，浮点容差 1e-12。`returns.txt`：**checked 135 values; differences: 0**。
- 汇总（比扩展池等权均值多涨）：

| 持有期 | 期数 | 平均超额 | 95% 区间 | 跑赢 |
|---|---|---|---|---|
| 1 季 | 20 | +2.92% | [−0.24%, +6.07%] | 12/20 |
| 2 季 | 19 | +9.13% | [+0.68%, +17.58%] | 14/19 |
| 3 季 | 18 | +11.29% | [+0.82%, +21.76%] | 14/18 |

- 篡改验收：基准副本改一期超额 +0.001 → 差异 1、退出 1；改 3 季区间下限 +0.001 → 差异 1、退出 1。
- 价格收益不含分红；历史榜为事后回放（approximate）；18 期样本，区间宽。

## A5 · 回归 — 通过

- `code_version()` = `13e5f9d5183f1b8e`，未变。
- 全量 `pytest -q -rfE --tb=no -p no:cacheprovider`（`.venv` Python 3.13）：
  - 分支：`branch-pytest.txt`，12 failed / 4299 passed / 4 skipped（在审查修复前的 `312e9b9b` 上运行）。
  - 基线：`baseline-pytest.txt` 复制自 `reports/prosperity/m12-health/branch-pytest.txt`（M12 在 `4b07d03d` 上的运行）。原因：自动权限拒绝在本会话新建 main worktree 跑全量。`4b07d03d..b539dd18` 之间没有 `.py` 改动（`git diff --stat 4b07d03d b539dd18 -- '*.py'` 为空），所以这份结果就是 main `b539dd18` 的基线。
  - `failure-diff.txt`：**新增 0，消失 0**，共有 12 个。7 个 `test_breadth_buy_quality` 是裸 worktree 没有研究数据文件；5 个 `test_morning_report` 依赖 live 概念注册表。两组都与本改动无关。
- 审查修复后重跑景气全套 `pytest tests/test_prosperity_*.py tests/test_run_prosperity_wrapper.py -q`：**550 passed**（M12 为 536；本改动新增 14 个）。修复只改 `prosperity_pool.py` 和它的测试，所以没有重跑全量。

## 审查修复后重跑（2026-11-02，`/code-review high` 7 条，见 plan §14）

- `criteria.tier` 去掉 `keep`，所以重新生成了 `pool-20261031.json`、`pool-20261031-ter.json`、`history.json`。
- A2：仍为 36 只、STRICT 17、`tier_cut` 170、哈希前缀 `305f4393`。22 期只数不变。
- A3：差异 0；两份篡改副本退出 1。
- A3b：结果不变，`board_mismatch` 返回 `'ranked'`。
- A4：从头重算 `returns-computed.json`，135 值差异 0；两份篡改基准退出 1。
- 输入快照哈希前后不变。
