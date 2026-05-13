# Concept Taxonomy v2 — Design Spec

**Date**: 2026-05-13
**Status**: Spec v2 Rev 3（Boss review 第 2 轮修订完成，可进入实现 plan）
**Supersedes**: `config/concepts/report_concepts.json` (14-bucket legacy) + 已有 7 条 `company_concept_tags` (LLM-reviewed phase2)
**Scope**: Extended pool **533 只美股**。**本期不覆盖 ETF**（extend 池里 0 个 ETF），SPY/QQQ/SOXX 等仍走 legacy bucket fallback。本期不动核心池外的非 extend 池股票。

---

## 1. 背景与动机

### 1.1 现状

晨报（`scripts/morning_report.py`）展示的"概念标签"，95%+ 来自 `config/concepts/report_concepts.json`：
- 14 个 legacy single-level bucket（AI算力/云、半导体链、互联网/广告 …）
- 222 个 symbol_bucket_overrides + 14 条 keyword rule
- 默认 fallback = "其他"

仅 7 只股票（LLM-reviewed phase2 产物）走 `market.db.company_concept_tags` 三层 registry。绝大多数股票只展示**单层粗 bucket**，且分类逻辑分散在 JSON 配置中。

### 1.2 三个核心限制

1. **单层粒度不够** — NVDA / INTC / AMD 都是"半导体链"，看不出 GPU vs CPU vs 设备差异
2. **跨行业主题无聚合维度** — "AI 算力" 跨 IT / Comm Services / Consumer Discretionary（NVDA + AMZN + GOOGL），14 bucket 没法切
3. **多元化公司表达单薄** — AMZN 只能在一个 bucket，零售面 / 云面 / 广告面无法分层表达

### 1.3 目标

为 533 只 extend 池股票建立 4 维标签体系（**不含 ETF**，extend 池里 0 个 ETF），支撑：
- 三层 grouping（板块 / 赛道 / 细分）
- 跨行业**热点主题聚合**（晨报后续计算估值 / 动量切片的基础）
- 市值档位横切

**非目标**：不覆盖核心池外的全市场；不替代 OPRMS；不替代 themes 引擎（`terminal/themes.py`）的主题研究功能。

---

## 2. 架构总览

### 2.1 4 列 schema

| 列 | 类型 | 父子关系 | 数量 | 聚合用途 |
|---|---|---|---|---|
| **L1** | enum (`concepts.concept_id` 中 level=1) | 根 | **11**（ETF 已从本期移除） | 板块层估值/动量 |
| **L2** | enum (`concepts.concept_id` 中 level=2) | **严格挂在 L1 下**（每个 L2 是其 L1 的真子集，FK `parent_id`） | 60 | 赛道层估值/动量（主用） |
| **L3** | JSON array of enum (`concepts.concept_id` 中 level=3, concept_type=`theme`) | **独立轴**：稳定主题轴，月度 review，与 L1/L2 平行（无 parent_id） | 42 | 跨行业主题聚合 |
| **L4** | enum (派生，不存表) | 派生（builder 出 CSV 时读 `company.db.companies.market_cap` 填参考列） | 4 + null | 市值档位横切（本期仅 CSV 参考，不进 DB / 不进晨报） |

### 2.2 关键设计选择

| 选择 | 决策 | 理由 |
|---|---|---|
| 分类哲学 | **纯自定义投资骨架**（拒绝套用 GICS） | GICS 体现行业归属，不体现投资视角；AMZN/MSFT 在 GICS 里永远进零售/IT，无法做"AI 算力"聚合 |
| L1/L2 关系 | 严格父子，单一主链路 | 聚合自洽（每个 L2 集合是其 L1 集合的真子集） |
| L3 关系 | 独立轴 + 多标签 + 封闭集 | 主题跨行业，多标签可覆盖一只股票的多个主题；封闭集避免命名漂移 |
| 多元化公司 | 单一主链路 = 市场估值核心 + 主导催化剂 | 例：AMZN 主链路走 AI 算力（AWS 是估值核心）；零售面通过 L3 themes 表达 |
| L4 市值档位 | 派生字段，不存表 | 市值每天变，固化档位会与 `companies.market_cap` 脱节 |

### 2.3 数据流

```
FMP profile (sector/industry/description)
       ↓
Prefill pipeline (keyword rules + LLM)
       ↓
CSV: reports/concept_registry/extended_pool_tags_YYYY-MM-DD.csv
       ↓
Boss 手动审改
       ↓
build_company_concept_registry.py --read-reviewed-csv (校验 + 回写)
       ↓
market.db.company_concept_tags
       ↓
ConceptClassifier.display_tags() / aggregate_by_l1() / aggregate_by_theme()
       ↓
Morning report / dashboard / future PI aggregation
```

---

## 3. Taxonomy 详细定义

### 3.1 L1 行业骨架（11 个）

| # | L1 ID | L1 中文名 | 范围 | 典型成员 |
|---|---|---|---|---|
| 1 | `ai_compute_cloud` | AI算力与云 | 超大规模云、AI 平台、自研芯片云、数据中心运营 | AMZN, MSFT, ORCL, IBM, ANET, SNOW, DDOG, EQIX |
| 2 | `semiconductor` | 半导体 | 芯片设计 / 设备 / 代工 / 模拟 / IP | NVDA, AMD, INTC, ASML, TSM, AVGO, MU, AMAT, ARM, QCOM |
| 3 | `internet_software` | 互联网与软件 | 搜索 / 社交 / 流媒体 / 消费互联网 / 游戏 / SaaS / 安全 | GOOGL, META, NFLX, ADBE, CRM, NOW, UBER, EA, CRWD |
| 4 | `autonomy_robotics` | 自动驾驶与机器人 | 电动车整车、FSD、工业 / 医疗机器人 | TSLA, RIVN, LCID, MBLY |
| 5 | `pharma_life_sci` | 创新药与生命科学 | 大药企 / 生物科技 / 医疗器械 / 工具 / 医保 | LLY, NVO, JNJ, MRK, ABBV, REGN, GILD, TMO, ISRG |
| 6 | `finance` | 金融 | 银行 / 投行 / 资管 / 保险 / 支付（**不含加密**） | JPM, BAC, GS, BLK, SCHW, V, MA, BRK.B |
| 7 | `crypto` | 加密资产 | **沾边即归** — 交易所 / 挖矿 / 储备（**注**：加密 ETF 不在 extend 池，本期不打标） | COIN, HOOD, MARA, RIOT, MSTR |
| 8 | `consumer_retail` | 消费与零售 | 必需 / 可选 / 餐饮 / 零售 / 海外电商 / 消费电子硬件 | KO, PEP, MCD, WMT, COST, HD, NKE, BABA, AAPL |
| 9 | `energy_materials` | 能源与材料 | 油气 / 新能源 / 矿业 / 化工 / 农产 | XOM, CVX, COP, SLB, FCX, LIN, SHW |
| 10 | `industrial_aerospace` | 工业与航天 | 航空航天 / 国防 / 重工 / 物流 / 资本品 / 航空公司 | BA, LMT, RTX, HON, CAT, DE, UPS, FDX |
| 11 | `realestate_utility` | 地产与公用 | REIT / 电力公用 / 通信塔 / 建材 | PLD, AMT, NEE, DUK, SO, EQR |

