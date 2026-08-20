# Issue 044: Identity retry batch 未对同 CIK incumbents 闭包会制造双主类

**Status**: 已修复（Extended Primary Universe Stop A self-review fix wave）
**Date**: 2026-08-20
**Severity**: HIGH — 同一 issuer 可同时出现两个 `eligible=1` ticker，污染 universe 分母
**Related**: `src/data/entrant_bootstrap.py` · `scripts/bootstrap_security_master.py` · `scripts/reconcile_fundamentals.py`

## 触发

SM 已有 `OLD/CIK1` eligible primary；`NEW/CIK1` 之前 profile 为空，TTL 到期后由 reconcile 单票重试。旧实现只对本次 queue 内的 `NEW` 做 share-class resolution，将它视为 singleton 并写成 eligible，最终 OLD/NEW 双主类。完整 bootstrap 重跑时一类 fetch 失败、另一类成功也会复现。

## 根因

证券身份的决策单位是完整 CIK group，不是一次 API batch。retry/rebootstrap 的输入天然可能是局部集合；把局部集合直接送进纯 `resolve_share_classes()`，等价于把“没出现在本批”误判成“不存在”。

## 修复

- 新增 `resolve_share_classes_with_incumbents()`：候选记录按 CIK 拉入现有 SM incumbents 与 profile，再统一结算。
- entrant bootstrap、reconcile identity queue、full bootstrap rerun 共用该闭集路径。
- 回归覆盖 reconcile 单票恢复和 bootstrap 部分 fetch 失败，两者均只保留一个 eligible primary。

## 教训

凡是 issuer/group 级约束，增量 batch 必须先对持久化 incumbents 做闭包；“本批数据完整”不是可默认的前提。
