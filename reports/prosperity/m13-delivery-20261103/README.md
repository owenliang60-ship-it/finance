# 景气 M13 Telegram 投递：本地真实数据验收（2026-11-03）

Plan：`docs/plans/2026-11-03-prosperity-m13-delivery.md`。分支 `prosperity/m13-delivery`。
`code_version` `13e5f9d5183f1b8e`（不变）。

## 输入

| 输入 | 路径 | 说明 |
|---|---|---|
| prosperity.db | `/tmp/m12/prosperity.db` | 11-01 快照，sha256 前 16 位 `ca0aa05a7cfccf75`，21 张 F1 季末冻结榜 + 10-31 live 周榜 |
| market.db | `/tmp/m13/market.db` | 本地副本 `.backup`（11-03 09:00 拉取），`quick_check` ok |
| 池文件 | `reports/prosperity/pool-q1-20261102/pool-20261031.json` | 10-31，36 只，generated_at 2026-11-01T04:00:00Z |

发送端换成写本地文件的假实现（`/tmp/m13/accept.py`、`/tmp/m13/accept_pool.py`），不联网。

## 结果

| # | 项 | 结果 |
|---|---|---|
| A6 | 真实数据全流程 | 通过。预置上周台账（health 基线 = 倒数第二个冻结日 2026-05-30）后，一次运行发出三项：网页 `prosperity-web-20261031.html` 10,249,963 字节、88 节；进出文本；体检报告 `prosperity-health-F1-20260829.html` 83,001 字节、八章标题齐全。总耗时 305.7 秒，峰值内存 2.47 GB（主进程 + 两个子进程的快照与报告）。两个输入库 SHA256 前后不变；`tmp/` 已删除。立即重跑 → `already_delivered`，没有再发 |
| A6 | caption | `景气榜 2026-10-31 · F1 STRICT 55 / FULL 194 · 入池 36`（与 M11 验收的 10-31 计数一致） |
| A6 | 进出文本（池可用） | 时钟设为池文件生成之后（11-01 05:00Z）：`入池 36 只 / 对比 2026-10-24 / 新进（34）… / 移出（1）: GONE\_X`；台账写入 36 个 `pool_members`。证据 `pool-text.json` |
| A6 | 进出文本（池不可用） | 时钟设为 10-31 09:00Z（早于池文件生成）：池文件校验报 `prosperity_pool_invalid`，文本只写原因，不比较，台账不写 `pool_members`。审查前 caption 写成"入池 0"（code review #2），已改为"入池 未知" |
| A6 | 审查修复后重跑 | 时钟 11-01 05:00Z：池文件通过新鲜度校验与原榜绑定（`pool_binding`），三项都发出；caption `入池 36`；台账 36 个 `pool_members`、health `sent_at` 冻结 2026-08-29；耗时 320.4 秒；两库 SHA256 不变；重跑 `already_delivered`。证据 `sent.json`（当前版本） |
| A7 | 全量测试 | 审查修复后 12 failed / 4386 passed / 4 skipped，失败名单与基线 `817007dd` 完全相同，新增失败 0 |

`out/` 里两个 HTML 是验收产物（约 10 MB，gitignore，可重新生成）。

## 云端待办（需要 ssh 授权，见 plan T6）

- 依赖：`python3 -c "import backtest.research.prosperity_health, backtest.research.prosperity_health_html"`（scipy）。
- A9 上传实测：10 MB 网页发到私聊，`real` < 300 秒。
- A11 云端体检报告试跑：两个 `.backup` 快照 + `prosperity_health.py --schemes F1`。云端峰值内存要留意：本地全流程 2.47 GB。