**Scope note**：L1 = ETF 已从本期移除。SPY/QQQ/SOXX 等 ETF 不在 extend 池（FMP screener 不含 ETF），晨报中 ETF 标签仍走 `report_concepts.json` legacy fallback。未来如要覆盖 ETF，会单独立项扩展。

### 3.2 L2 赛道（60 个，严格挂在 L1 下）

#### L1=1 AI算力与云（5）
- `hyperscaler` 超大规模云 — AMZN, MSFT, ORCL
- `datacenter_reit` 数据中心运营 (REIT) — EQIX, DLR
- `ai_platform_saas` AI 平台与数据 SaaS — SNOW, DDOG, NET, MDB
- `datacenter_power_cool` 数据中心电力与冷却 — VRT, ETN, GEV
- `datacenter_network` 数据中心网络设备 — ANET, CIEN, HPE

#### L1=2 半导体（7）
- `gpu_accelerator` 计算芯片 / GPU 加速器 — NVDA（**AMD 因 CPU 主链路不在此**）
- `cpu_soc` CPU 与 SoC — INTC, **AMD**, QCOM
- `memory_chip` 存储芯片 — MU, WDC, STX
- `foundry` 晶圆代工 — TSM, GFS
- `semi_equipment` 半导体设备 — ASML, AMAT, LRCX, KLAC
- `analog_power` 模拟与功率 — TXN, ADI, ON, NXPI
- `ip_eda_fabless` 芯片 IP / EDA / Fabless 其他 — ARM, AVGO, MRVL, SNPS, CDNS

#### L1=3 互联网与软件（8）
- `search_engine` 搜索引擎 — GOOGL
- `social_media` 社交媒体 — META, SNAP, PINS, RDDT
- `streaming_content` 流媒体与内容 — NFLX, DIS, SPOT
- `consumer_internet_platform` 消费互联网平台 — UBER, ABNB, DASH, BKNG, EXPE, ETSY, CHWY
- `gaming_interactive` 游戏与互动娱乐 — EA, TTWO, RBLX, U
- `enterprise_saas` 企业 SaaS（含广告 SaaS） — CRM, NOW, ADBE, INTU, WDAY, TTD, APP
- `cybersecurity` 信息安全 — CRWD, PANW, ZS, FTNT, OKTA
- `ecommerce_saas` 电商 SaaS — SHOP

#### L1=4 自动驾驶与机器人（3）
- `ev_oem` 电动车整车 — TSLA, RIVN, LCID, NIO, XPEV, LI
- `autonomy_perception` 自动驾驶感知系统 — MBLY
- `industrial_medical_robotics` 工业 / 医疗机器人 — (ISRG 主链路走 L1=5)

#### L1=5 创新药与生命科学（5）
- `large_pharma` 大型制药 — JNJ, MRK, PFE, ABBV, BMY, AZN, NVS
- `innovative_biotech` 创新生物科技 — LLY, NVO, REGN, GILD, VRTX, BIIB, MRNA
- `medical_device` 医疗器械 — ISRG, MDT, ABT, BSX, SYK
- `life_sci_tools_cro` 生命科学工具与 CRO — TMO, DHR, A, ILMN, IQV, ICLR
- `health_services` 医保健康服务 — UNH, CVS, HUM, CI

#### L1=6 金融（6）
- `mega_bank` 大型综合银行 — JPM, BAC, C, WFC
- `investment_bank` 投资银行与交易 — GS, MS
- `asset_management` 资产管理 — BLK, BX, KKR, AMP
- `broker_exchange` 经纪与交易所 — SCHW, CME, ICE, MKTX
- `payment_fintech` 支付与金融科技 — V, MA, AXP, PYPL, FI, FIS
- `insurance` 保险 — BRK.B, AIG, PRU, MET, ALL, TRV, CB

#### L1=7 加密资产（3）
- `crypto_exchange` 加密交易所 / 经纪 — COIN, **HOOD**
- `crypto_mining` 加密挖矿 — MARA, RIOT, CLSK *(注：本期 extend 池均不含，保留槽位备扩展)*
- `crypto_treasury` 加密储备 / 敞口 — MSTR, SQ *(SQ 不在 extend 池)*

#### L1=8 消费与零售（7）
- `consumer_staples` 必需消费品 — KO, PEP, PG, CL, KMB, MO, MDLZ
- `consumer_discretionary_brand` 可选消费品牌 — NKE, LULU, RL, EL
- `restaurant` 餐饮 — MCD, SBUX, CMG, YUM
- `big_box_retail` 大型零售 — WMT, COST, TGT, KR
- `home_improvement` 家居建材零售 — HD, LOW, FND
- `oversea_ecommerce_platform` 海外电商平台 — BABA, JD, MELI, PDD
- `consumer_electronics_brand` 消费电子与品牌硬件 — AAPL, SONY, LOGI

#### L1=9 能源与材料（6）
- `integrated_oil` 综合油气 — XOM, CVX, COP, BP, SHEL, TTE
- `oil_services` 油服与设备 — SLB, HAL, BKR, FTI
- `clean_energy` 新能源（光伏 / 风电 / 储能设备） — ENPH, FSLR, NEE 留给 L1=11
- `mining_metals` 矿业与金属 — FCX, NEM, RIO, BHP
- `chemicals_gases` 化工与气体 — LIN, APD, ECL, SHW, DOW, DD
- `agriculture_fertilizer` 农产与化肥 — ADM, BG, NTR, MOS, CF, CTVA

