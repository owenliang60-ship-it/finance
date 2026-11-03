# 景气 M9 周更运行与恢复（含 M10 精选池发布）

M9 已部署（2026-10-26 装周六 16:30 / 20:30 两条 cron）。M10 精选池随 live 周更发布。

## 入口与所有权

`market.db` 只读；云端 `data/prosperity.db` 是生产结果库。
本地验收使用独立的 `prosperity_research*.db`。每个结果库旁的
`<db_path>.state/` 保存候选证据和运行记录，不能与其他库混用。

云端所有手动与 cron 运行通过 `scripts/run_prosperity.sh`。
该入口检查继承的 job/resource 锁；直接调用时由 `cron_wrapper.sh`
获取 `finance_prosperity` 和 `market_db_writer` 两把锁。
不要直接并发运行写同一结果库的 Python CLI。

```bash
# 云端本周六（默认），只在部署获批后执行
scripts/run_prosperity.sh
# 云端历史重建，默认只到上周六
scripts/run_prosperity.sh --rebuild-chain
# 历史补跑只写库，不发布
scripts/run_prosperity.sh --as-of 2026-10-17
```

cron 使用计划中的周六 16:30 / 20:30 CST 两个条目。所有 replay 入口拒绝
本周和未来日期。`--live` 只接受本周六；不补造已经错过的 live 观察时间。

## 本地真实验收

先确认本周基本面任务结束，再取质量报告，最后 pull 市场快照。
记录快照 SHA256 和 UTC 时刻，确认报告 `after.as_of` 不晚于快照。
以下命令的日期、路径、时间和哈希必须来自本次实际快照，不照抄示例值。

```bash
"/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/prosperity_weekly.py \
  --live --market-db /absolute/path/to/fixed-market.db \
  --observed-at ACTUAL_SNAPSHOT_ISO_UTC --snapshot-sha256 ACTUAL_SHA256 \
  --quality-dir /absolute/path/to/quality --db /tmp/prosperity_research_m9.db
```

验收脚本在 `reports/prosperity/m9-scheduler-20261024/`：
`acceptance.py` 校验每次八榜/四榜落库后读回等于内存结果，记录峰值 RSS、耗时、体积；
`verify_freezes.py` 不导入景气内核，逐季复核候选证据、首次达标日和 60 个源库财季日期。
它不声称独立重做所有 M4 成员资格判定。

## 故障与恢复

| 退出码 | 含义 | 处理 |
|---|---|---|
| 0 | 计算成功并已发布（`published` / `already_published`）；不传 `--pool-path` 时为 `publication_not_configured` | 只有 `published` 或 `already_published` 才表示池文件是本周的 |
| 3 | 输入/基本面硬门控失败 | 修复输入证据后重试，不降低门槛 |
| 4 | 日期、模式或参数错误 | 更正参数；当前周只能 live |
| 5 | 已计算、发布未确认 | 同周重试只发布，不重算。池发布常见原因：`liquidity_window_short` / `liquidity_prices_stale`（价格没到或过期，先查 06:30 价格任务）、`pool_ranked_coverage_below_floor`（周榜可排名覆盖率 <90%；周榜已冻结，**同周重试不会好**，只能等下周或回北极星讨论门槛，不要反复重跑）、`pool_liquidity_coverage_below_floor`（有成交额排名的成员 <90%；价格缺失导致，**补齐价格后同周重试可以成功**）、磁盘错误。池文件此时是上一版，或本周已校验的新版（`os.replace` 后目录 fsync 或 `mark_published` 失败）；读取端不会看到半写文件 |
| 6 | 重建、空间、证据、恢复或运行故障 | 查 `.state/runs/` 的 error，保留候选记录 |
| 75 | job/resource 锁忙 | 等待下一次入口，不并发绕过锁 |

候选记录完整且未达标时直接复用。达标但八榜未提交时，按记录中的原
mode/cutoff/source_evidence 准备八榜，逐榜比 M8 内容哈希。
`recovery_input_changed` 表示输入已改变，停止该季及后续写入。
恢复原始来源后再重试；不能删除候选记录来绕过此检查。

