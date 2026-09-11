# Extended Primary Universe — Stop C Rollout Audit

**Date**: 2026-08-21  
**Final code**: `d5f22f2`（local main = origin/main = aliyun）  
**Scope**: backup cleanup、云端部署、security-master bootstrap、Core→watchlist migration、pre-backfill backup。未修改 crontab。

## 1. Storage preflight

- `market.db.backup-*`: 17 → 3，精确保留 2026-08-01 / 08-08 / 08-15。
- 删除旧备份 14 份，共 11,646,676,992 bytes；不可恢复。
- 云盘可用空间：6.6GB → 18GB；创建 pre-backfill snapshot 后 17GB。
- live DB 与保留备份均未删除。

## 2. Schema / bootstrap

- 7 张新表全部存在：`security_master`、`extended_membership`、`coverage_status`、`company_profile`、`fundamental_vintage`、`fundamental_backfill_runs/jobs`。
- 首轮 full bootstrap：union 1,385；发现 26 个 same-CIK 普通股+债/优先股 group 被名称冲突 collateral block。
- 修复 override precedence 并审计 25 个明确普通股主类；Athene CIK 使用 `null` block（当前无普通股）。
- 最终 current denominator：
  - raw screener: 1,003
  - eligible/active membership: **943**
  - secondary share class / non-primary instrument: 58
  - missing profile: 1 (`FITB-PA`)
  - audited no-common-equity block: 1 (`ATHS`)
  - `needs_review_primary`: 0
- `active membership == raw ∩ SM eligible`：SET EQUAL。

## 3. Core retirement migration

- 迁入 local-owned watchlist 11 只：`ABBNY AXTI CRSP DXYZ JOBY NTLA NVTS ONDS QS SES TEM`。
- SOXX 未进 watchlist，由 SOX basket 覆盖。
- watchlist 总数：15；local/cloud 一致。
- 发现并修复两项 sync 问题：EXIT trap 覆盖成功 rc（issue 050）；in-place rsync live SQLite 未发布新 schema（issue 052）。最终使用 checksum + quick_check + atomic replace，旧 cloud company.db 暂存 `/tmp/company.db.pre-watchlist-20260821`。

## 4. Final integrity / rollback evidence

| Check | Local | Cloud |
|---|---:|---:|
| active membership | 943 | 943 |
| security_master rows | 1,385 | 1,385 |
| watchlist rows | 15 | 15 |
| market.db quick_check | ok | ok |
| company.db quick_check | ok | ok |

- Pre-backfill SQLite backup API snapshot：`data/market.db.pre-backfill-20260821`（938,053,632 bytes，`quick_check=ok`，sidecars removed）。
- Cloud weekly backups retained: 3；automated retention root fix remains a separate follow-up before backups can grow unbounded again.

## 5. Stop C verdict

**PASS**。代码、denominator、双端数据库、watchlist migration 与 rollback snapshot 均验收通过。下一 Stop 为 **D canary 25**；需单独批准，全量 backfill 仍需 canary 报告后二次批准。
