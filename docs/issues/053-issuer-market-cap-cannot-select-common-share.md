# Issue 053: 发行人级 market cap 不能用于选择普通股主类

**Status**: 已修复（`codex/extended-stop-d-hardening`，待合并/部署）
**Date**: 2026-08-21
**Severity**: HIGH — active Extended 分母混入优先股/债务证券，并把普通股降为 secondary
**Related**: `src/data/security_master.py` · `src/data/market_store.py`

## 触发与根因

Stop D 前的独立 denominator review 在 943 个 active eligible 中发现：

- `FITB-PM` 被选为 Fifth Third 主类，而普通股 `FITB` 被降为 secondary；
- `MER-PK`（Bank of America 6.45% Notes）和 `SATA`（永久优先股）以 singleton `ok` 入池。

FMP 把发行人级 market cap 复制到同一发行人的 preferred/depositary listing。`FITB-PM`
因此显示 51.4B，高于 `FITB` 的 49.4B；旧算法按 market cap 优先，正好选反。单票分类又只排除 ETF/Fund，明确的债务/优先股 title 没有 gate。

## 修复

- 对明确的 `-P*` preferred ticker、PFD/preferred、notes/debentures、固定票息 title，新增 `non_common_instrument` fail-closed reason；ADR Depositary Shares 与 MLP Common Units 不做泛化误杀。
- share-class primary 改为 security-level `averageVolume/volAvg` 优先，market cap 仅在成交量缺失/打平时兜底。
- `non_common_instrument` 加入 SM 白名单与 historical as-of identity blocked 集。
- 真实 FITB/MER-PK/SATA payload 形状进入回归测试。

## Rollout 补救（Stop D 前强制执行）

生产 Stop C 是在旧代码 `bb5da49` 上跑的，库内已经固化了错误 primary：
BAC/MS/KEY/KIM/MAA/WBS/REXR/FITB 被自家 preferred 降成 secondary，另有多组
双类普通股仍沿用 market-cap-first 的旧裁决。周频 `bootstrap_entrants` 只处理新
entrant 触及的 CIK，不会全局重新 classify 既有 SM；因此部署代码并不会自动修库。

hardening merge/push/deploy 后，必须在 Stop D canary 前：

1. 经 `market_db_writer` lock 运行一次完整 `python3 scripts/bootstrap_security_master.py`，不得带 `--current-only` / `--limit`；
2. 验证已知普通股恢复 `eligible=1, reason=ok`，preferred/notes 全部非 eligible；
3. 重建 post-hardening pre-backfill backup 并做 `quick_check`（旧 2026-08-21 backup 不能作为新基线）；
4. 将纠正后的 `market.db` pull 回本地对拍。

完整可复制命令与 assert 清单见 implementation plan 的 **Stop C.5**。

## 已知边界

窄正则不会猜测 `HBANZ/PPLC/AQNB/APOS/FITBI/FITBM` 这类仅靠 vendor ticker
约定表达的非普通股；它们没有明确 title 时仍由同 CIK 的普通股成交量压成 secondary。
这是有意的低误杀取舍：不能用“五字符 ticker”泛化封禁合法普通股。完整 bootstrap
保证同组比较，后续 entrant guard 则禁止缺少实际决策指标的 incumbent 被盲降级。

## 教训

第三方 profile 中“看起来属于证券”的 market cap 可能实际是发行人级字段；证券身份必须先用 security-level 证据判型，再比较 share class，不能让 issuer metric 替代 instrument identity。
