# Issue 051: scripts/ 入口未注入项目根目录导致直接执行 import 失败

**Status**: 已修复（Extended Primary Universe Stop C）
**Date**: 2026-08-21
**Severity**: MEDIUM — migration 预览可 import，正式 `python scripts/...` 却在写入前崩溃
**Related**: `scripts/migrate_core_watchlist.py`

## 根因与修复

测试通过 package import 加载模块，掩盖了 Python 直接执行脚本时 `sys.path[0]` 只指向 `scripts/`。入口现按项目惯例把 repo root 插入 `sys.path`；新增从 repo 外 cwd 经 `runpy` 调用迁移函数的回归。
