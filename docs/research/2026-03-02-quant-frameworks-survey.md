# 开源量化框架调研：Qlib / FinRL / VectorBT / Lean

> 调研日期：2026-03-02
> 目的：评估主流开源量化框架，为自建因子挖掘+回测框架提供参考
> 结论：四个框架均面向更细粒度交易场景（tick/分钟级），不适合直接集成；但 Qlib 的因子 DSL、RD-Agent、PIT 设计有借鉴价值

---

## 一句话定位

| 框架 | 定位 | 开发者 | Stars |
|------|------|--------|-------|
| **Lean** | C# 工业级多资产回测+实盘引擎 | QuantConnect | 10K |
| **Qlib** | AI-first 量化投资研究平台，ML 是架构基础 | 微软亚洲研究院 | 38K |
| **FinRL** | 深度强化学习交易框架 | 哥伦比亚大学 AI4Finance | 14K |
| **VectorBT** | 向量化回测引擎，参数搜索之王 | Oleg Polakow (个人) | 6.8K |

---

## 核心能力矩阵

| 能力 | Lean | Qlib | FinRL | VectorBT |
|------|------|------|-------|----------|
| **ML/DL 模型** | 可集成 | 20+ SOTA 模型 | 5 个 DRL 算法 | 无 |
| **因子研究** | 无原生支持 | Expression Engine + Alpha158/360 | 技术指标作为状态 | IndicatorFactory |
| **参数优化** | Grid Search | 有限 | 超参搜索 | **极强**（千级并行） |
| **回测速度** | 快（C#） | 中等 | 慢（训练耗时） | **最快**（向量化） |
| **实盘交易** | **最强**（多券商） | 无 | Alpaca 纸盘 | 无 |
| **期权支持** | **完整** | 无 | 无 | 无 |
| **前视偏差防护** | 有 | **PIT 数据库**（最好） | 无专门机制 | 无专门机制 |
| **可解释性** | 高 | 因子 IC、特征重要性 | 低（黑箱 RL） | 高 |
| **文档质量** | 优秀 | 较好 | 中等 | 免费版不完整 |
| **活跃度** | 活跃 | 中等（转向 RD-Agent） | **低**（基本停更） | 免费版维护模式 |

---

## 各框架详情

### 1. QuantConnect Lean

- **语言**: C#（Python/F# 接口）
- **架构**: 事件驱动，逐 tick 模拟，多资产（股票/期权/期货/外汇/加密）
- **数据**: 自有数据库（tick 到月级，8000+ 资产），支持自定义数据
- **实盘**: IB, TD Ameritrade, Coinbase 等多券商
- **云端**: QuantConnect 云免费回测
- **License**: Apache 2.0
- **适合**: 想要工业级回测+实盘一体化的团队，多资产策略
- **不适合**: 纯因子研究（C# 生态不如 Python 灵活）
- **调研详情**: 之前单独调研过，见对话记录

### 2. Microsoft Qlib

- **架构**: 四层 — 数据层(自研二进制) → 学习层(ML pipeline) → 工作流层 → 接口层
- **核心创新**:
  - **Expression Engine**: 因子 DSL `Ref($close, 60) / $close`, `Corr($close, $volume, 20)` — 编译到高效二进制
  - **Alpha158/360**: 预定义 158/360 个因子集合，学术标准 benchmark
  - **Point-in-Time 数据库**: 基本面数据时间戳精确，防前视偏差
  - **Model Zoo**: LightGBM, XGBoost, LSTM, Transformer, GAT, HIST 等 20+ 模型
  - **RD-Agent** (2024-2025): **LLM 自动化因子挖掘 + 模型优化**，最大亮点
  - **嵌套决策框架**: 日级仓位 + 日内执行多粒度策略
- **数据**: Yahoo Finance 为主，官方数据源已挂（用社区替代），中国市场偏重
- **Benchmark** (LightGBM + Alpha158, 中国股票):
  - 年化 17.83% (无成本) / 12.90% (含成本)
  - Information Ratio 1.997 / 1.444
  - Max DD -8.18% / -9.11%
- **弱点**: 无实盘、中国市场偏重、学习曲线陡、开发重心转向 RD-Agent
- **License**: MIT