损坏、错版本、错方案或校验失败的候选记录会阻断恢复。
用匹配的备份集合恢复数据库和状态目录。普通发布异常会回滚原副本；
若进程被杀后留下 `<db>.publish-pending`，先读取其中的 stage 路径，
核对保留的 old/new 副本并恢复完整集合，再清除标记，不能仅删标记继续写。

## 备份与拉取

正式入口在运行前、成功计算后制作匹配备份，每个源库只保留最近两套。
快照使用 SQLite backup API，包含 WAL 已提交内容；每套包含 gzip 压缩的数据库
（`database.sqlite.gz`，约原体积 28%）、状态目录和哈希清单。清单同时记录压缩前的
SHA256，校验和发布时先解压再核对，再跑 quick_check。
候选记录与数据库必须一起恢复。空间不足时不备份也不进入计算。

`sync_to_cloud.sh --pull` 会拉取云端结果库与状态。云端尚无结果库时显式跳过。
下载校验 SHA256、SQLite quick_check 和候选记录后发布；本地库被占用、WAL 非空或
存在未完成发布标记时拒绝替换。旧有效副本保留。

## 按版本归档（D5）

云端只保留两个 `code_version`：当前版本，和最近写过 live 周榜的另一个版本。
更早的版本归档到本地后再从云端移除。榜单内容不改写，只换存放位置。
cron 和周更入口都不会移除任何数据。空间不足时周更退出 6，日志和 Telegram 报警
列出可归档版本与预计释放空间。

1. 云端导出（只读源库）：
   `python scripts/prosperity_archive.py export --db data/prosperity.db --version <V> --out data/prosperity-archive/<V>`
2. 拉回本地，在本地副本上校验：
   `python scripts/prosperity_archive.py verify --manifest <本地目录>/manifest.json`。
   校验库文件与状态文件 SHA256、各表行数、每张榜经 M8 读回后的 `content_hash`，
   通过后在清单上标 verified。
3. 把已校验的 `manifest.json` 传回云端原目录，**Boss 确认后**执行：
   `python scripts/prosperity_archive.py remove --db data/prosperity.db --manifest data/prosperity-archive/<V>/manifest.json --backup-dir data/prosperity-backups`。
   移除前持 `finance_prosperity` 锁，重新核对云端行与清单一致，并先做一次完整备份。
   然后在单个事务里删除该版本的行，随后执行 VACUUM，再删除该版本的候选和运行记录。
   当前版本和上一个 live 版本会被拒绝。
4. 云端 `data/prosperity-archive/<V>` 在本地归档确认保存后可另行清理，需 Boss 确认。

本地归档库与 M8 同 schema，可用 `ProsperityStore` 只读打开。
实测（21 季单版本，307 MB，M 系列本机）：export 168 s，verify 151 s，remove 156 s，
峰值 RSS 约 0.68 GB；移除后库为 0.1 MB，移除前备份 97 MB。

## 发布边界（M10 起）

- cron 入口 `run_prosperity.sh` 固定传 `--pool-path data/pool/prosperity_pool.json`。
  live 周更算完四张周榜后，发布挂点读 F1 周榜与 `market.db` 价格，生成池文件，
  全部挂点成功后才 `mark_published`。
- `--as-of` 与 `--rebuild-chain` 不调用挂点，不写池文件。
- 直接运行 `scripts/prosperity_weekly.py` 不传 `--pool-path` 时不发布（本地验收即如此）。
- 选池规则：F1 周榜第一档（排名 ≤ ceil(可排名数 / 5)）∩ STRICT/FULL ∩ 周榜成员内最近 30 个交易日平均日成交额前 100，
  合格的全进池（北极星 §4.1）。读取用 `src/data/prosperity_pool.load_prosperity_pool`，
  不可用原因四种：`prosperity_pool_missing` / `_criteria_mismatch` / `_stale` / `_invalid`。
  能读原榜的读取端另调 `board_mismatch(pool, stored)` 核对池文件自报的榜级计数与切点，
  再逐成员对照原榜核对排名和评级（`board_mismatch` 不查成员，北极星 §4.1）。
  2026-11-02 起规则含第一档；此前按旧规则生成的池文件读取时报 `_criteria_mismatch`（预期）。
