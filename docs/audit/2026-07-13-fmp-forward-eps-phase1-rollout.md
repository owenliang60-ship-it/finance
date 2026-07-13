# FMP Forward EPS Phase 1 Rollout Audit

**Date**: 2026-07-13
**Plan**: `docs/plans/2026-07-11-fmp-forward-eps-data-layer.md`
**Scope**: Tasks 11–13（live contract probe、key rotation、cloud backfill、weekly smoke、cron cutover）
**Status**: Tasks 11–13 当日 rollout 已完成；等待 2026-07-18 自然周六验收，尚未标记 LIVE

## 结论

生产 backfill 与 full weekly smoke 的数据质量门均通过，4Q coverage 都是 `1,009/1,074 = 93.95%`；旧 yfinance 表的行数与确定性 hash 未变化，SQLite 完整性、主键唯一性和五篮子 holdings gate 均通过。云端 key rotation、DB 备份和 cron cutover 已完成。

唯一明确未达标项是原计划的 FMP `<30min` 时延目标：backfill 79.9min、weekly 79.5min。1,074 symbols × 3 个串行 endpoint × 1.5s 安全间隔决定理论下限约 80min，这属于计划估算错误，不是偶发性能回归。10:45 槽位没有后续 writer 冲突，并继续由 `market_db_writer` 资源锁保护。

Phase 1 仍不能标记 LIVE：必须等 2026-07-18 自然周六真实运行 `run_forward_data.sh`，证明 yfinance → FMP 顺序、双 verifier、日志与告警链路全部通过。本报告不会把同日 smoke 冒充自然周运行。

## Task 11 — Live Contract 与 holdings 配置审计

- upgraded key staged preflight：configured，且与旧 key 不同；全程只输出布尔状态。
- 5-symbol dry-run：`AAPL,MU,ONTO,GLW,NVMI`，5/5 quarterly 成功，77 estimate rows、40 earnings rows、0 unmatched，rc=0。
- 100-call probe：
  - 2.0s：100/100 成功、0×429，225.0s；
  - 1.5s：100/100 成功、100×HTTP 200、0 transport error、0×429，167.6s；
  - 选择 `FMP_FORWARD_API_CALL_INTERVAL=1.5`。
- 5 ETF live holdings 原始 837 行；配置修正前 813 included、3 foreign unmapped。
- issuer 核对并增加精确映射：`0EDE.L→NXPI`、`DSG.TO→DSGX`、`BB.TO→BB`；三只美国 ticker 均有 FMP quarterly estimates。
- 配置修正后 816 included、0 foreign unmapped；fail-closed 规则保持不变。

## Task 12 — Deploy、备份与 key rotation

- code：`origin/main` / cloud 均部署至 `3135159`。
- cloud Python 3.10 compile + FMP client tests：12 passed。
- production DB WAL-safe backup：
  - `/root/workspace/backups/finance/market.db.pre-fmp-forward.20260713_175052`
  - 856,899,584 bytes；`PRAGMA quick_check=ok`；旧 yfinance `forward_estimates=38,932` rows。
- key rotation 后 local/cloud `.env` 均为 mode 600；staging 字段删除；forward interval=1.5。
- rotation 后 legacy quote 与 5-symbol new endpoints dry-run 在 local/cloud 均通过；未写 DB。

## Task 13 — Production Data 与 cron

### Backfill

- command 通过 `finance_forward` + `market_db_writer` 双锁的 resilient `nohup` session 执行；非 Saturday，未与其他 writer 重叠。
- runtime：4,794s（79.9min），wrapper `OK`，rc=0。
- immutable manifest：1,074 targets，status=`complete`；quarter payload 非空 1,051、valid-empty 23、transport/processing failed 0、unprocessed 0。
- independent verifier：PASS，4Q coverage `1,009/1,074 = 93.95%`。
- estimates：1,051 symbols / 42,575 rows：Q 32,851 / FY 9,724；fiscal span 2021-01-02..2033-06-30。
- earnings final table：1,050 symbols / 83,805 rows；EPS actual 82,638 non-null / 1,167 null；fiscal matched 67,463 / unmatched 16,342。
- holdings：SPY 505/501、QQQ 108/101、SOX 33/30、IGV 111/107、XLF 80/77（raw/included）；foreign unmapped=0。
- duplicate PK groups：四张业务表全部 0；`fmp_basket_valuation` 仍为 0 rows（Phase 2 contract only）。
- legacy yfinance table：38,932 rows，sorted-row SHA256 与 backfill 前完全一致。
- `PRAGMA quick_check=ok`；log/launcher/writer processes 均正常退出。
- 非阻塞 warnings：8 条 blank/unrecognized holdings 审计行；Honeywell 两组同名碰撞；backfill 曾出现 BMNR `announce_date=1970-01-01` upstream sentinel。weekly replace 后该行已消失，但 recurrence 风险记录在 Issue 041。