#### L1=10 工业与航天（6）
- `aerospace_defense` 航空航天与国防 — BA, LMT, RTX, NOC, GD
- `industrial_automation` 工业自动化与机械 — HON, ETN, EMR, ROK, ROP, PH
- `heavy_machinery` 重型机械与农机 — CAT, DE, AGCO
- `logistics_shipping` 物流与航运 — UPS, FDX, EXPD, CHRW
- `airlines` 航空公司 — DAL, UAL, AAL, LUV
- `engineering_construction` 工程与建筑 — URI, MAS, PWR

#### L1=11 地产与公用（4）
- `power_utility` 电力与公用（含水务燃气） — NEE, DUK, SO, AEP, EXC, D, AWK
- `residential_commercial_reit` 住宅与商业 REIT — PLD, EQR, AVB, ESS
- `comm_infra_reit` 通信基建 REIT — AMT, CCI, SBAC
- `industrial_materials` 工业建材 — VMC, MLM

**L2 总数 = 5+7+8+3+5+6+3+7+6+6+4 = 60 个**（已删除 ETF L1 下 6 个 L2 + 加密 ETF L2）

### 3.3 L3 主题轴（42 个，6 簇，**稳定主题轴，月度 review**）

**L3 stable ID 表**（`concept_id` 即 PK，与 L1/L2 同 namespace，由 `concept_taxonomy_v2.json` SSOT 定义）。`aliases` 列用于 CSV 回读时把 Boss 写的中文 label 容错映射回 `concept_id`。

#### 簇 1: AI 全链路（10）

| id | 中文名 | status | aliases |
|---|---|---|---|
| `ai_compute` | AI算力 | active | AI算力, AI 算力 |
| `ai_inference` | AI推理 | active | AI推理, 推理 |
| `edge_ai` | 端侧AI | active | 端侧AI, 边缘AI, on-device AI |
| `inhouse_chip` | 自研芯片 | active | 自研芯片, 云厂自研 |
| `hbm` | HBM | active | HBM, 高带宽内存 |
| `datacenter_power` | 数据中心电力 | active | 数据中心电力, DC 电力 |
| `datacenter_cooling` | 数据中心冷却 | active | 数据中心冷却, 液冷 |
| `ai_model_layer` | AI模型层 | active | AI模型层, 大模型, foundation model |
| `ai_application_layer` | AI应用层 | active | AI应用层, AI 应用 |
| `ai_infra_material` | AI基建材料 | active | AI基建材料, 光模块, CPO, PCB |

#### 簇 2: 下一代驱动（8）

| id | 中文名 | status | aliases |
|---|---|---|---|
| `autonomy_fsd` | 自动驾驶/FSD | active | 自动驾驶, FSD, 自动驾驶/FSD |
| `humanoid_robotics` | 人形机器人 | active | 人形机器人, Optimus |
| `quantum_computing` | 量子计算 | active | 量子计算, quantum |
| `commercial_space` | 商业航天 | active | 商业航天, 商业太空 |
| `nuclear_revival` | 核能复兴 | active | 核能复兴, 核电复兴 |
| `energy_storage` | 储能 | active | 储能, 储能电池 |
| `hydrogen` | 氢能 | active | 氢能, 氢燃料 |
| `nextgen_comms` | 6G/卫星通信 | active | 6G, 卫星通信, 6G/卫星通信 |

#### 簇 3: 医疗主题（6）

| id | 中文名 | status | aliases |
|---|---|---|---|
| `glp1_weightloss` | GLP-1减肥 | active | GLP-1, GLP-1减肥, 减肥药 |
| `gene_editing` | 基因编辑 | active | 基因编辑, CRISPR |
| `immunotherapy` | 免疫疗法 | active | 免疫疗法, 肿瘤免疫 |
| `alzheimers` | 阿尔茨海默 | active | 阿尔茨海默, AD |
| `digital_health` | 数字健康 | active | 数字健康 |
| `medical_ai` | 医疗AI | active | 医疗AI, AI 医疗 |

#### 簇 4: 宏观/货币（7）

| id | 中文名 | status | aliases |
|---|---|---|---|
| `btc_exposure` | 比特币暴露 | active | 比特币暴露, BTC 敞口 |
| `gold_proxy` | 黄金代理 | active | 黄金代理, 黄金敞口 |
| `inflation_hedge` | 通胀对冲 | active | 通胀对冲 |
| `rate_sensitive` | 利率敏感 | active | 利率敏感 |
| `usd_proxy` | 美元代理 | active | 美元代理, USD 敞口 |
| `china_exposure` | 中国敞口 | active | 中国敞口, China exposure |
| `geopolitics` | 地缘政治 | active | 地缘政治, 地缘风险 |

#### 簇 5: 行业周期（5）

| id | 中文名 | status | aliases |
|---|---|---|---|
| `semi_cycle` | 半导体周期 | active | 半导体周期 |
| `realestate_cycle` | 房地产周期 | active | 房地产周期, 地产周期 |
| `industrial_capex` | 工业资本开支 | active | 工业资本开支, capex |
| `energy_transition` | 能源转型 | active | 能源转型 |
| `defensive_stock` | 防御股 | active | 防御股, defensive |

#### 簇 6: 监管/政策（6）

| id | 中文名 | status | aliases |
|---|---|---|---|
| `antitrust_risk` | 反垄断风险 | active | 反垄断风险, 反垄断 |
| `tariff_exposure` | 关税敞口 | active | 关税敞口, 关税 |
| `ira_beneficiary` | IRA受益 | active | IRA受益, IRA |
| `fda_catalyst` | FDA审批催化 | active | FDA审批催化, FDA 催化 |
| `crypto_regulation` | 加密监管 | active | 加密监管, crypto regulation |
| `ai_export_control` | AI出口管制 | active | AI出口管制, 出口管制 |

**L3 总数 = 10+8+6+7+5+6 = 42 个**。

**L3 性质（按 Boss review v2 修订）**：
- **L3 是稳定主题轴**，不是短生命周期 hot theme。月度 review 节奏（不是季度），可新增/废弃但**基调稳定**
- L3 存在 `concepts` 表里，`level=3`, `parent_id=NULL`, `concept_type='theme'`, `status='active'`（废弃用 `status='deprecated'`）
- `company_concept_tags.theme_ids` JSON array 存 **L3 的 `concept_id`**（同 namespace 为 `concepts.concept_id`，不再走旧 `concept_themes.theme_id`）

