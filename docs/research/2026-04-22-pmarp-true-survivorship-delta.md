# PMARP True Survivorship Delta

**Date**: 2026-04-22  
**Question**: 补入第一批退市/被收购 large-cap overlay 之后，`PMARP cross_up 2%` 日频 hardening 会不会被推翻？  
**Answer**: 不会。当前 21 只 overlay 明显抬高了 Full / IS 样本量，但 `OOS 60d` 结论完全不变。

---

## 1. 运行口径

### Baseline

- Universe: `extended`
- Artifact: `backtest/new/pmarp_crossup_hardening_20260422/`

### Partial Survivorship Overlay (current)

- Universe: `extended_true`
- Overlay symbols: 21 names (`ATVI/FRC/SBNY/SIVB/TWTR/VMW` + 15 more large-cap delisted/acquired names)
- Artifact: `backtest/new/pmarp_crossup_hardening_true_survivorship_20260422_v3/`

---

## 2. 样本量变化

| Sample | Raw events old | Raw events new | Delta | Symbols old | Symbols new | Delta |
|---|---:|---:|---:|---:|---:|---:|
| Full | 9922 | 10025 | +103 | 521 | 539 | +18 |
| IS | 6953 | 7056 | +103 | 513 | 531 | +18 |
| OOS | 2969 | 2969 | +0 | 520 | 520 | +0 |

这说明 partial overlay 已经不只是“象征性补 6 只”，而是真的把一批 pre-2025 delisted / acquired large caps 拉回了样本。

---

## 3. 关键指标变化

### OOS（最重要）

| Horizon | Old mean | New mean | Delta | Old hit | New hit | Old p-FDR | New p-FDR |
|---|---:|---:|---:|---:|---:|---:|---:|
| 7d | +0.7889% | +0.7889% | +0.0000pp | 57.14% | 57.14% | 0.001668 | 0.001668 |
| 30d | +1.9896% | +1.9896% | +0.0000pp | 55.25% | 55.25% | 0.001640 | 0.001640 |
| 60d | +5.5870% | +5.5870% | +0.0000pp | 57.71% | 57.71% | 0.000493 | 0.000493 |

### Full（辅助观察）

| Horizon | Old mean | New mean | Delta | Old p-FDR | New p-FDR |
|---|---:|---:|---:|---:|---:|
| 7d | +0.2111% | +0.2422% | +0.0312pp | 0.076483 | 0.042903 |
| 30d | +0.6696% | +0.6585% | -0.0111pp | 0.018261 | 0.018227 |
| 60d | +2.7865% | +2.7958% | +0.0092pp | 0.000045 | 0.000037 |

---

## 4. 结论

### 4.1 这 21 只 partial overlay 没有推翻 PMARP

预注册主判据看的是 `OOS 60d`：

- Mean excess: `+5.5870% → +5.5870%`
- Hit rate: `57.71% → 57.71%`
- p-FDR: `0.000493 → 0.000493`

完全不变，原因也清楚：当前新增的 delisted / acquired symbols 全部发生在 2025-01-01 之前，所以它们只影响 Full / IS，不影响 OOS。

### 4.2 这次修复的真实含义

这次不是“彻底修好 true survivorship”，而是：

1. 把 21 个 large-cap delisted / acquired names 补回样本
2. 证明 PMARP 结果**不会被这一层 pre-2025 的 survivorship omission 轻易推翻**
3. 把研究口径从 `extended` 升级为 `extended_true (partial overlay)`

### 4.3 现在该怎么表述

最诚实的表述应该是：

> PMARP `cross_up 2%` 已通过日频 OOS hardening；在补入第一批 audited delisted large-cap overlay 后，结论保持稳定。当前 survivorship bias **已显著缩小，但未完全消除**。

更准确地说，现在应该更新为：

> PMARP `cross_up 2%` 已通过日频 OOS hardening；在补入 21 个 delisted/acquired large-cap partial overlay 后，Full / IS 样本明显扩大，而 OOS 主结论保持不变。当前 survivorship bias **已显著缩小，但未完全消除**。

---

## 5. 下一步

如果要继续往“彻底夯实”推进，下一步不是继续调 PMARP，而是继续扩 overlay coverage：

1. 增补更多 2021-07 以来的 large-cap delisted / acquired names
2. 若要宣称 full true survivorship，必须换到可完整枚举 delisted universe 的更强数据源
