# 因子研究Codex1.0 — 自主因子挖掘专项计划

## 1. 目标定义

自主因子挖掘能力不是“自动发明无限新因子”，而是：

1. 在受控的因子语法空间内自动生成候选。
2. 自动完成防泄漏检查、统计评估、回测验证与去重筛选。
3. 将有效因子沉淀为可复用资产（可解释、可复现、可比较）。

---

## 2. 能力分级（Codex1.0建议）

### L0 手工模式

- 人工定义因子 + 人工跑评估。

### L1 半自动模式

- 人工定义原子特征，系统自动参数扫描和评估。

### L2 受控自主模式（Codex1.0目标）

- 系统自动组合原子特征生成候选。
- 系统自动评估与筛选。
- 人工只做白名单审批。

### L3 强自主模式（Codex2.0以后）

- 自动提出新操作符和搜索策略。
- 自动调整搜索预算与策略模板。

---

## 3. 自主挖掘总体架构

```text
Universe + Price/Volume + Calendar
  -> Atomic Features (return, vol, range, volume, trend)
  -> Factor Grammar (operators + windows + transforms)
  -> Candidate Generator (random/grid/evolutionary search)
  -> Leakage & Quality Gate
  -> Fast Evaluation (IC, quantile spread, stability)
  -> Slow Evaluation (event-driven backtest + benchmark excess)
  -> Novelty Filter (correlation clustering)
  -> Registry (approved / watchlist / rejected)
```

---

## 4. 因子语法（Factor DSL）

定义受控 DSL，防止“无限制表达式爆炸”。

### 4.1 原子特征（Atoms）

- `ret_n(close, n)`
- `vol_n(close, n)`
- `rsi(close, n)`
- `macd(close, fast, slow, signal)`
- `atr(high, low, close, n)`
- `zscore(x, n)`
- `rank_cs(x)`（横截面排名）

### 4.2 操作符（Operators）

- 单目: `lag`, `delta`, `abs`, `clip`, `normalize`
- 双目: `add`, `sub`, `mul`, `div`
- 组合: `ema(x,n)`, `sma(x,n)`, `ts_rank(x,n)`

### 4.3 约束（Hard Constraints）

1. 最大表达式深度: 4
2. 最大窗口: 252
3. 最小样本: 3 年
4. 任何节点不得访问未来时间

---

## 5. 候选因子生成策略

## 5.1 生成器组合

1. Grid 生成: 低维参数全覆盖，做基线。
2. Random 生成: 扩展搜索空间，增加新颖性。
3. Evolutionary 生成: 保留高分候选并做变异。

## 5.2 搜索预算

- 每轮最多生成 2,000 个候选（防计算爆炸）。
- 快评后保留 Top 10% 进入慢评。
- 慢评后仅 Top 2% 入“观察池”。

---

## 6. 双层评估与筛选

## 6.1 Fast Lane（快速统计层）

- Mean IC
- ICIR
- Quantile Spread
- 分年度稳定性
- 因子覆盖率

淘汰规则示例：

1. `abs(mean_ic) < 0.01` 且 `abs(icir) < 0.2`
2. 样本覆盖率 < 60%
3. 单年度崩塌（显著反向且不可解释）

## 6.2 Slow Lane（交易可行层）

- 统一策略模板（Top-K + 固定成本）
- OOS Walk-forward
- 相对 SPY/QQQ/VOO 的超额收益与 IR

淘汰规则示例：

1. OOS IR <= 0
2. 超额收益为负且无分市场优势
3. 成本敏感性过高（滑点上调后崩塌）

---

## 7. 新颖性控制与去重

避免重复挖掘同质因子。

1. 计算候选与已入库因子的相关性矩阵。
2. 相关性 > 0.85 视为同簇。
3. 同簇仅保留评分最高且稳定性更高者。
4. 输出“等价因子簇”报告，避免因子堆叠幻觉。

---

## 8. 防未来函数专项机制

1. 因子计算仅允许 `slice_to_date(t)` 数据。
2. 因子时间戳与交易时间戳强制错位（t 生成，t+1 执行）。
3. 所有 forward return 只进入评估模块。
4. 所有候选自动跑 leakage 单测，失败直接拒绝入池。
5. 对 “极端高分” 候选做人工审计（反作弊层）。

---

## 9. 自主挖掘数据模型

建议维护三张核心表：

1. `factor_candidates`
   - `candidate_id`
   - `dsl_expr`
   - `params_json`
   - `created_at`
   - `status`（new / fast_pass / slow_pass / rejected）
2. `factor_metrics`
   - `candidate_id`
   - `window`
   - `mean_ic`
   - `icir`
   - `spread`
   - `oos_ir`
   - `excess_return_vs_spy`
3. `factor_clusters`
   - `cluster_id`
   - `representative_candidate_id`
   - `member_ids`
   - `intra_corr`

---

## 10. 编排流程（Orchestrator）

每日/每周批处理：

1. 读取搜索预算与Universe。
2. 生成候选DSL表达式。
3. 跑 Fast Lane，淘汰弱候选。
4. 对通过者跑 Slow Lane 回测。
5. 运行去重聚类与最终打分。
6. 更新候选池并输出榜单报告。

---

## 11. 实施里程碑（6周专项）

## Week 1

- 落地 DSL 与语法校验器
- 落地原子特征与操作符白名单

## Week 2

- 完成候选生成器（grid + random）
- 完成 Fast Lane 指标计算

## Week 3

- 接入慢评回测通道（统一策略模板）
- 接入 SPY/QQQ/VOO 超额指标

## Week 4

- 完成 novelty filter 和聚类去重
- 打通候选池状态机

## Week 5

- 完成编排器（批量自动运行）
- 输出第一版自主挖掘报告

## Week 6

- 完成防未来函数专项测试
- 固化验收标准与上线流程

---

## 12. 验收标准（自主能力）

1. 单轮可自动生成 >= 500 个合法候选因子。
2. Fast/Slow 双层评估可自动跑通并产出榜单。
3. 所有入池因子均有可复现记录和可解释 DSL。
4. 防未来函数测试通过率 100%。
5. 通过因子对 SPY/QQQ/VOO 至少一个基准在 OOS 有稳定超额特征。

---

## 13. 风险与对策

1. 风险: 搜索空间爆炸。  
   对策: 限制语法深度、预算和窗口上限。

2. 风险: 数据挖掘偏差。  
   对策: Purged Walk-forward + 多窗口一致性筛选。

3. 风险: 因子同质化严重。  
   对策: 强制相关性聚类去重。

4. 风险: 过度依赖单一市场阶段。  
   对策: 按年份和市场 regime 分段评估。

