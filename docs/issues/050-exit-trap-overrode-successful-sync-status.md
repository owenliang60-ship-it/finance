# Issue 050: EXIT trap 的 false test 覆盖成功同步状态

**Status**: 已修复（Extended Primary Universe Stop C）
**Date**: 2026-08-21
**Severity**: MEDIUM — pull 全部完成且健康检查通过，脚本仍 exit 1，自动化误报失败
**Related**: `sync_to_cloud.sh`

## 根因与修复

`cleanup()` 最后一条是 `[ -n "$_CC_TMP" ] && rm ...`；正常路径已把 `_CC_TMP` 清空，因此 test 返回 1，而 Bash `EXIT` trap 的函数返回值覆盖了脚本原始成功状态。修复为入口捕获 `$?`，best-effort 释放锁/临时目录，最后显式 `return "$rc"`。

## 教训

EXIT trap 必须保存并恢复原始退出码；cleanup 自己的“无需处理”不是任务失败。
