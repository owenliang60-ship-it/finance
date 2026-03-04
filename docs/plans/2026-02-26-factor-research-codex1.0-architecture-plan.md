# 因子研究Codex1.0 — 系统架构与实施计划

## 1. 项目定位

**项目名称**: 因子研究Codex1.0  
**目标**: 构建一个面向美股的因子挖掘与回测系统，支持因子发现、因子评估、策略回测、参数优化和统一比较。  
**核心要求**:

1. 因子挖掘: 支持 RSI、MACD 等技术指标因子及其参数变体。
2. 标准回测: 因子可无缝接入统一回测框架，支持频率切换和自动调优。
3. 严格防未来函数: 从数据层到回测执行层全链路约束 look-ahead bias。
4. 基准对比: 对 SPY / QQQ / VOO 计算超额收益和相对表现。
5. 统一评估: 所有因子用同一套指标和评分体系比较。
6. 市场范围: 仅美股，不支持数字货币。

---

## 2. 范围与非目标

### 2.1 In Scope

- 美股日频因子研究与组合回测
- 技术指标因子库与参数扫描
- Walk-forward 优化
- 因子有效性报告 + 策略绩效报告
- 可复现实验管理

### 2.2 Out of Scope (Codex1.0)

- 数字货币市场
- 分钟级/秒级高频策略
- 真实下单交易与券商对接
- 复杂机器学习 alpha 模型训练平台

---

## 3. 架构原则

1. **时间语义优先**: 所有数据都必须有可见时间 `as_of_time`。
2. **评估与交易分离**: 因子统计有效性与交易可执行性分层实现。
3. **同口径对比**: 因子/策略比较必须统一窗口、统一成本、统一基准。
4. **可复现**: 每次实验固化代码版本、数据快照、参数、结果。
5. **可插拔**: 因子、成本模型、组合构建器、优化器可替换。

---

## 4. 高层架构

```text
Data Layer (US Stocks + Benchmarks + Calendar)
  -> Factor Lab (RSI/MACD/Vol/Volume/Momentum)
  -> Autonomous Miner (Hypothesis -> Generate -> Validate -> Rank)
  -> Leakage Guard (时间戳约束 + 执行延迟 + Purged Split)
  -> Factor Evaluation (IC / Quantile / Stability)
  -> Strategy Builder (Top-K / Weighting / Constraints)
  -> Backtest Engine (Event-driven, costs/slippage)
  -> Benchmark & Attribution (SPY/QQQ/VOO)
  -> Optimizer (Sweep + Optuna + Walk-forward)
  -> Reports & Scoreboard
```

---

## 5. 代码结构建议

```text
Finance/
  factor_research_codex1/
    config/
      default.yaml
      universe.yaml
    data/
      calendar.py
      loader.py
      point_in_time_store.py
      benchmark.py
    factors/
      base.py
      technical/
        rsi.py
        macd.py
        sma_cross.py
        volatility.py
      registry.py
      transforms.py
    miner/
      hypothesis.py
      feature_grammar.py
      generator.py
      novelty_filter.py
      validator.py
      orchestrator.py
    evaluation/
      forward_returns.py
      ic.py
      quantile.py
      turnover.py
      scoreboard.py
    portfolio/
      signal_to_weight.py
      constraints.py
    backtest/
      engine.py
      broker_model.py
      slippage.py
      performance.py
      attribution.py
    optimize/
      sweep.py
      walk_forward.py
      optuna_search.py
    pipeline/
      run_factor_research.py
      run_backtest.py
      run_compare.py
    reports/
      factor_report.py
      strategy_report.py
    experiments/
      tracker.py
      schema.py
  tests/
    test_leakage_guard/
    test_factor_evaluation/
    test_backtest_engine/
    test_walk_forward/
```

---

## 6. 关键模块设计

## 6.1 Data Layer

- 美股交易日历: XNYS
- 价格数据: 调整后 OHLCV
- 基准: SPY / QQQ / VOO
- 股票池: 可配置（如 S&P500、NASDAQ100、自定义池）
- PIT 语义: 财务/事件型数据必须以可见时间入库

## 6.2 Factor Lab

- `FactorSpec` 统一接口:
  - `name`
  - `params`
  - `min_lookback`
  - `compute(data_slice, as_of_date) -> Series[symbol, score]`
- 技术因子首批:
  - RSI (多窗口)
  - MACD (快慢线/信号线多参数)
  - SMA/EMA 趋势因子
  - 波动率/ATR
  - 量价因子（RVOL、成交额动量）

## 6.3 Leakage Guard

- 规则 A: `as_of_date=t` 只能使用 `<=t` 可见数据
- 规则 B: 若因子由 `t` 收盘生成，最早在 `t+1` 执行
- 规则 C: 前向收益只用于评估，不用于信号生成
- 规则 D: Walk-forward 使用时间序列切分并设置 gap/embargo
- 规则 E: 每个因子至少一个 “故意植入未来信息” 的反例测试

## 6.4 Factor Evaluation

- 输出统一指标:
  - Mean IC
  - ICIR
  - IC Hit Rate
  - Quantile Return Spread (Q5-Q1)
  - 因子衰减曲线 (1/5/10/20 日)
  - Turnover / Rank Autocorrelation
- 可选统计检验:
  - t-stat / p-value
  - Bootstrap 稳健性

## 6.5 Backtest Engine

