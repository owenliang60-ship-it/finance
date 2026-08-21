# Issue 049: 同 CIK 名称冲突会连带封禁已知普通股主类

**Status**: 已修复（Extended Primary Universe Stop C denominator audit）
**Date**: 2026-08-21
**Severity**: HIGH — 26 个 issuer group 的普通股与债券/优先股一起被排除，当前主池漏掉 GS/T/SO/DUK/MSTR 等 25 个明确公司
**Related**: `src/data/security_master.py` · `config/share_class_overrides.json`

## 触发与根因

首次云端 Bootstrap 得到 raw 1003、membership 918，其中 53 个 current symbol 被标 `identity_conflict`。展开后发现多数为同 CIK 下的普通股 + preferred/debt/when-issued；resolver 在检查人工 override 之前先执行 company-name 一致性 gate，导致 override 对已审计组完全无效，普通股被 collateral block。

## 修复

- 有效 override（CIK→且 symbol 确实属于组）提升到名称冲突检查之前；无 override 时仍保持 fail-closed。
- 根据 Bootstrap 真实 payload/mcap/volume 审计，为 25 个有明确普通股主类的 group 添加 override。
- ATH group 当前只有 preferred/debt、无普通股，使用 `CIK: null` 的显式 block override；该语义对 singleton 同样生效。
- 测试锁定“override 可解已知名称差异”和生产 override 清单。

## 教训

人工 override 若排在自动 fail-closed gate 之后，就只是看起来存在；生产 denominator 首跑必须展开 reason 分布与具体 group，不能只看总数落在预期区间。