**打 L3 规则**：
- 只打"影响估值/动量逻辑"的主题。XOM = [`inflation_hedge`, `geopolitics`]；KO = []（纯防御，无主题）
- 每只股票 L3 数量软上限 ~4（超过说明在硬塞，稀释聚合）
- 池子封闭：**所有 L3 必须出自上表 42 个 `concept_id`**。CSV 中 Boss 写的中文 label 在回读时通过 aliases 表容错映射回 ID；映射失败 → fail-fast
- 新主题出现要走维护流程改 `concept_taxonomy_v2.json`，再由 builder 重 upsert
- **月度 review L3 主题轴**（清掉过气、加入新兴）。L1/L2 不动

### 3.4 L4 市值档位

| Enum | 中文名 | 区间 | 大致数量 |
|---|---|---|---|
| `mega` | 巨头 | ≥ $1T | 6-8 只 |
| `large` | 大 | ≥ $300B 且 < $1T | ~30 只 |
| `mid` | 中 | ≥ $100B 且 < $300B | ~80 只 |
| `small` | 小 | ≥ $10B 且 < $100B | ~400 只 |
| `null` | — | ETF / 无市值 | SPY 等不参与档位 |

**存储用英文 enum，渲染时映射中文。**

**边界规则**：用 `≥` 划下界，`<` 划上界。$1T 整恰好算巨头；$300B 整算大；$100B 整算中；$10B 整算小。

**派生计算（跨 DB 边界，本期范围）**：
- `mcap_tier` **不存进 `market.db`**（既不加列、也不进 `company_concept_tags`）
- 本期 builder 在 Phase 4 输出 CSV 时跨库读取 `company.db.companies.market_cap` 填 `mcap_tier` 参考列，**仅供 Boss 审改时查看**，不参与 Phase 5 校验、不会被 Phase 6 回写
- ConceptClassifier / 晨报 / aggregation 暂不消费 L4
- 未来如需在查询路径派生（ATTACH `company.db` 或固化到列），单独立项扩展，不在本期

---

## 4. 多元化公司主链路规则

### 4.1 判定优先级（自上而下）

1. **市场估值核心**（分析师 SOTP / 估值 attribution 的主项）
2. **利润主导业务**（毛利贡献最大）
3. **收入主导业务**（兜底）

### 4.2 7 个 anchor 案例

**示例 L3 列均从 §3.3 的 42 个 `concept_id` 池中选取**（这里显示中文名以便阅读，回写 DB 时存 ID）。

| Symbol | 收入大头 | 估值核心 | L1 主链路 | L2 | L3 themes (示例) |
|---|---|---|---|---|---|
| AMZN | 零售 84% | AWS（高毛利 + AI 增长） | AI算力与云 | 超大规模云 | [`ai_compute`] |
| MSFT | Cloud 41% + Productivity 33% | Cloud + Copilot | AI算力与云 | 超大规模云 | [`ai_compute`, `ai_application_layer`, `edge_ai`] |
| GOOGL | 搜索广告 57% | 搜索广告 + AI | 互联网与软件 | 搜索引擎 | [`ai_compute`, `inhouse_chip`, `antitrust_risk`] |
| AAPL | iPhone 53% + Services 22% | iPhone + Services | 消费与零售 | 消费电子与品牌硬件 | [`edge_ai`, `china_exposure`, `antitrust_risk`] |
| TSLA | 汽车 86% | FSD + Optimus | 自动驾驶与机器人 | 电动车整车 | [`autonomy_fsd`, `humanoid_robotics`, `energy_storage`] |
| AVGO | 半导体 78% | 半导体 + VMware | 半导体 | 芯片 IP / EDA / Fabless 其他 | [`ai_compute`, `ai_infra_material`] |
| BRK.B | 保险 + 控股 | 控股 + 浮存金 | 金融 | 保险 | [`defensive_stock`, `rate_sensitive`] |

### 4.3 多元化"另一面"的承接

第二条业务线**不在 L1/L2 重复表达**（避免双重计数）。只有当 §3.3 的 42 L3 池子里**存在对应稳定主题**时，才通过 L3 themes 标记；池子里没有对应主题就不打（不为单只股票临时新增 L3）。例：
- TSLA 储能面 → L3 = `energy_storage`（池中有 ✓）
- AVGO VMware 面 → 不打 L3（不显著影响估值，且池中无企业软件主题）
- AMZN 零售/物流面 → 不打 L3（池中没有"电商生态/物流网络"主题；如未来需要表达，走 taxonomy 月度 review 流程新增）
- AAPL Services 面 → 不打 L3（池中没有"Services生态"主题；如未来需要表达，同上）

---

## 5. Database Schema 演进

### 5.1 `concepts` 表（schema 不变，内容重建）

现有 schema 保留（`concept_id PK, label, level, parent_id, concept_type, status, created_at, updated_at`），内容重建。

**FK 风险说明**：`market_store.py:563` 强制 `PRAGMA foreign_keys=ON`，且 `concept_themes.parent_concept_id` 有 FK references `concepts(concept_id)`。现有 5 条 concept_themes（hbm/liquid_cooling/ai_pc/nuclear_revaluation/silicon_photonics）的 `parent_concept_id` 指向旧 concepts 行（memory / data_center_power / semiconductor / nuclear_power / optical_communications）。如果直接 `DELETE FROM concepts` 会触发 FK constraint failure。

**迁移顺序（必须严格按顺序执行，事务包裹）**：

```sql
BEGIN;
-- Step 1: 切断 concept_themes 对 concepts 的 FK 引用（保留 5 条快照，仅断开父子链接）
UPDATE concept_themes SET parent_concept_id = NULL;

-- Step 2: 清空旧 concepts（此时无 FK 引用，可安全删除）
DELETE FROM company_concept_tags;  -- v1 LLM-reviewed 7 条也清空（schema 兼容但语义重建）
DELETE FROM symbol_concept_edges;  -- Phase 2 reservation 表，本期未写入但同样 FK references concepts
DELETE FROM concepts;

-- Step 3: 写入 v2 concepts
INSERT INTO concepts (...) VALUES ...;  -- 11 L1
INSERT INTO concepts (...) VALUES ...;  -- 60 L2
INSERT INTO concepts (...) VALUES ...;  -- 42 L3
COMMIT;
```