### Full weekly smoke

- command 通过同一 `finance_forward` + `market_db_writer` 双锁执行；writer 4,766s、wrapper 4,768s（79.5min），rc=0，日志以 `OK` 收尾。
- immutable manifest：1,074 targets，status=`complete`；quarter payload 非空 1,045、valid-empty 23、quarter failed 6、unprocessed 0。
- 独立 weekly verifier：PASS，4Q coverage `1,009/1,074 = 93.95%`。
- weekly estimates：1,045 symbols / 17,592 rows：Q 12,984 / FY 4,608；fiscal span 2026-03-22..2033-06-30。
- six quarter failures：`BTI,COKE,L,PUK,RELX,YNDX`；annual unresolved：`STRF`；earnings unresolved：`HONAV`。均低于 gate，证据留在 manifest。
- 23 个 valid-empty symbols 与 backfill 完全一致，说明 empty evidence 稳定而不是随机掉数。
- final earnings table：1,050 symbols / 83,730 rows；EPS actual 82,638 non-null / 1,092 null；matched 67,449 / unmatched 16,281；最早日期恢复为 1985，BMNR epoch sentinel 已被本轮替换。
- holdings raw/included、四表 duplicate PK=0、`PRAGMA quick_check=ok`、旧 yfinance 38,932 rows + sorted-row SHA256 全部保持不变。

### 同日 backfill → weekly 的审计后果

Spec 明确规定 `snapshot_kind` 不进入 PK；同日 weekly 会覆盖 17,592 条重叠未来行并把它们重标为 `weekly`。最终 42,575 条 estimates = weekly 17,592 + historical backfill 24,983，没有数据丢失，但 backfill verifier 在 weekly 后会变成 `0/1,074`，因此同日 backfill 的可重复验证证据只能以 weekly 前保存的 verifier 输出为准。该限制记录在 Issue 042；本轮不擅自修改已冻结 schema 语义。

### Cron cutover

- 最近四次 `finance_fundamental` duration：1604s / 1655s / 1562s / 1571s，均 <35min；选择 Saturday 10:45。
- reviewed backup：`/root/workspace/cron_backups/crontab.pre-fmp-forward-cutover.20260713_180624`
- reviewed candidate：`/root/workspace/cron_backups/crontab.fmp-forward-candidate.20260713_180624`
- candidate 只改变 `finance_fundamental` resource-lock prefix 与 `finance_forward` schedule/command；其他行逐字节一致。
- weekly smoke 通过后从 reviewed candidate 安装；安装前确认 current crontab 与 backup 精确一致，candidate 非空且两条目标 job 各只有一条。
- 安装后立即回读并与 candidate 比对，随后第二次独立 SSH session 再比对，结果均为 `exact`。
- 生效 schedule：fundamental Saturday 10:00（持有 `market_db_writer`）→ forward Saturday 10:45（同锁、busy rc=75、运行 `run_forward_data.sh`）。
- 安装后发现关联注释仍写 10:15；另做一份精确 backup + 单行 candidate，把注释改为 10:45，安装回读仍为 `exact`（backup：`crontab.pre-forward-comment-fix.20260713_204602`）。

## 尚未满足的自然时间门

- 下一次自然周六（2026-07-18）需验证真实 `run_forward_data.sh` 顺序为 yfinance → FMP、两个 verifier 通过、日志无 key/URL 泄漏，并确认总 duration 约 95–105min 的新预算。
- Codex automation `fmp-forward` 已安排在当日 13:30 CST 自动执行只读验收；绿则在隔离 worktree 完成 LIVE 状态收尾，红则保留 pending 并报告 blocker。
- 一次 smoke + 一次自然周六是 Phase 1 LIVE 的最低条件；四周并行对拍完成前，yfinance 不下线。

## Rollback

- code：回退到 merge 前 cloud commit，并恢复旧 crontab backup。
- cron：`crontab /root/workspace/cron_backups/crontab.pre-fmp-forward-cutover.20260713_180624`，随后 `crontab -l` 比对。
- DB：只在明确批准恢复时使用精确的 WAL-safe backup 主文件；禁止用通配符猜主文件。
