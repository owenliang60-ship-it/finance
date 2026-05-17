---
issue_id: 025
title: Concept v2 rule 分类器对描述全文做关键词子串匹配 — 财团/公用事业大面积误命中，347 个 rule 行至少 30 个错分
date: 2026-05-15
severity: high
domain: concept-registry
status: documented
---

## 现象

Task 13(concept taxonomy v2 全量打标）Step 3 全量 533 dry-run 产出
`reports/concept_registry/extended_pool_tags_2026-05-15.csv` 后，Step 4 人工审改
发现 `prefill_source=rule` 的行大面积错分。用 FMP `sector`/`industry` 与分类结果
`l1` 交叉核对，**347 个 rule 行里至少 30 个 sector/l1 明显冲突**：

| 症状 | 例子 | 错分结果 |
|------|------|----------|
| 财团描述碰撞 | BRK-A / BRK-B（伯克希尔） | → 消费与零售 > 餐饮 |
| 公用事业描述碰撞 | SO / SOMN（南方电力） | → AI算力与云 > 数据中心网络设备 |
| 保险描述碰撞 | ALL（全州保险） | → 消费与零售 > 消费电子与品牌硬件 |
| 保险描述碰撞 | TRV（旅行者保险） | → 工业与航天 > 物流与航运 |
| 券商描述碰撞 | IBKR（盈透证券） | → 能源与材料 > 矿业与金属 |
| 电力公用→新能源 系统性错映 | DUK / D / XEL / PEG / ETR 等 ~20 个 | → 能源与材料 > 新能源（应为 地产与公用 > 电力与公用）|

30 是「跨 sector 可检出」的下界。**同 sector 内的误命中（如某半导体票被另一半导体桶
误收）这种交叉核对查不出来，真实错误率更高。**

## 根因

rule 匹配逻辑在 `terminal/company_concepts.py:122`：

```python
text = self._text(item)          # _text(): description + sector + industry 拼成一段
for rule in self._keyword_rules:
    hit = next((kw for kw in rule["keywords"] if kw in text), None)  # 子串匹配
    if hit:
        return ... l1=rule["l1"], l2=rule["l2"], source="rule", confidence=0.6
```

两个叠加缺陷：

1. **匹配范围含 `description`**（`_text()` 在 company_concepts.py:179 把 description
   也拼进来）。公司描述是自由文本，财团/业务宽泛的公司描述里会偶然出现无关行业的词
   —— 伯克希尔描述提到 Dairy Queen 餐厅 → 命中「餐饮」关键词；南方电力描述含 network
   → 命中「数据中心网络设备」。
2. **首个命中即返回**（`keyword_rules` 列表顺序敏感，贪婪）。一旦某个宽泛关键词先
   命中，后面更准确的规则没有机会。

`keyword_rules` 表本身还有一处系统性错映：受监管电力公用事业的关键词指向
`能源与材料 > 新能源` 而非 `地产与公用 > 电力与公用`（NEE 这类纯新能源发电商可辩，
但 DUK/D/SO 等以化石/核电为主的受监管电力公司明确错）。

## 影响

- plan 的设计前提「rule 命中不走 LLM、直接采信」被打破 —— rule 段产出 ~9%+ 硬错
  且含不可见的同 sector 内误命中。
- **Task 13 不能推进到 Step 5 save**：`extended_pool_tags_2026-05-15.csv` 虽然
  `--validate-only` 结构校验通过（545 行 0 拒绝），但校验只查覆盖率/结构，不查语义
  对错，错分行会直接写进 `market.db` 的 `company_concept_tags`。
- v2 三段式 concept 测试（含 commit `20f8f33` 的 3-stock 集成测试）样本太少，
  mock 数据也不触发真实描述碰撞，因此单测全绿但全量跑才暴露。

## 修复路径（候选，未实施 — 下个 session 决策）

| 方案 | 做法 | 代价 |
|------|------|------|
| A 修匹配范围（倾向）| 改 `_text()` 只匹配 FMP `sector`/`industry`（短受控词表），不匹配 description | 代码改动小；可能更多票落 unclassified→LLM，LLM 成本略增；改完需重跑 |
| B 弃用 rule，全量 LLM | 347 个 rule 票全改走 LLM prefill | +~$70/次，每次刷新都贵且易撞限流 |
| C 只 LLM 重分检出的误命中 | 把检出的 30 个冲突行单独走 LLM(~$6) | 最快最便宜；同 sector 内隐藏误命中修不到，根因仍在 |
| D 手改 CSV | Boss 在 CSV 里手工改 30 误命中 + 7 电信 | 本次能 save；根因不修，下次刷新复发 |

并行的 taxonomy 缺桶问题：电信运营商（VZ/T/TMUS/VOD/VIV/CHT）在 v2 taxonomy
里没有合适 L2 桶，被 LLM 段塞进「通信基建REIT」/「电力与公用」（conf 0.45-0.55）。
Boss 决定与 rule 修复一起 re-plan 决定。

## 教训

- 关键词子串匹配分类器，**匹配范围一旦包含自由文本描述，财团/宽泛业务公司必误命中**。
  关键词分类应锚定短受控字段（sector/industry/标准行业码），不要打全文。
- 「首个命中即返回」的规则链对规则顺序敏感，宽泛关键词会饿死精确规则。
- 结构校验（行数/覆盖率/FK）通过 ≠ 语义正确。打标类产物必须有一道语义抽检
  （如 sector↔l1 一致性 gate），否则错数据会静默入库。
- 小样本集成测试 + mock 数据不能替代首次全量真实跑 —— 数据质量类 bug 只在全量
  真实 profile 上才暴露。