写入内容：
- 11 个 L1 — `level=1, parent_id=NULL, concept_type='evergreen'`
- 60 个 L2 — `level=2, parent_id=对应 L1 ID, concept_type='evergreen'`
- 42 个 L3 — `level=3, parent_id=NULL, concept_type='theme'`

**注**：L3 进 `concepts` 表是关键设计决策（让 `theme_ids` 与 L1/L2 同 namespace），不进旧 `concept_themes` 表。

### 5.2 `company_concept_tags` 表（schema 兼容，语义重定义）

现有列保留，语义调整：

| 列 | v2 语义 |
|---|---|
| `primary_concept_id` | L1 `concept_id`（必填） |
| `secondary_concept_id` | L2 `concept_id`（必填；必须 `parent_id = primary_concept_id`） |
| `tertiary_concept_id` | **弃用** — 永远 NULL（v1 用作 3rd concept，v2 由 L3 themes 接管） |
| `theme_ids` | JSON array of **L3 `concept_id`**（同 `concepts.concept_id` namespace，**不是** `concept_themes.theme_id`）。**首位 = 最主要主题**（语义约定，影响 display 渲染） |
| `display_tags` | DB canonical = `"L1_label / L2_label / L3_first_label"`（L3 数组为空时降级为 `"L1_label / L2_label"`）。例：NVDA = "半导体 / 计算芯片/GPU加速器 / AI算力" |
| `business_role` | 一句话业务描述 |
| `confidence`, `source`, `needs_review`, `evidence`, `updated_at` | 沿用 |

**为何不加新表**：schema 兼容；tertiary 留 NULL 不破坏 FK；theme_ids JSON array 已支持多 L3。

### 5.3 `concept_themes` 表在 v2 中的地位（Boss review 必答项）

- **v2 完全不使用 `concept_themes` 表**作为数据源或写入目标
- 保留表结构和现有 5 条数据（hbm / liquid_cooling / ai_pc / nuclear_revaluation / silicon_photonics）作历史快照，不 DROP（避免 schema migration 痛点）
- **5 条快照的 `parent_concept_id` 在 Phase 1 迁移时被置 NULL**（详 §5.1），切断 FK 后历史快照仅保留 theme_id/label/lifecycle 字段，作"曾经存在过这些 themes"的考古证据，不再指向任何 concept
- `src/data/market_store.py::upsert_concept_themes()` 方法保留但 **v2 builder 不再调用**，加 deprecation comment
- v2 builder 不再 `SELECT theme_id, label FROM concept_themes` 拼接 display_tags（**当前 builder line 284 的逻辑要重写**）
- 旧 5 条 themes 与 v2 L3 池子的对应：仅作 reference，**不做数据迁移**——v2 L3 是全新池子，由 `concept_taxonomy_v2.json` 定义。如要把"硅光"加入 v2 L3 池需要走 taxonomy 维护流程

### 5.4 Legacy 兼容

- 老 `report_concepts.json` 14 bucket **保留**作 fallback（注册表 miss 时 ETF 等仍用）
- `terminal/concept_classifier.py::_CONCEPT_TO_LEGACY_BUCKET` 需要更新（11 个新 L1 → 14 个旧 bucket 的映射表，提供 grouping continuity）
- 现存 7 条 LLM-reviewed `company_concept_tags` 数据**清空重建**（schema 语义不兼容；display 拼接逻辑不同）

---

## 6. CSV Schema

文件路径：`reports/concept_registry/extended_pool_tags_YYYY-MM-DD.csv`
配套文件：`reports/concept_registry/taxonomy_reference.csv`（L1/L2/L3 全集供查阅）

### 6.1 主 CSV 列定义（15 列）

| # | 列名 | 必填 | 类型 | 说明 |
|---|---|---|---|---|
| 1 | `symbol` | ✅ | str | 股票代码（PK） |
| 2 | `company_name` | ✅ | str | FMP 公司名 |
| 3 | `fmp_sector` | — | str | FMP GICS sector（参考） |
| 4 | `fmp_industry` | — | str | FMP GICS industry（参考） |
| 5 | `market_cap_b` | — | float | 市值（B$，参考） |
| 6 | `mcap_tier` | — | str | 派生（英文 enum: `mega` / `large` / `mid` / `small` / 空） |
| 7 | `description` | — | str | FMP 业务简介（截断 500 字符） |
| 8 | `l1` | ✅ | str | L1 行业骨架（预填，Boss 可改） |
| 9 | `l2` | ✅ | str | L2 赛道（预填；必须挂在 l1 下） |
| 10 | `l3_themes` | — | str | L3 主题轴（分号分隔，可空） |
| 11 | `business_role` | ✅ | str | 一句话业务描述（LLM 预填） |
| 12 | `prefill_source` | — | str | rule / llm / override |
| 13 | `confidence` | — | float | 0-1 |
| 14 | `needs_review` | — | int | 1=机器低信心 |
| 15 | `boss_notes` | — | str | Boss 自由备注 |

### 6.2 编辑规则

- UTF-8 编码，无 BOM
- l1 / l2 / l3 列使用中文标签名（不是 ID），导入时再映射回 ID
- l3_themes 分号分隔，**首位 = 最主要主题**：`AI算力;HBM;数据中心电力`（顺序影响 display_tags 第三段渲染）。所有元素必须来自 §3.3 的 42 L3 池，回读时按 `aliases` 表容错映射回 `concept_id`
- `needs_review` 高亮的行 Boss 重点看（confidence < 0.7）
- `market_cap_b` / `mcap_tier` 列由 builder 在 CSV 生成时跨库读取 `company.db.companies.market_cap` 填入，**仅供 Boss 查阅**，Phase 5 校验和 Phase 6 回写都不会消费这两列（本期 L4 不进 DB，详 §3.4）

### 6.3 taxonomy_reference.csv

| 列 | 说明 |
|---|---|
| `level` | 1 / 2 / 3 |
| `id` | concept_id |
| `name_cn` | 中文名 |
| `parent_id` | L1 节点为空，L2 节点 = 对应 L1 |
| `typical_members` | 3-5 个代表股票 |

