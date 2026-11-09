# 景气精选池显示改动：真实数据验收（2026-11-09）

Boss 2026-11-09 定的四处改动，都只改显示，`code_version` 不变（13e5f9d5183f1b8e）。分支 `prosperity/pool-display`，验收代码 620535c0。

1. 网页页首目录前列出精选池（按景气排名）。
2. 每周私聊进出文本末行列出全部成员。
3. 晨报 0d 列全部成员，加"相对EMA30"列，站上的在前。
4. 晨报第 1 段 PMARP、第 2 段量能异常先列景气精选池，再列其他 Extended；🔴 仍标 Premium。

## 输入（都只读）

本机到云端下载方向只有约 10 KB/s（Boss 在外地），所以验收在云端跑：分支代码 `git archive` 解到 `/tmp/pooldisp-accept/code`（目录里没有 `.env`，不会外发），数据用 sqlite backup API 拍快照。验收完已删除临时目录。

| 输入 | 说明 |
|---|---|
| prosperity.db | 11-09 快照，sha256 前 16 位 `ab824e41c7b6e3fd` |
| market.db | 11-09 快照，sha256 前 16 位 `ea2a6cd9b9d8ffe7`（价格到 11-06） |
| 池文件 | 云端 `data/pool/prosperity_pool.json`，as_of 2026-11-07，36 只 |
| Premium 池 | 云端 `data/pool/premium_pool.json`；读时跳过新鲜度 |
| 晨报信号 | 云端 `data/scans/morning_20261107_080107.json`（as_of 2026-11-06，PMARP 137 条、量能异常 145 条） |

脚本 `accept.py ROOT WORK EVIDENCE`。私聊文本走真实 `deliver_week`，发送端换成记录函数；网页 CLI 在第 1 步已跑过，`deliver_week` 里复用那份页面。

## 结果（`result.json`）

| 项 | 结果 |
|---|---|
| 网页 | 10,248,943 字节；独立核对 `checked 72536 rows, 36 pool ranks, 36 pool list rows; differences: 0`；页首 36 行与池文件排名顺序一致 |
| 私聊文本 | `delivered`；caption `景气榜 2026-11-07 · F1 STRICT 51 / FULL 184 · 入池 36`；末行与池文件排名一致（`weekly-text.txt`） |
| 0d | 36 行：上方 7、下方 29、未就绪 0，上方在前；纯 Python 独立重算 EMA30（同 127 行窗口）和 7 天市值，差异 0（`section-0d.txt`） |
| 第 1 段 PMARP | 景气精选池 8 / 其他 Extended 129，与"命中 ∩ 池文件成员"独立计数一致，错组 0 |
| 第 2 段量能异常 | 景气精选池 4 / 其他 Extended 141，一致，错组 0（`section-1-2.txt`） |
| HTML / 图 | HTML 有分组标题（`morning_report_2026-11-06.html`）；6 张 PNG 用云端字体出图 |
| 输入 | 两个快照运行前后 SHA256 不变 |

审查前代码（2e1798a7）先跑过一轮，结果相同；审查修复后用 620535c0 重跑，即本目录。

## 测试

- 相关套件：314 passed, 1 skipped。
- 全量：main 33bcbcda 4445 passed / 0 failed；分支 620535c0 4461 passed / 0 failed；两边失败名单一致（都为空）。两个 worktree 都把 `data/market.db`、`company.db`、`pool`、`dollar_volume.db`、`breadth_study_1b` 只读链接到主 checkout。

## 审查（`/code-review high`，9 条）

| # | 结论 | 处理 |
|---|---|---|
| 1 | 0d 计算失败时第 1/2 段丢了分组 | 修：失败时保留池摘要与对比 |
| 2 | 文档把未 merge 的改动写成已上线；证据目录未建 | 修：北极星标"未 merge"；本目录 |
| 3 | 0d 自己重算 EMA30、未规范化代码 | 修：共享扫描返回每个成员的行 |
| 4 | 7 天市值规则第三份拷贝 | 修：抽出 `_observed_market_cap`，扫描与 0d 共用 |
| 5 | 核对脚本把"入池 0 只"误判为差异 | 修：按是否绑定判断；夹具验证修前 2 条差异、修后 0 |
| 6 | 空的景气组在 HTML/图里不显示 | 修：三处都显示 |
| 7 | 三处输出各自分组 | 不改：三处共用 `_prosperity_split` / `_split_groups`，与现有 `_pmarp_signal_cap_groups` 同一模式 |
| 8 | 私聊名单重复排序且放在快照步骤里 | 修：直接用校验过的排名顺序 |
| 9 | 旧 JSON 缺 `members`/`symbols` 时无回退 | 不改：没有重放旧 JSON 的路径；缺字段显示"显示异常 / 不分组"属于可见失败 |

三条修复测试在修复前代码上全部失败，修复后通过。
