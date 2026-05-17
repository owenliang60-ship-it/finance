---
issue_id: 028
title: Concept rule 分类器忠实传播 FMP 错误行业标签 — refresh+reclassify 可能降级 mistagged 公司分类质量
date: 2026-05-17
severity: low
domain: concept-registry
status: documented
---

## 现象

为 7 个缺-FMP-profile 的真美股（`POET OKLO MXL TTD AAL SOFI IREN`）跑
`--refresh-profiles` 补 profile 后 `--symbols-only` 重分类，本意是用真实
profile 替换"空-profile 时 LLM 仅凭 ticker 硬猜"的不可靠分类。

结果 5 票改善或持平，但 2 票**重分类后比硬猜更差**：

| 票 | 重分类前（空-profile LLM 硬猜） | 重分类后（rule） | 真实业务 |
|----|------|------|------|
| OKLO | 能源与材料/新能源 + 核能复兴;能源转型 | 地产与公用/电力与公用 | 核能 SMR 公司 |
| IREN | AI算力与云/数据中心运营 + 比特币暴露 | 金融/投资银行与交易 | 比特币矿企/AI 数据中心 |

## 根因

issue-025 重建后，`rule` 分类器对 FMP 的 `(sector, industry)` 做精确查表
（`industry_map` 82 对）。映射本身正确，但**输入的 FMP 行业标签本身错了**：

- OKLO 被 FMP 标成 `Utilities / Regulated Electric` → 规则正确映射成
  "地产与公用/电力与公用"。但 OKLO 是核裂变 SMR 成长公司，不是受监管电力公用事业。
- IREN 被 FMP 标成 `Financial Services / Financial - Capital Markets` → 规则
  映射成"金融/投资银行与交易"。IREN 实为比特币矿企。

垃圾进垃圾出。讽刺的是这 2 票空-profile 时 LLM 凭公司名硬猜反而猜对了——因为
LLM 用的是语义先验，rule 用的是 FMP 结构化标签。

## 影响

- 仅影响 FMP 行业标签错误的少数公司（多为新上市/SPAC/业务跨界的票）。
- rule 命中即短路、不再调 LLM（设计如此，为省 LLM 成本）→ 错误标签不会被
  下游 LLM 纠正。
- 本次 OKLO/IREN 经 Boss 裁定保留重分类前的旧值，未污染注册表。

## 缓解（本次）

Boss 决定：OKLO/IREN 保留重分类前分类，merge 时只替换 POET/MXL/TTD/AAL/SOFI 5 行。

## 教训

- **rule 分类器的质量上限 = FMP 行业标签的质量**。对 FMP 已知会错标的公司
  （核能/加密/业务跨界的新票），rule 命中不等于分类正确。
- 重分类（refresh+reclassify）不是单调改善操作——对 FMP-mistagged 的票可能
  从"LLM 语义猜测正确"退化到"rule 结构化映射错误"。批量重分类后应人工抽查
  rule 命中的票，而非默认信任。
- 可选的防御性改进（未实施，待立项）：维护一份"FMP 行业标签不可信"的票
  名单，命中其中的票强制走 LLM 而非 rule；或对 rule 结果做一层"L1 与公司
  description 语义一致性"校验。