---

## 7. Pipeline（**升级现有 builder，不新增 import 脚本**）

按 Boss review 必改项 #3：v2 走**单一 build pipeline 升级路径**。`scripts/build_company_concept_registry.py` 升级为 v2 主入口，不开第二条 `import_concept_tags.py` 主路径。

现有 builder 5 阶段架构升级映射：

| 现有阶段 | v2 升级 |
|---|---|
| 1) upsert concepts | 加载 `concept_taxonomy_v2.json`，upsert 11 L1 + 60 L2 + 42 L3 |
| 2) upsert concept_themes | **DEPRECATED** — 不再调用，builder 移除该步骤 |
| 3) build company rows (manual → rule → legacy → fallback) | classify chain 升级为返回 (L1, L2, L3 list, business_role) |
| 4) write review CSV (hard + soft) | 沿用，列扩展为新 15 列 schema |
| 5) gate check + upsert tags | 加 `--read-reviewed-csv` 子流程：从 Boss 审改后的 CSV 回读、校验、回写 |

### 7.1 Phase 1: Taxonomy 固化

- 写 `config/concepts/concept_taxonomy_v2.json`（SSOT，全集 + keyword rule）
- Builder 启动时加载该 JSON，按 §5.1 的迁移顺序在单个事务里重建 `market.db.concepts`：
  1. `UPDATE concept_themes SET parent_concept_id = NULL`（切 FK）
  2. `DELETE FROM company_concept_tags` + `DELETE FROM symbol_concept_edges` + `DELETE FROM concepts`
  3. `INSERT INTO concepts` × (11 L1 + 60 L2 + 42 L3)
- 同时输出 `reports/concept_registry/taxonomy_reference.csv` 供 Boss 查阅

### 7.2 Phase 2: FMP profile 拉取

- 533 只全量 `/profile/{symbol}` 调用（已有 86 只 sector/industry，但 description 不全，统一刷新）
- 写回 `data/fundamental/profiles.json`（**不**写 company.db；`companies` 表 schema 无 `description` 列且 P3 所有权由其他 pipeline 维护 `market_cap`）
- 不超过 FMP 限流（2s 间隔，~18 分钟）
- **写前 backup `data/fundamental/profiles.json`**（详 §9 回滚）

### 7.3 Phase 3: Prefill — Builder 升级后的 classify chain

`terminal/company_concepts.py::ConceptRegistry.classify()` 升级为输出 `(l1_id, l2_id, l3_id_list, business_role, confidence, source)`：

| Priority | 来源 | confidence | source |
|---|---|---|---|
| 1 | `company_concept_overrides.json` 手动 override | 0.99 | `manual` |
| 2 | keyword rule（FMP industry → L1+L2 直推） | 0.7 | `rule` |
| 3 | LLM judgment（Claude Haiku，输入 desc + taxonomy） | LLM 自评 0.5-0.95 | `llm` |
| 4 | LLM 调用失败（超时 / API 错） | 0.0 | `failed`（needs_review=1，**CSV 中 l1/l2/l3 留空**） |
| 5 | LLM 返回但解析失败（label 不在 taxonomy 内） | 0.1 | `fallback`（needs_review=1，**CSV 中 l1/l2/l3 留空**） |

**关键不变量**：
- 失败行（priority 4/5）**只在 CSV 中留空**，作为 Boss 待审改信号。**绝不进 DB**——CSV 回读阶段（Phase 5）若仍有空 l1/l2 行，fail-fast，整个 batch 不写 `company_concept_tags`
- 不引入"其他/未分类"作为 catch-all L1，避免污染 11 类骨架
- 533 行 extend 池 = 533 行 CSV，但**只有 l1/l2 全部合法的 batch 才允许 save 到 DB**（Phase 5 + Phase 6 联合保证）

### 7.4 Phase 4: review CSV 输出（沿用现有 builder 的双队列）

- 主 CSV: `reports/concept_registry/extended_pool_tags_YYYY-MM-DD.csv`（15 列，详 §6）
- 沿用现有 builder 的 `hard_needs_review`（fallback/failed 行，**block gate**）和 `soft_low_confidence`（confidence<0.7，不 block）双队列
- Boss 打开（Numbers/VSCode），按 `confidence` 升序审改

### 7.5 Phase 5: 回读 + 校验 + Gate

- Builder 加 `--read-reviewed-csv <path>` 参数：从 CSV 回读 l1/l2/l3_themes/business_role/boss_notes
- **fail-fast 校验**（详 §10 验收）：
  - 缺行（symbol 在 extend 池但 CSV 中缺失） → fail
  - 重复 symbol → fail
  - **l1 列为空 / null / 空白字符** → fail（不允许 NOT NULL 列 missing）
  - **l2 列为空 / null / 空白字符** → fail（v2 要求 l2 必填）
  - 非法 L1（不在 11 L1 集合） → fail
  - 非法 L2（不在 60 L2 集合） → fail
  - 非法 L2 parent（`l2.parent_id != l1.concept_id`） → fail
  - 非法 L3（中文 label 经 aliases 映射后仍不在 42 L3 集合，或 `level != 3`） → fail
  - 默认 fail-fast 退出；可加 `--validate-only` 产出 `_rejected.csv` 报告不退出
- **save 前置不变量**：通过 Phase 5 校验的 batch 必须满足 "533 行全部存在 + 每行 l1/l2 合法 + l3 列表元素全部合法"。任一条件不满足 → Phase 6 不执行
- Gate 沿用现有 `priority_coverage == 100% AND tail_needs_review_rate < 30%`，`--force-save` 可绕过 gate（但**不能绕过上述结构性校验**——`--force-save` 只放宽 confidence 分布要求）

### 7.6 Phase 6: 写入 market.db

- 校验通过的行 upsert 到 `company_concept_tags`（清空旧 7 条 LLM-reviewed）
- 重建 `display_tags` = `"L1_label / L2_label / L3_first_label"`（L3 空则 2 段）
- 事务包裹，失败回滚
- **写前 backup `market.db`**（详 §9）
- `--rebuild-display` 子命令保持可用

### 7.7 Phase 7: 下游接管（自动）