- 事件驱动日频回测
- 组合构建:
  - Top-K
  - Equal / Score-weighted
  - 约束（单票上限、行业上限、最小流动性）
- 成本模型:
  - 佣金
  - 滑点（bps）
  - 可扩展冲击成本
- 调仓频率:
  - D / W / M

## 6.6 Benchmark & Attribution

- 与 SPY / QQQ / VOO 同窗口对齐
- 统一输出:
  - Excess Return
  - Alpha / Beta
  - Information Ratio
  - Tracking Error
  - Up/Down Capture

## 6.7 Optimizer

- 参数网格扫描（快速基线）
- Optuna 贝叶斯优化（中后期）
- Walk-forward 滚动验证（训练/验证）
- 目标函数建议:
  - 主目标: OOS Information Ratio
  - 惩罚项: 换手、回撤、参数不稳定性

## 6.8 Autonomous Factor Mining（自主因子挖掘）

- 目标: 在可控搜索空间内自动提出候选因子、自动验证并输出可交易候选集。
- 核心闭环:
  - 假设生成（趋势/均值回复/波动压缩/量价背离）
  - 因子生成（DSL/语法树拼接）
  - 合法性检查（时间可见性、数据完整性、极值稳健性）
  - 统一评估（IC + 分位 + OOS 回测 + 基准超额）
  - 新颖性筛选（与已存在因子相关性去重）
  - 结果入库（候选池、淘汰池、白名单）
- 自主能力边界:
  - 允许“自动组合已有原子特征”
  - 不允许“绕过防未来函数规则”
  - 不允许“仅凭单一时间窗表现入选”
- 产出物:
  - 候选因子卡（定义、参数、有效区间、失效场景）
  - 因子排名榜单（统一评分）
  - 可复现实验记录（数据快照 + 代码版本 + 配置）

---

## 7. 统一因子评分体系（核心）

定义每个因子的总分 `FactorScore`：

`FactorScore = 0.45 * 信号有效性 + 0.20 * 可交易性 + 0.35 * 策略转化`

### 7.1 信号有效性 (45%)

- Mean IC
- ICIR
- Quantile Spread
- 稳定性（按年份/市场阶段分组后的一致性）

### 7.2 可交易性 (20%)

- 换手率
- 容量代理（相对 ADV）
- 成本敏感性（不同滑点下性能衰减）

### 7.3 策略转化 (35%)

- 对 SPY/QQQ/VOO 的超额收益
- OOS IR
- OOS Max Drawdown

---

## 8. 防未来函数测试清单

1. **切片完整性测试**: 任意时点 `t` 的输入数据最大日期不得超过 `t`。
2. **执行延迟测试**: `close[t]` 信号不得在 `t` 成交。
3. **错位鲁棒性测试**: 信号整体平移 1 天后结果应明显变化。
4. **伪因子检测测试**: 植入未来收益构造的伪因子应被 guard 拦截。
5. **基准对齐测试**: 策略收益与基准收益必须按日期内连接后计算相对指标。

---

## 9. 12周实施路线图

## Phase 0 (Week 1)

- 建立 `factor_research_codex1/` 目录
- 定义配置与实验结果 schema
- 接入交易日历与 benchmark 数据接口

## Phase 1 (Week 2-3)

- 完成 Factor 接口、注册表、RSI/MACD/SMA 因子
- 完成 forward return + IC + quantile 分析
- 输出首版因子报告

## Phase 2 (Week 4-6)

- 完成事件驱动回测引擎（仅美股）
- 接入成本/滑点/调仓频率
- 完成 benchmark 相对绩效模块

## Phase 3 (Week 7-9)

- 完成参数扫描 + Walk-forward
- 接入 Optuna 自动调优
- 产出因子排行榜与策略排行榜

## Phase 4 (Week 10-12)

- 完成防未来函数专项测试
- 完成性能优化与文档
- 发布 Codex1.0 可复现版本

---

## 10. 交付物定义

1. 可运行 CLI:
   - `run_factor_research`
   - `run_backtest`
   - `run_compare`
2. 标准报告:
   - 因子有效性报告（IC/分位/稳定性）
   - 策略报告（收益/风险/基准对比）
3. 统一榜单:
   - 因子总分排名
   - 参数稳定性评分
4. 质量保障:
   - 防未来函数测试套件
   - 实验可复现日志
5. 自主因子挖掘:
   - 自动候选生成流水线
   - 候选因子数据库（通过/淘汰/观察中）
   - 因子新颖性与重复度报告

---

## 11. 验收标准

1. 因子从定义到回测全链路可一键运行。
2. 所有报告包含相对 SPY/QQQ/VOO 的统一比较。
3. 防未来函数测试全部通过。
4. 不同因子可按统一评分体系排序，结果可复现。
5. 系统严格限定在美股市场，不包含数字货币逻辑。
6. 自主挖掘模块可以在限定语法空间内自动产出候选，并通过统一评估流程筛选。

---

## 12. 参考开源项目（实现借鉴）

- Alphalens: 因子分析框架
- Zipline-Reloaded: 事件驱动回测思路
- Empyrical: 绩效指标实现
- TA-Lib: 技术指标计算
- exchange_calendars: 美股交易日历
- Optuna: 参数优化
- Qlib: 大型研究平台架构参考

---

## 13. 自主因子挖掘专项架构

详见:
`docs/plans/2026-02-26-factor-research-codex1.0-autonomous-factor-mining-plan.md`