### 3. AI4Finance FinRL

- **架构**: 三层 — 数据处理 → Gym 环境(状态/动作/奖励) → DRL 智能体
- **核心创新**:
  - **集成 DRL 策略**: 同时训练 A2C/PPO/DDPG/SAC/TD3，滚动窗口按 Sharpe 选最优
  - **波动率风控**: 波动率指数超阈值时自动清仓
  - **FinRL-DeepSeek** (2025): LLM 风险信号 + RL 决策（134% vs 73% buy-and-hold）
  - **三后端**: Stable-Baselines3 / ElegantRL(GPU) / RLlib(分布式)
- **数据**: 14+ 数据源（Yahoo, Alpaca, Binance, CCXT, WRDS 等）
- **弱点**: **基本停更**（上次 release 2022.06）、代码质量差、RL 过拟合风险极高、明确声明不适合真钱
- **学术**: 6 篇论文（NeurIPS 2018/2020/2022, ICAIF 2020/2021, Springer 2024）
- **License**: MIT

### 4. polakowo VectorBT

- **架构**: 数据 → 指标工厂(Numba JIT) → 信号矩阵 → Portfolio 模拟(全向量化) → 分析
- **核心创新**:
  - **向量化策略表示**: 1000 个参数组合 = NumPy 数组的 1000 列，同时处理
  - **Numba JIT 桥接路径依赖**: 向量化 + JIT 编译的创造性结合
  - **MappedArray 稀疏存储**: 只存事件记录，内存效率极高
  - **IndicatorFactory**: 声明式配置创建指标，自动获得广播/缓存/JIT
- **性能**: 比 Backtrader ~1000x 快，Rolling Sortino 比 QuantStats 343x 快
- **PRO 版** ($20/月): 并行(Dask/Ray)、组合优化、止损阶梯、杠杆、JAX GPU
- **弱点**: 单人项目（bus factor=1）、免费版停止开发、无实盘、Commons Clause 限制商用
- **License**: Apache 2.0 + Commons Clause

---

## 与我们 Finance Desk 的关系

### 为什么不直接用

我们的投资逻辑是**周到月级持仓、OPRMS 驱动、基本面因子 + 宏观 regime + 定性判断**。四个框架解决的核心问题是 tick/分钟级逐笔成交模拟（滑点、部分成交、订单簿），这对我们是大炮打蚊子。

我们需要的是**因子研究 + 策略验证工具**，不是交易引擎。

### 可借鉴的设计理念

| 来源 | 理念 | 应用方向 |
|------|------|----------|
| Qlib Expression Engine | 因子 DSL，声明式定义 | 提升 Factor Study 的因子定义效率 |
| Qlib Alpha158/360 | 标准因子库 | 因子研究的灵感库和 benchmark |
| Qlib RD-Agent | LLM 自动因子挖掘 | Factor Research Codex 的核心方向 |
| Qlib PIT | Point-in-Time 设计 | 防止基本面因子前视偏差 |
| FinRL 集成策略 | 多模型滚动选优 | Factor Study 的模型组合思路 |
| FinRL 波动率指数 | 市场异常退出 | 与已有 regime multiplier 一致 |
| VectorBT 向量化 | 参数空间并行搜索 | 因子参数扫描性能升级方向 |
| VectorBT IndicatorFactory | 声明式指标工厂 | indicator 插件体系的借鉴 |

### 不需要的

- 四者的数据层 — 我们已有 FMP + SQLite + CSV 基建
- 四者的实盘能力 — Lean 最强但我们暂不需要自动执行
- 四者的期权支持 — 只有 Lean 有，我们 Options Desk 另有方案

---

## 下一步方向

在现有 `backtest/factor_study/` + `backtest/engine.py` 基础上演进自建框架，重点：
1. **因子挖掘** — 因子定义 → IC/IR → 分层回报 → 衰减分析
2. **组合构建** — Top-N 选股 / 权重优化 → 换仓 → benchmark-adjusted return
3. **风险归因** — 暴露分解 → 回撤分析 → regime 条件表现
4. **LLM 因子挖掘** — 参考 RD-Agent 思路，与 Factor Research Codex 结合