- `concept_classifier.py::display_tags()` 从 registry 命中 533 只 → 直接返回 DB canonical 字符串
- `concept_tags()` 返回 list（2 或 3 元素，从 DB canonical split " / "）
- 晨报展示 "半导体 / 计算芯片/GPU加速器 / AI算力" 三段
- 非 extend 池股票仍走 legacy bucket（fallback）

---

## 8. 下游影响

### 8.1 必改的现有文件（Boss review 必改项 #4）

| 文件 | 改动 |
|---|---|
| `scripts/build_company_concept_registry.py` | **主入口升级**：加载 `concept_taxonomy_v2.json` 替换旧 `taxonomy.json`+`concept_themes.json`；移除 `upsert_concept_themes` 调用（line 107）；重写 display_tags 拼接逻辑（line 270-340）不再读 `concept_themes` 表；加 `--read-reviewed-csv` 子流程；CSV 列扩展为新 15 列 schema |
| `terminal/company_concepts.py` | `ConceptRegistry` 加载 v2 taxonomy JSON；`classify()` 升级返回 `(l1_id, l2_id, l3_id_list, business_role, confidence, source)`；keyword rules 升级到新 11 L1 + 60 L2；移除 `theme_ids=[]` 占位（line 194/222/237），改为真实 L3 list |
| `terminal/concept_classifier.py` | `_CONCEPT_TO_LEGACY_BUCKET` 映射更新（11 个新 L1 → 14 个旧 bucket）；`display_tags()` 直接返回 DB canonical 字符串（不做 split / 不裁层）；`concept_tags()` 返回 split list |
| `src/data/market_store.py` | `upsert_concept_themes()` 加 `@deprecated` comment（保留方法但 v2 builder 不调）；`upsert_concept_tags()` 校验 `theme_ids` 指向 concepts.concept_id 而非 concept_themes.theme_id |
| `tests/test_company_concepts.py` 等 | classify chain 输出三元组 → 测试断言更新；旧 theme_ids 走 concept_themes 的测试改为指向 concepts.concept_id |
| `scripts/morning_report.py` | 无需改动（沿用 `clf.display_tags()` 接口；接口签名不变） |

### 8.2 新增文件

| 文件 | 内容 |
|---|---|
| `config/concepts/concept_taxonomy_v2.json` | SSOT — 11 L1 + 60 L2 + 42 L3 全集 + keyword rules + 多元化 anchor overrides |
| `reports/concept_registry/extended_pool_tags_YYYY-MM-DD.csv` | Boss 审改用 CSV |
| `reports/concept_registry/taxonomy_reference.csv` | L1/L2/L3 全集查阅表 |

### 8.3 不在本期范围（future work）

- 估值/动量聚合脚本（`aggregate_by_l1.py` / `aggregate_by_theme.py`）— 单独 plan
- 晨报新增 "L3 主题切片" section — 等聚合脚本就绪
- 核心池外的非 extend 池股票打标 — 暂不做
- ETF 标签覆盖 — 单独立项

---

## 9. 风险与回滚

| 风险 | 缓解 |
|---|---|
| LLM prefill 准确度低 → Boss 改很多 | `needs_review` 高亮 + confidence 排序；Phase 3 前 dry-run 5 只看效果 |
| 多元化公司主链路在金融 / 消费聚合时缺席 | L3 themes 兜底；后续可加 `alt_l1` 列允许第二主链路（不在本期） |
| L3 主题轴过气 | 月度 review；旧 tag 保留但 status='deprecated' |
| 回写 DB 失败 | 写前 backup `market.db` + `company.db`；事务回滚；先 dry-run 输出 diff |
| CSV 编辑误删行 | git track CSV；回写前 diff 提示；缺行 fail-fast |
| Schema 不兼容 LLM-reviewed 7 条 | 清空重建（Boss 已同意） |
| 旧 builder 路径误覆盖 v2 数据 | 升级后旧路径直接读 v2 taxonomy；旧 `taxonomy.json`/`concept_themes.json` 不再被加载；加 integration test 验证 |

### 9.1 回滚边界（Boss review 必改项 #6 — 双写入目标备份）

本期流程会写两类持久层，回滚必须同时恢复：

| 目标 | 写入操作 | Backup 文件 | 备份方式 |
|---|---|---|---|
| `data/market.db` | upsert `concepts` + `company_concept_tags` | `data/market.db.backup-<ts>-phase6` | sqlite3 backup API（**WAL-safe**，不用 shutil.copy） |
| `data/fundamental/profiles.json` | Phase 2 FMP profile 刷新 | `data/fundamental/profiles.json.backup-<ts>-preprofiles` | shutil.copy2（plain JSON 安全） |

**Builder 自动备份触发点**：
- Phase 2 写 `profiles.json` 前 → 备份 JSON
- Phase 6 写 `market.db` 前 → backup via `sqlite3.Connection.backup()`（WAL 安全，避免漏 -wal 里未 checkpoint 的事务）
- Backup 文件保留 7 天（cron 清理）

**注**：Phase 2 本期**不**写 `company.db`（Rev 3 v2 plan amendment）。company.db 的 `market_cap` 列继续由其他 data pipeline 维护，本期 builder 只读不写。

### 9.2 回滚步骤

1. 恢复 backup：
   - `cp data/market.db.backup-<ts>-phase6 data/market.db`（WAL-safe 拷贝的 backup，直接 cp 回去即可）
   - `cp data/fundamental/profiles.json.backup-<ts>-preprofiles data/fundamental/profiles.json`
2. revert v2 改动：`git revert <commits>` 或 checkout 旧版 `terminal/company_concepts.py` / `terminal/concept_classifier.py` / `scripts/build_company_concept_registry.py`
3. 晨报回到 14 bucket legacy fallback（验证：跑 `scripts/morning_report.py --dry-run` 看输出）

---

## 10. 验收标准

### 10.1 数据完整性

