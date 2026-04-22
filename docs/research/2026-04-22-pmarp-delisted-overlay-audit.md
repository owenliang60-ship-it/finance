# PMARP Delisted Overlay Audit

**Date**: 2026-04-22  
**Scope**: PMARP `cross_up_2.0` 日频 hardening 的扩展版退市/被收购 large-cap overlay  
**Status**: `partial overlay`，不是 full true survivorship

---

## 1. 为什么要做

此前 `extended` universe 只包含今天仍然活着的 `$10B+` 美股，`historical_market_cap` 只能做活名单内部的 PIT 过滤，**不能把已经退市/被收购的股票重新纳入样本**。这会造成 true survivorship bias。

---

## 2. 当前权限下的上游现实

2026-04-22 本地实测：

- `FMP /stable/delisted-companies?page=0&limit=100` → `200`
- `FMP /stable/delisted-companies?page=1&limit=100` → `402`
- `FMP /stable/historical-sp500-constituent` → `402`
- `FMP /stable/historical-market-capitalization?symbol=TWTR` → `200`
- `FMP /stable/historical-price-eod/full?symbol=TWTR` → `200`

结论：

1. **单股历史市值 + 日频价格回填链路可用**
2. **完整退市名单自动发现链路在当前 entitlement 下不可用**
3. 因此这次修复必须诚实标记为 `partial overlay`

---

## 3. 当前已纳入名单

当前 overlay 已纳入 21 只 delisted / acquired large-cap symbols。

### 3.1 高置信首批（手工审计）

| Symbol | 事件 | Delisted | 研究意义 |
|---|---|---:|---|
| `TWTR` | 私有化 | 2022-10-28 | 高流动性大票被收购，不应从样本里消失 |
| `VMW` | 被 Broadcom 收购 | 2023-11-22 | 大票并购退市案例 |
| `ATVI` | 被 Microsoft 收购 | 2023-10-20 | 大票并购退市案例 |
| `FRC` | 银行失败 | 2023-05-01 | 典型失败路径， survivorship 最容易漏掉 |
| `SBNY` | 银行失败 | 2023-03-13 | 典型失败路径 |
| `SIVB` | 银行失败 | 2023-06-16 | 典型失败路径 |

首批运行结果：

| Symbol | mcap rows | price rows | max market cap |
|---|---:|---:|---:|
| `ATVI` | 581 | 581 | $74.25B |
| `FRC` | 475 | 475 | $39.72B |
| `SBNY` | 1200 | 1200 | $22.57B |
| `SIVB` | 425 | 425 | $44.32B |
| `TWTR` | 335 | 335 | $57.21B |
| `VMW` | 606 | 606 | $76.99B |

全部通过 `$10B` 门槛，已进入 `delisted_large_caps` overlay。

### 3.2 第二批（provider last trade date 派生）

这一批目前用 FMP `historical-price-eod/full` 返回的 `last_price_date` 作为 `delisted_date` cap，**可用于 partial overlay 扩覆盖，但还不是逐只公告级审计完成版**。

| Symbol | Last trade date | 说明 |
|---|---:|---|
| `ALXN` | 2021-07-20 | AstraZeneca 并购 |
| `BKI` | 2023-09-01 | ICE 并购 |
| `CERN` | 2022-06-07 | Oracle 并购 |
| `COUP` | 2023-02-27 | Thoma Bravo 私有化 |
| `CTXS` | 2022-09-29 | Vista / Elliott 私有化 |
| `DRE` | 2022-09-30 | Prologis 并购 |
| `NLSN` | 2022-10-11 | 私有化 |
| `NUAN` | 2022-03-03 | Microsoft 并购 |
| `PXD` | 2024-05-02 | Exxon Mobil 并购 |
| `SGEN` | 2023-12-22 | Pfizer 并购 |
| `SPLK` | 2024-03-18 | Cisco 并购 |
| `WORK` | 2021-07-20 | Salesforce 并购 |
| `XLNX` | 2022-02-11 | AMD 并购 |
| `ZEN` | 2022-11-21 | 私有化 |
| `ZNGA` | 2022-05-20 | Take-Two 并购 |

这 15 只也全部通过 `$10B` 门槛，并已进入 `delisted_large_caps` overlay。

---

## 4. 资产位置

- 审计候选表：`/Users/owen/CC workspace/Finance/data/pool/delisted_large_cap_candidates.json`
- 可执行 overlay：`/Users/owen/CC workspace/Finance/data/pool/delisted_large_caps.json`
- 回填脚本：`scripts/backfill_delisted_large_caps.py`

---

## 5. 还没解决的部分

这仍然不是 full true survivorship，原因很直接：

- 退市名单没有完整分页能力
- 没有完整 historical constituent feed
- 当前虽然已经扩到 21 只，但 discovery 仍不是完整历史全集
- 第二批 15 只的 cap date 来自 provider last trade date，而不是逐只官方公告核验

所以当前正确说法是：

> PMARP 日频 hardening 已从“纯活名单”升级到“active + audited delisted large-cap partial overlay”，true survivorship **显著缩小**，但**没有彻底关闭**。