- 周榜一旦标记已发布，同周再跑只返回 `already_published`，**不会检查或重写池文件**。
  发布后池文件被删或损坏，读取端会报 `prosperity_pool_missing` / `_invalid`（fail-closed，不会读到错数据）；
  重建方法：在云端用 `build_pool` + `publish_prosperity_pool` 对本周 F1 周榜手动重建，需 Boss 确认。
- 成交额排名在发布时读当时的 `market.db`，不随周榜冻结。同一天内重试结果相同；
  隔几天手动重建时，若期间 `as_of` 之前的价格被回补或修正，成员可能与当周略有不同。
- 网页与 Telegram（M11、M13）尚未接入。体检报告（M12）只在本地生成，见下节。

### 首次发布核对（2026-11-07，M10 A9 + 第一档规则）

main `1d35d702`（2026-11-02 push）第一次把发布挂点和第一档规则带到云端。云端此前停在 `ab572de`。全部只读：

1. 11-03 06:25 自动 pull 之后：`git log -1` 为 `1d35d702` 或更新；`code_version` 仍为 `13e5f9d5183f1b8e`；`bash -n scripts/run_prosperity.sh` 无输出。
2. 11-07 16:30 之后：`logs/cron_prosperity.log` 当次 OK；`prosperity_weekly.py` 打印的 JSON 里 `"detail": {"publication": "published"}`（`src/data/prosperity_run.py:332`）。
3. `load_prosperity_pool()` 返回 `available=True`；`criteria.tier == {'tiers': 5}`；`coverage.tier_cut == ceil(coverage.ranked.covered / 5)`；`selected` 约 20–35（10-31 回放为 36）。
4. `data/prosperity.db` 里 `publication` 表有 as_of 2026-11-07 的四张周榜（`SELECT COUNT(*) FROM publication p JOIN board b ON b.board_id=p.board_id WHERE b.as_of='2026-11-07' AND b.board_type='weekly'` = 4）。
5. 20:30 那次运行的 JSON 是 `"publication": "already_published"`（`prosperity_run.py:325`）。

任一项不符：不重跑、不手动发布，先记录现象再报 Boss。

## 体检报告（M12，本地）

每个方案一份固定格式 HTML（北极星 §4.3 八章 + 附录）和一份 `summary.json`。两个库只读，
只写 `--out` 目录。不进 `code_version`，不装 cron，不发 Telegram（云端生成与投递属 M13）。

```bash
PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
mkdir -p /tmp/health
(cd "/Users/owen/CC workspace/Finance" && ./sync_to_cloud.sh --pull)  # 先拉云端 market.db（本地只是副本）
"$PY" scripts/sync_prosperity.py --db /tmp/health/prosperity.db        # 云端结果库快照（约 170 MB 压缩包）
sqlite3 "/Users/owen/CC workspace/Finance/data/market.db" ".backup /tmp/health/market.db"   # WAL 安全副本，固定输入
"$PY" scripts/prosperity_health.py --db /tmp/health/prosperity.db \
  --market-db /tmp/health/market.db --out /tmp/health/out            # 默认 F1,F1-rank,F1-exfin,F0
```

| 退出码 | 含义 |
|---|---|
| 0 | 已写 `<方案>.html` 与 `summary.json` |
| 3 | 库文件不存在、某方案在该 `code_version` 下没有季末冻结榜，或 `market.db` 没有 SPY 价格（不写任何文件） |
| 4 | 方案名不在注册表，`--out` 是已存在的非目录，或输入库带非空 WAL（传 `sqlite3 .backup` 做的自包含快照） |
| 6 | 运行期间某个输入库被改写（运行前后 SHA256 不同），不写任何文件；对固定副本重跑 |

