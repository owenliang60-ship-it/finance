# 046: 云端 market.db 周备份无保留策略，磁盘 ~7 周内耗尽

> **日期**: 2026-08-18
> **严重度**: P1（独立于任何项目的生产险情，有明确倒计时）
> **状态**: 2026-09-12已完成一次性离机归档清理；自动保留策略仍待治理（下方原审计数字为历史记录）
> **发现方式**: Extended Primary Universe 项目数据基建审计（subagent 实测云端磁盘）

## 现象

云端 `/root/workspace/Finance/data/` 共 17GB，其中：

- `market.db`（live）: 0.93GB
- `market.db.backup-*` × **17 份**: **13.41GB**，自 2026-05-17 起每周堆积，单份 803MB→925MB 随库增长

云盘 `/dev/vda3` 总量 49G，已用 41G，**仅余 6.7G（86%）**。每周六新增 ~0.93GB 备份 → **即使什么都不做，约 7 周后磁盘满**，届时所有云端 cron（价格采集、晨报、PI 推送）连锁失败。

## 根因

`_backup_sqlite()`（`scripts/build_company_concept_registry.py:1406-1429`）每周六 09:00 由 concept weekly sync 调用（`:1121`），用了正确的 SQLite backup API，但**从不删除旧备份**。`data_guardian` 的 `MAX_SNAPSHOTS=10` 保留策略（`src/data/data_guardian.py:24`, `:226-235`）只管它自己的 `.backups/*.tar.gz`，管不到这些 `.backup-*` 文件。

## 修复建议（待批准）

1. **立即**：云端裁剪到最近 2-3 份 → 释放 ~12.5GB（删除为破坏性操作，需 Boss 批准）
2. **根治**：给 `_backup_sqlite()` 加保留策略，照抄 `data_guardian._enforce_retention()` 模式
3. **附带**：VACUUM market.db 需 ~1.9GB 空闲空间，清理前偏紧，清理后无虞

## 教训

- 任何自动备份逻辑**写入时就必须带保留策略**——"先跑起来再说"的备份会静默吃满磁盘
- 多套备份机制并存时（data_guardian tar.gz + concept registry .backup-*），每套都要有自己的 retention，不能假设别人的策略会覆盖自己
- 对应 L2 盲点清单"异常路径无收尾"的变体：正常路径（每周成功备份）同样需要收尾（裁剪旧份）

## 2026-09-12 20:02 更新

Boss批准普通周备份保留最近3份、本次上线备份不动，并要求全盘盘点。
现场实际是7份`pre-weekly-sync`，不是原记录17份。8/1、8/8、8/15、8/22四份先离机归档、逐文件SHA256核对，再在writer资源锁与集合/元数据/打开句柄检查后移除云端原件。

- 释放3,691,913,216字节（3.44GiB）；可用2.56→6.00GiB，df使用率95%→88%。
- 保留8/29、9/5、9/12三份周备份，其SQLite quick_check均通过；PE的final/native两份上线回滚备份哈希不变。
- 本地归档约968MiB，SHA256 `a6f2cbbc27fe5022eb34ab9d23e0ae432b4cb765b4aa5fce3c7b5be5f6517bca`。
- 本地/云端各11项保护测试通过，生产库只读检查仍为773条历史/60条PIT。未改生产代码、crontab或服务。
- 当前大类：Finance数据/备份11.08GiB、临时/PE试跑5.99、系统软件/状态6.20、下载/浏览器/npm缓存4.55、应用及共享依赖4.42、日志3.66、旧项目/迁移备份1.96、swap2.00。硬链接按inode去重。

报告与全部证据位于`.worktrees/index-pe-morning-chart/reports/rendered/cloud-storage-20260912/`，报告名`cloud-storage-report.md`。该worktree现在持有离机恢复归档，清理worktree前需先保存该资产。

本次没有实现自动保留。`_backup_sqlite()`仍无retention，新PE周频的`pre-soxx-historical-pe`也调用它，后续策略需覆盖两类；Guardian另有MAX_SNAPSHOTS=10，不能替代上述治理。日志、缓存、试跑库与其他修复备份均未删除，后续范围待选定。

附带复核：当前正式market.db freelist_count=0，无可回收空页；原8/18条目中的VACUUM建议不作为本轮空间措施，未执行VACUUM。

## 2026-09-13 A批冷资产清理完成

另行批准的A1 PE试跑、A2临时恢复库、A3旧安装包均已离机归档并实际恢复验收后移除云端原件，
合计再释放5.59GiB。9/13终验可用11.73GiB、75%；生产quick_check=ok、773/60条PE产品不变。
原三份普通周备份和两份PE回滚备份SHA复核一致；生产日志依赖旧.bak目录，始终保留（issue079）。
A1嵌套WAL按Boss专门批准的三件套原样归档+复制件只读合成一致性副本处理（issue080）。

稳定恢复档案：`reports/rendered/cloud-archives/2026-09-12/A2-A3/`及
`reports/rendered/cloud-archives/2026-09-13/A1/RESULT.md`。B旧data/迁移备份、C缓存、D日志未清理。
**自动retention仍未实现，本issue不因一次性腾出空间而关闭。** 未改生产代码、cron或服务。

## 2026-09-26 M0：一次性清理 + 自动保留上限上线

**清理（Boss 批准直接删除）**：删除 27 个文件共 13,507,305,620 字节（3 份旧周备份、9/19 的 PE 备份、4 份手动修复前快照、2 份扩池回填前快照、premium-repair 库副本、10 个 guardian tar.gz、7/13 的 FMP forward 快照）。删除前确认无进程占用。云端可用空间 1.7G→15G，`market.db` quick_check=ok。

**自动保留**：plan `docs/plans/2026-09-26-m0-backup-retention.md`，merge `18c1e0fa`，已 push，22:42 CST 部署到云端（`git pull --ff-only`，92d8619→18c1e0f）。
- `_backup_sqlite()`：空间门为逻辑大小（page_count×page_size，含 WAL）×1.5；`mkstemp` 独占临时文件 → quick_check → `os.link` 发布（不覆盖），权限 0644；失败统一抛 `RuntimeError`。
- 只有 `--scheduled` 的 cron 入口使用 `auto-weekly-sync`（留 2 份）和 `auto-index-pe-weekly`（留 2 份）两个标签；手动 `pre-*` 标签永远不修剪。修剪失败只记 error 日志；超过 24 小时的同标签孤儿临时文件在修剪时清理。
- 验证：本地 main 全量 tests 4236 passed / 1 个原有失败。云端临时克隆中本次相关测试全部通过（另有 10 个原有失败，合并前后名单一致）。云端 ext4 端到端演练通过：真实修剪、0644、硬链接数 1、手动标签不删、模拟空间不足时 PE 入口 rc=2 且不打开写库、周同步 fatal 且不写库。部署前后受保护文件 12 条（名单 / 大小 / mtime）一致。

**待验收（首个自然周六 2026-10-03）**：
- 每类 `auto-*` 备份：quick_check=ok，日志中空间门数字正确，修剪名单正确，份数 ≤2。
- 受保护的 `pre-*`、`.gz`、`.json` 文件不变；`df` 可用空间 ≥10G。
- 某类备份当周没触发就记"待验证"。两份 `auto-weekly-sync` 生成后，另请 Boss 决定是否手动删除遗留的 9/19、9/26 两份 `pre-weekly-sync`。
- **本 issue 在自然周六验收通过后关闭。**