| # | 验收点 | 验证 SQL / 方法 |
|---|---|---|
| 1 | 11 L1 + 60 L2 + 42 L3 全部入 `concepts` 表 | `SELECT level, COUNT(*) FROM concepts GROUP BY level` → (1,11) (2,60) (3,42) |
| 2 | 533 只 extend 池全部入 `company_concept_tags` | `SELECT COUNT(DISTINCT symbol) FROM company_concept_tags WHERE symbol IN (extend 池)` = 533 |
| 3 | 每行 L2 严格挂在 L1 下 | `SELECT cct.symbol FROM company_concept_tags cct JOIN concepts c2 ON cct.secondary_concept_id=c2.concept_id WHERE c2.parent_id != cct.primary_concept_id` 应返回 0 行 |
| 4 | L3 theme_ids 全部 join 到 concepts 且 level=3（Boss review 必改项） | 解析每行 theme_ids JSON，对每个 id：`SELECT level FROM concepts WHERE concept_id=?` 必须 = 3 |
| 5 | 旧 `concept_themes` 表不参与 v2 display rebuild（Boss review 必改项） | grep `concept_themes` 在 v2 builder + classifier 中不应被读取；integration test 删表后 v2 流程仍正常 |
| 6 | 旧 builder 路径不会覆盖 v2 taxonomy（Boss review 必改项） | 删除 `config/concepts/taxonomy.json`/`concept_themes.json` 后跑 builder，仍能基于 v2 JSON 正常工作 |

### 10.2 CSV 回读 fail-fast 校验（Boss review 必改项）

| # | 错误类型 | 行为 |
|---|---|---|
| 1 | symbol 在 extend 池但 CSV 中缺失 | fail-fast：报告所有缺失 symbol 并退出，不写 DB |
| 2 | CSV 中 symbol 重复 | fail-fast：报告重复行，退出 |
| 3 | **l1 列为空 / null / 空白** | fail-fast（save 前 533 行 l1 必须全部有值） |
| 4 | **l2 列为空 / null / 空白** | fail-fast（save 前 533 行 l2 必须全部有值） |
| 5 | l1 不在 11 L1 集合 | fail-fast |
| 6 | l2 不在 60 L2 集合 | fail-fast |
| 7 | l2 的 parent_id ≠ l1 | fail-fast |
| 8 | l3_themes 中任一元素经 aliases 映射后仍不在 42 L3 集合 | fail-fast |
| 9 | l3_themes 中元素 level ≠ 3 | fail-fast |
| 10 | `--validate-only` 模式 | 产出 `_rejected.csv`，不退出，不写 DB |

### 10.3 晨报渲染

| # | 验收点 | 方法 |
|---|---|---|
| 1 | 晨报中 533 只概念标签从单层升级到三段（有 L3 时） | 跑 `python scripts/morning_report.py --dry-run` 看输出含 "/ / " 格式 |
| 2 | 非 extend 池股票 + ETF 仍走 legacy 14 bucket | 同上，验证 SPY 等仍是单 bucket 显示 |

### 10.4 Boss 审改质量

| # | 验收点 | 阈值 |
|---|---|---|
| 1 | 审改后 needs_review=1 的剩余比例 | < 5% |
| 2 | 现有 7 条 LLM-reviewed 数据被覆盖 | `SELECT COUNT(*) FROM company_concept_tags WHERE source='llm_reviewed'` = 0 |

---

## 11. Open Questions / 待后续讨论

1. **alt_l1（双主链路）**：是否要加 `alt_l1` 列让 AMZN 同时进 AI算力 + 消费聚合？本期不做，但 schema 留扩展空间
2. **L4 持久化**：当前派生计算，是否未来要固化到 `mcap_tier` 列以做"档位变化"事件追踪？
3. **L3 主题轴动态化**：月度 review 机制谁负责（Boss 还是自动产出建议）？
4. **核心池外股票**：是否后续要打到全 FMP universe？还是只覆盖 extend 池？
5. **业务变化的更新机制**：例如 AMZN 某天 AWS 占比再涨，主链路定义不变；但具体股票的 L1/L2 应该多久 review 一次？建议年度

---

## 12. 变更日志

| 日期 | 变更 | 决策人 |
|---|---|---|
| 2026-05-12 | 初稿；通过 brainstorming skill 与 Boss 一对一确认所有设计点 | Boss |
| 2026-05-12 (Rev 2) | Boss review 第 1 轮修订：①L1 删 ETF (12→11)、L2 删 ETF + 加密 ETF L2 (67→60)；②L3 语义统一进 `concepts` 表 level=3 / concept_type='theme'，`theme_ids` 引用 `concepts.concept_id`；③ `concept_themes` 表 v2 不读不写（保留 schema 不动）；④pipeline 改为升级 `scripts/build_company_concept_registry.py`，不新增 `import_concept_tags.py`；⑤补全必改文件清单（builder + company_concepts + concept_classifier + market_store + tests）；⑥display_tags 口径统一为 DB canonical 三段，不在晨报侧 split；⑦回滚边界双 DB（market.db + company.db）；⑧"热点池" 改名 "L3 主题轴"，季度→月度 review；⑨补 fail-fast 校验验收 | Boss |
| 2026-05-13 (Rev 3) | Boss review 第 2 轮修订（修 4 P1 + 1 P2）：①数据流图 phantom 脚本 `import_concept_tags.py` 改为 `build_company_concept_registry.py --read-reviewed-csv`；②533 行 save 不变量：失败行只在 CSV 留空、绝不进 DB，Phase 5 fail-fast 显式拒绝 l1/l2 空或不合法，§10.2 验收表加 l1/l2 missing 校验项；③L3 加 stable ID 表（42 个 `concept_id` + aliases 容错），anchor 全部改用池内合法 ID（AMZN/AAPL 池外标签删除并在 §4.3 说明），CSV 编辑规则示例 `数据中心` 改 `数据中心电力`；④`concepts` 重建迁移顺序写死：`UPDATE concept_themes SET parent_concept_id=NULL` → `DELETE FROM company_concept_tags / symbol_concept_edges / concepts` → `INSERT` 新 113 行，全部事务包裹，避开 FK constraint failure；⑤L4 跨 DB 边界澄清：本期 mcap_tier 仅 CSV 参考列（builder 出 CSV 时跨库读 company.db 填），不进 market.db / 不进晨报，未来再扩 | Boss |
| 2026-05-13 (Rev 3.1) | Plan writing 二审发现 + 落地修正（不改设计方向，只对齐物理实现）：①§7.2 Phase 2 写入目标从 `company.db.companies` 改为 `data/fundamental/profiles.json`（`companies` 表 schema 无 description 列，profiles.json 是现有 builder input 模式）；②§9.1 backup 表对齐双写入目标 (`market.db` + `profiles.json`)，并显式要求 `market.db` 用 sqlite3 backup API 而非 shutil.copy2（WAL 安全） | Boss |