怎么读：
- 头部三条说明固定：不作上线门槛；价格收益不含分红；期数约 20、区间宽。先看头部的已满期数和收益覆盖率。
- 第 1 章五档超额与 Q1−Q5 的区间用 Newey-West，lag 按实际重叠期数算（真实数据 1/2/3 季 = 1/2/3）。
  `p_bh` 是跨方案 BH-FDR，检验族固定 24 项（4 方案 × 3 持有期 × 价差与 IC），没有 p 值的项按 1 计入并显示 UNKNOWN。
  `--schemes` 只决定输出哪几份 HTML，检验族始终包含全部 4 个方案，所以单出 F1 的 `p_bh` 与全量运行相同。
- 第 3 章对照组只用共同有效期（动量从 2022-03 起才有），p 值是描述性的。
- 附录 D 列出每条拆股记录的处理状态；`unresolved` 跨过的收益在附录 A 里记 `split_unresolved`。
- 独立核对与复权敏感性：`reports/prosperity/m12-health/verify_health.py`（不 import 项目代码）。

## 网页榜单（M11，本地）

一个纯静态 HTML：4 个方案的最新 live 周榜加全部季末冻结榜，所有行都在，约 10 MB。无脚本，用页首目录切换方案和期数。
库只读，只写 `--out`。不进 `code_version`，不装 cron，不发 Telegram（云端生成与投递属 M13）。

```bash
PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
mkdir -p /tmp/web
"$PY" scripts/sync_prosperity.py --db /tmp/web/prosperity.db              # 云端结果库快照，自包含无 WAL
scp aliyun:/root/workspace/Finance/data/pool/prosperity_pool.json /tmp/web/  # 池文件不随任何同步脚本到本地
"$PY" scripts/prosperity_web.py --db /tmp/web/prosperity.db \
  --out /tmp/web/prosperity-web.html --pool /tmp/web/prosperity_pool.json    # --pool 可省略
```

池文件和库快照要在同一次拉取：库里有了新一周的周榜而池文件还是上周的，页面会写"未标入池：池文件与本榜不符（as_of）"，不标入池。

| 退出码 | 含义 |
|---|---|
| 0 | 已写 HTML。`--pool` 读不到或不是合法 JSON 不算错误，F1 最新周榜榜头写原因 |
| 3 | 库文件不存在，或该 `code_version` 下没有任何榜 |
| 4 | `--out` 是目录或父目录不存在；`--out` 是库、库的 `-wal`/`-shm`/`.state` 或 `--pool` 本身（硬链接、大小写或 Unicode 写法不同也算，`--db` 是符号链接时按真实路径判断）；库带非空 WAL（传自包含快照） |
| 6 | 运行期间库被改写（读前读后 SHA256 不同），不写文件 |

怎么读：
- 只有 F1 最新周榜标入池（✓ 与成交额排名）。池文件必须和这张榜完全对得上：as_of、criteria、code_version、来源哈希、
  股票池、可排名数、第一档切点、合格数，以及每个成员的排名与评级。任一项不符就整张榜不标，榜头写"未标入池：原因"。
- 本地快照常常超过 8 天，所以网页不查池文件新鲜度；入池标记只说明池文件属于这张榜，榜头会写明这一点。
- 库里没有 F1 live 周榜时（例如重建冻结链后、第一个 live 周六前），页首写"未标入池：库中无 F1 live 周榜"。
- 整张榜都是近似 PIT（回放出来的季末榜）时，榜头写"本榜全部为近似 PIT"，行内不再逐行标。
- 独立核对：`/usr/bin/python3 -I reports/prosperity/m11-web-20261102/verify_web.py --db … --html … --code-version <页首的 code_version> [--pool …]`
  （只用标准库，不 import 项目代码；`--code-version` 必填，取页首显示的值）。
