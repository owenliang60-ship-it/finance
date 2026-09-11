# Issue 052: 对 live SQLite 主文件做 in-place rsync 可 exit 0 但保留旧内容

**Status**: 已修复（Extended Primary Universe Stop C）
**Date**: 2026-08-21
**Severity**: HIGH — company.db push 显示成功、大小和 mtime 更新，但 checksum 不同且新 watchlist 表缺失
**Related**: `sync_to_cloud.sh`

## 触发与根因

本地 company.db 已 checkpoint 且含 watchlist，旧脚本直接 rsync 到远端 live path。远端文件 mtime/大小更新、命令 exit 0，但内容 checksum 仍不同；SQLite/WAL 连接与 in-place inode 覆盖不是安全发布协议。

## 修复

上传唯一 temp path → SHA-256 + `quick_check` → 远端无打开连接 gate → 保留单份 `/tmp/company.db.pre-sync` → `mv` 原子替换 → 删除 sidecar → 再次 checksum + integrity 验证。生产事故现场已用同协议修复并验证 watchlist 15 行。
