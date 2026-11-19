# 晨报 0d 切换为景气动量精选池：真实数据验收（2026-11-19）

Boss 2026-11-19：美股晨报 0d 只显示景气动量精选池。只改晨报读取与显示，周六链路不动，`code_version` 不变（13e5f9d5183f1b8e）。分支 `morning/0d-prosperity-momentum`，未 merge。计划 `docs/plans/2026-11-19-morning-0d-prosperity-momentum.md`。

1. 0d 只列景气精选池 ∩ 14 周动量榜前 30，按全主池动量名次升序，10 列：标的、动量名次、14周涨幅、景气排名、评级、景气分、收盘、EMA30、相对EMA30、当前市值。
2. 名单来自周六 M16 已写出的 `data/prosperity-delivery/out/<as_of>/momentum-<YYYYMMDD>.json`，`as_of` 取已绑定的池文件；校验不过时本段显示原因，不回退上周，不退回全池。
3. EMA30 门与市值门仍对全池计算，只显示入选行；小标题的 EMA30 计数按入选行重算。
4. 删去与 Premium 的对比块和 Premium 列；第 1 段 PMARP、第 2 段量能异常仍按全量景气精选池分组，🔴 仍标 Premium。

## 输入（都只读）

| 输入 | 说明 |
|---|---|
| prosperity.db | `.backup` 快照，含 2026-11-14 F1 live 周榜；sha256 前 16 位 `d91ab61867ff3d9b` |
| market.db | `.backup` 快照，价格到 2026-11-17；sha256 前 16 位 `18a326f57d6b1007`。文件头是 WAL 格式，用 `immutable=1` 打开，否则只读打开也会在旁边建 `-wal`/`-shm` |
| 池文件 | 云端 `data/pool/prosperity_pool.json`，as_of 2026-11-14，35 只 |
| 动量 JSON | 云端 `data/prosperity-delivery/out/2026-11-14/momentum-20261114.json`，主池 888 只，价格到 2026-11-13 |
| 晨报信号 | 云端 `data/scans/morning_20261118_080133.json`（as_of 2026-11-17，PMARP 24 条、量能异常 38 条） |

运行：`accept.py ROOT WORK EVIDENCE PNG_DIR`。脚本走真实读取端（`load_bound_prosperity_pool` 按 2026-11-18 08:01 SGT 判断新鲜度；`load_prosperity_momentum_picks` 读 `WORK/delivery`）和真实 `_build_prosperity_compass` / 三处渲染；`get_store()` 换成指向快照的只读库。PNG 写到 `PNG_DIR`（本次 `/tmp/morning-0d-accept-png/`），不入 git。

## 结果（`result.json`）

| 项 | 结果 |
|---|---|
| 入选 | ABBV #5、V #21、COST #23、MDT #27，与"池文件 ∩ JSON 合格行前 30"独立计算一致；未评分 0 只 |
| 0d 数值 | 纯 sqlite 独立重算：14 周涨幅（窗口首末收盘 2026-08-06 → 2026-11-13）、EMA30（同 127 行窗口）、7 天市值，以及文本单元格，差异 0（`section-0d.txt`） |
| 小标题 | `景气动量精选池 4 只（截至 2026-11-14，景气池 35 只 ∩ 14周动量榜前 30，动量价至 2026-11-13） \| EMA30 4/4 \| 站上EMA30 4 只`；全池 EMA30 就绪 34/35（SPCX 不足 127 行），不影响本段 |
| 文本 | 无 `_`、无 Premium |
| HTML | `morning_report_2026-11-17.html`：0d 块只有 4 只入选，其余 31 只池成员不出现；无"与 Premium 对比"；第 1/2 段分组标题在 |
| 第 1 段 PMARP | 景气精选池 1 / 其他 Extended 23，与"命中 ∩ 35 只池成员"独立计数一致，错组 0 |
| 第 2 段量能异常 | 景气精选池 1 / 其他 Extended 37，一致，错组 0 |
| 图 | 5 张 PNG，`03_00d_prosperity_pool.png` 副标题完整（本机字体实测 1950 px，上限 2248 px） |
| 输入 | 5 个输入文件运行前后 SHA256 不变；`WORK` 目录文件清单不变 |

与云端 11-18 晨报的同池扫描对照（只报告，不作门槛）：35 只的收盘、上方/下方、景气排名一致。EMA30 最大相对差 6.9e-4：云端用晨报共享的更长价格帧起算递推 EMA，本次从 127 行库数据起算，取 252 行起算时差到 7.7e-6。18 只市值不同：快照里 2026-11-17 的市值行与那次运行读到的值不同（其中 17 只在快照最近 5 个市值行里找不到同值，1 只等于 11-13 的行）。所以门槛只用同一快照上的独立重算。

## 测试

- 定向：`test_morning_report.py`、`test_prosperity_morning.py`、`test_prosperity_delivery.py`、`test_momentum_board.py`、`test_prosperity_schemes.py`、`test_run_prosperity_wrapper.py` 共 370 passed, 1 skipped（基线 282 passed, 1 skipped）。
- 全量：`pytest tests -q` 4579 passed, 4 skipped, 0 failed（389 s）。worktree 的 `data/` 下数据库与池目录只读链接到主 checkout。
- 语法：改动的 5 个文件在 Python 3.11.0 与 3.9.6 下 `compile()` 通过；本机没有 3.10，云端 3.10.12 复验在 merge 部署后做。

## 与历史验收脚本的关系

`reports/prosperity/pool-display-20261109/accept.py` 调用旧签名 `_build_prosperity_compass(premium, {}, as_of, scan)`，并按旧 0d 版式（全部成员、Premium 列、对比块）解析文本。本分支把签名改为 `_build_prosperity_compass(price_frames, as_of, scan)`，该脚本不能在本分支上重跑；它的结果只对当时的代码成立，留档不改。
