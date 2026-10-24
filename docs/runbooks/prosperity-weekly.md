# 景气 M9 周更运行与恢复

代码位于 M9 worktree，尚未部署。正式部署须先决定计划 D5 的长期容量策略。

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
| 0 | 计算成功；无发布挂点也返回 0 | 查运行记录的 `publication_not_configured`，不当作已投递 |
| 3 | 输入/基本面硬门控失败 | 修复输入证据后重试，不降低门槛 |
| 4 | 日期、模式或参数错误 | 更正参数；当前周只能 live |
| 5 | 已计算、发布失败 | 同周重试只发布，不重算 |
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

## 发布边界

当前发布挂点为空，M9 不生成池文件、网页或 Telegram 消息。
未安装 cron、未 merge/push。M10–M13 接入后仍遵循全部发布成功才 `mark_published`。
