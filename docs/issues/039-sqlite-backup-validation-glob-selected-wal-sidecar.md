# Issue 039: SQLite 备份验证用通配符会误选 WAL sidecar

**Status**: 已修正（FMP forward EPS cloud rollout）
**Date**: 2026-07-13
**Severity**: MEDIUM — 可能把有效备份误报为损坏，或更糟地验证了错误文件
**Related**: cloud `market.db` rollout / WAL-safe backup procedure

## 触发

生产 `market.db` 完成 `wal_checkpoint(TRUNCATE)` 并复制后，验证命令用备份路径加 `*` 再按时间选择文件。SQLite 只读打开时报 `not a database`。

## 根因

通配符同时匹配主数据库和 SQLite 自动产生的 `-shm` / `-wal` sidecar；按 mtime 或列表顺序取第一项并不能保证选到主文件。备份本身没有损坏，验证对象错了。

## 修正

- 从备份动作开始就保存精确、无通配符的主文件路径。
- `immutable=1`、row count、`PRAGMA quick_check` 和文件大小全部对同一个精确路径执行。
- 检查 sidecar 时只做存在性审计，绝不把它们纳入候选主文件。
- 本次精确主文件验证结果：`quick_check=ok`，旧 yfinance `forward_estimates` 为 38,932 行，文件大小 856,899,584 bytes。

## 教训

SQLite 是“一份主文件 + 可选 sidecar”的文件族，但恢复和验证对象必须始终是显式主文件。生产备份脚本禁止用 `db_path* | head -1` 之类的启发式选择。
