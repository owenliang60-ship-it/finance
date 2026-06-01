# Issue 031: Concept Review-Manifest Schema vs Loader Mismatch

**Status**: A2.5 自身已绕过（重生成 canonical schema）；**durable 修复 deferred** — **loader fallback 已实现（A3 Task 1），retroactive 修好历史 manifest**（`scripts/build_company_concept_registry.py` `_load_review_manifest` 加 `data.get("symbols") or data.get("full_universe")`，方案 1）
**Date**: 2026-05-30（A2.5 落库时 Codex 审查发现；回溯发现 A2 5/24 manifest 同病）
**Severity**: P1 静默失效（coverage sidecar 形同虚设，但不污染数据 / fails-open）
**Related**: issue 030（coverage mismatch）· `scripts/build_company_concept_registry.py:836/856/1164` · A2.5 plan `docs/plans/2026-05-30-a2.5-ai-power-anchors.md`

## 触发
A2.5 落库前 Codex 审查 manifest sidecar，删除临时 CSV 一行（`03032`）后跑 `--validate-only`，**仍通过、966 rows parsed**——本应被 coverage 拦截。

## 根因
concept registry 有**两种 manifest schema 并存**，loader 只认其一：

| 写入方 | schema 关键字段 |
|--------|----------------|
| `_write_review_manifest()`（:836，build/reclassify dry-run 写） | **`symbols`** ✓ |
| A2/A2.5 relodge 用的专用脚本（issue 030 workaround 生成） | `full_universe`（无 `symbols`） |

而 `_load_review_manifest()`（:856）只读：
```python
syms = data.get("symbols") or []     # line 869
```
→ 拿 `full_universe`-schema 的 manifest 喂它 = **空集** → `_effective_extend_pool`（:1164）`base | {}` = base 单独，sidecar 零保护。

sidecar 的设计职责（5/14 Round 3）是兜住"被删的 watchlist/portfolio 行（不在 extended_universe 里）"——正是这类行因空集而漏网。

## 影响面
- A2（5/24 manifest）、A2.5（5/30 拷贝 A2）的 sidecar **从未真正保护过 coverage**。
- 两次落库都靠 `--extended-universe-path` 显式 pin 兜底（issue 030 workaround），所以数据没出错——但 sidecar 是死的，纯靠人记得加 pin。
- fails-open（漏审风险），不 fails-closed（不会污染已审数据）。

## A2.5 的处理（绕过，非根治）
重生成 05-30 manifest，补 canonical `symbols` key（967，从 05-30 CSV 派生）。验证：
- `_load_review_manifest()` 返回 967（原空）
- 删 `03032` → validate-only 报 `missing 1 symbols from extend pool: ['03032']`（原静默通过）

## Durable 修复（二选一，deferred 给 Boss/单独 review）
1. **改 loader（推荐，一行，retroactive 修好所有历史 manifest）**:
   ```python
   syms = data.get("symbols") or data.get("full_universe") or []
   ```
2. **统一写入口**：所有 manifest 生成走 `_write_review_manifest()`，废弃 `full_universe`-schema 脚本。

## 教训
1. **一个 schema、一个 writer、一个 loader**——并存两种 manifest schema 是定时炸弹。
2. coverage sidecar 是 fails-open 的安全网；安全网失效不会报错，必须**主动验证它真的拦得住**（删行测试），不能假设"文件存在 = 保护生效"。
3. 与 issue 030 叠加：手动落库的 coverage 既怕 drift-in（issue 030，靠 pin）又怕 sidecar 失效（本 issue）——A3 cron 化必须把这两层都做成自动可验证的。
