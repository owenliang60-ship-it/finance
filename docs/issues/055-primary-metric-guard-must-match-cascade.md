# Issue 055: incumbent demotion guard 必须匹配实际 primary 决策指标

**Status**: 已修复（`codex/extended-stop-d-hardening`，待合并/部署）
**Date**: 2026-08-22
**Severity**: HIGH — incumbent 虽缺本次决定 winner 的 volume，仍会因拥有未被采用的 market cap 而被错误降级
**Related**: `src/data/security_master.py` · `src/data/entrant_bootstrap.py`

## 触发与根因

primary cascade 已改为 volume 优先、market cap fallback，但 entrant guard 仍用“任意一个
volume 或 market cap 存在”判断 incumbent 是否有证据。构造 incumbent `{mktCap:50B,
volAvg:null}` + entrant `{mktCap:1B, volAvg:1000}` 时，唯一 volume 直接选择 entrant；
guard 却因为 incumbent 有 market cap 而允许降级，虽然 market cap 根本没有参与裁决。

## 修复

- primary selector 同时返回 winner 与 `deciding_metric`，entrant guard 复用同一纯函数，不再复制 cascade 语义；
- volume 决胜时要求被降级 incumbent 也有 volume；fallback 到 market cap 时才检查 market cap；
- valid manual override 属于独立人工证据，不被 metric guard 否决；
- 单边 volume 的真实失败场景加入回归测试。

## 教训

“有数据”不是一个全局布尔值。多级 cascade 的安全 guard 必须针对实际走到的那一级判断
证据完备性，否则未参与决策的字段会伪装成裁决证据。
