# 02 — 本地数据库全量实测与代码库技术契约审计报告

- **审计日期**: 2026-11-27
- **审计范围**: `data/market.db`（只读 URI）、`data/company.db`（只读 URI）、`data/prosperity.db`（只读 URI）、`config/concepts/*`、`terminal/concept_classifier.py`、`terminal/llm_concept_prefill.py`、`terminal/prosperity/*`、`src/data/market_store.py`、`scripts/morning_report.py`、`scripts/portfolio_intelligence.py`、`scripts/build_company_concept_registry.py`、`scripts/broad_universe_cron_wrapper.sh`
- **对应研究子问题**: R2 / R3 / R4 / R6 / R8 / R10 / R11 / R12

---

## 核心发现摘要（Executive Summary）

1. **覆盖池对账与三大实盘隐患（R4）**：
   - 活跃主池 `extended_membership (effective_to IS NULL)` 共 **856** 只（100% 属于 `security_master.eligible=1`）；叠加 `holdings`（22 只去重，其中 OPEN 4 只）与 `watchlist`（21 只）后，当前目标总并集为 **882** 只（若仅叠加 OPEN 持仓则为 **875** 只；含历史累计 Extended `1,011` 只则为 **1,033** 只）。
   - **隐患 A（多股类 Screener 抖动漏掉超级巨头）**：`GOOG`（Alphabet，Boss 当前 **OPEN** 持仓！）已于 `2026-11-21` 被标为 `effective_to='2026-11-21'` 掉出活跃 Extended 856 池，而 `GOOGL` 又在 `security_master` 被标为 `secondary_share_class -> GOOG`（`eligible=0`），导致若只跑活跃 Extended 856，**整个 Alphabet 在篮子计算中彻底消失**！同类掉出的还有 `FOXA`/`FOX`（`2026-11-07`）与 `NWSA`/`NWS`（`2026-11-07`）。
   - **隐患 B（现表 `company_concept_tags` 严重污染与缺口并存）**：现表 1,102 行中，一方面**漏掉了 19 只当前活跃 Extended 美股**（如 `CART`、`DOCU`、`FRO`、`FROG`、`GME`、`JKHY`、`MANH`、`PAYC`、`PCOR`、`SIRI`、`W` 等，因云端 `claude -p` 认证失效未落库）；另一方面混入了 **27 只五字母 `-X` 共同基金**（把 `asset_management` 从活跃 23 只灌水到 62 只）、**约 40 只优先股/次级债 Baby Bonds**（如 `STRC/D/F/K`、`SOJC/D/E`、`MGRB/D/E`），以及 **9 组同公司双股类同时在列**（`BRK-A`+`BRK-B`、`GOOG`+`GOOGL`、`FOX`+`FOXA` 等，直接导致等权涨跌与成交额双算）。
   - **隐患 C（异构标的未闭环拦截）**：`holdings ∪ watchlist` 中含 **7 只非美股**（港股 `01810`、`09992`、`03032`、`07709`、`1548.HK`、`2268.HK`，A 股 `603259.SS`）、**2 只 ETF**（`QQQ` OPEN、`DRAM` CLOSED）、**1 只封闭式私募股权基金 CEF**（`DXYZ`，`isFund=True`，未列入 `excluded_instruments.json`）、**1 个无效测试符**（`TEST`）以及 **1 只缺 profile 的美股分拆新股**（`GRAL`）。
2. **输入特征完备度极高（R6/R8）**：
   - 活跃 Extended **856 只**与历史 Extended **1,011 只**在 `market.db:company_profile` 中覆盖率均为 **100.0%**，`sector`、`industry` 零缺失，`description < 50` 字符为 **0 只**（最短 `CIB` 116 字符，其余 855 只全部 `≥ 150` 字符）。美股目标并集中仅 `GRAL`（GRAIL，Illumina 分拆上市）1 只真实美股缺 profile（外加 `concept_watchlist.json` 中的微盘股 `POET`）。
   - `market.db` 另有 `fmp_etf_holdings_snapshot`（含 `SOX`、`IGV`、`QQQ`、`SPY`、`XLF` 成分快照）和已验证原型的 `company_concept_evidence`（11 行 SEC 10-K Item 1 文本）可作辅助交叉核验。
3. **现有分类分布极度偏态，FMP `industry` 足以支撑干净重切（R2/R3）**：
   - 活跃 Extended 已入库的 837 只在 61 个 L2 中呈现典型哑铃失衡：**17 个 L2 `< 5` 只**（含 2 个 0 只空桶、5 个单票桶）、34 个在 `5~25` 只、**10 个 L2 `> 25` 只**（最大 `industrial_automation` 51 只、`mega_bank` 48 只、`integrated_oil` 47 只、`power_utility` 40 只）。
   - **L3 存储结构断层**：`company_concept_tags.tertiary_concept_id` 在全部 1,102 行中 **100% 为 NULL**；实际 L3 存于 JSON 数组列 `theme_ids`（活跃池 294/837 行非空），且把宏观风格因子（`rate_sensitive` 78、`defensive_stock` 48、`tariff_exposure` 33）与产业题材（`optical_comms` 5、`nuclear_revival` 5）混在同一层。
   - 实盘高弹性赛道**「光通信与高速互连」**当前被拆散在 5 个不同 L2（`datacenter_network`、`ip_eda_fabless`、`industrial_automation`、`semi_equipment`、`analog_power`），而 `datacenter_network` 又混入了 AI 服务器整机/存储（`DELL`、`HPE`、`SMCI`、`NTAP`），完全具备重切为两个独立 10~15 只高纯度篮子的底层基础。
4. **下游兼容契约与 `prosperity` 哈希红线（R10/R11/R12）**：
   - **晨报与 PI 用的是两套不同粒度接口**：`morning_report.py` 在模块导入期立即求值 `CONCEPT_BUCKET_ORDER = _active_bucket_order()`（61 个 L2 顺序），并用 `_grouping_bucket()`（L2 label）分组、`display_tags()` 展示三层标签；而 **`portfolio_intelligence.py` 故意调用 `classifier.bucket_order` 与 `classifier.classify()`（粗粒度 15 桶）** 来聚合持仓主题暴露与触发 `≥40% NAV` 集中度告警，并用 `business_role()` 渲染单行中文角色！重构时绝不能把 PI 粗暴改成 60 个 L2 桶，否则 `≥40% NAV` 告警阈值会被拆碎失效。
   - **`prosperity` 哈希铁律**：`src/data/market_store.py` 与 `config/settings.py` 均在 `terminal/prosperity/version.py:CODE_VERSION_SOURCES` 字节级 SHA256 哈希范围内，且 `tests/test_prosperity_schemes.py:118` 静态扫描所有被哈希文件的本地 `import`。新模块开发期**严禁改动这两个文件或被其 import**（保持 `code_version() == '4c9da30bd5001102'`）；未来退役旧表 DDL 与 `THEME_KEYWORDS_SEED` 必须合并为单次清理提交，并同步迁移 `scripts/scan_themes.py` 等调用方及执行 `prosperity_weekly.py --rebuild-chain`。
5. **云端运行环境与 LLM 通道现状（R8）**：
   - 仓库内**没有任何现成 HTTP/SDK LLM 客户端**，唯一 LLM 调用为 `subprocess.run(["claude", "-p", ...])`（见 `terminal/llm_concept_prefill.py:65`），且因云端 OAuth 组织权限失效（`docs/issues/034`）已长期瘫痪。
   - 本地 `.venv` 已安装 `anthropic`、`httpx`、`requests`、`pydantic`（未装 `openai` / `google-genai`，且 `requirements.txt` 仅声明了 `requests>=2.32`）；本地 `.env` 中唯一已配置的模型相关环境变量为 `MOONSHOT_API_KEY`（`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY` 等均为 `False`）。
   - `broad_universe_cron_wrapper.sh` 中 `concept_weekly_sync` 以 `wait_seconds=0` 抢占 `market_db_writer` 锁并在锁内串行跑分类；新架构必须改为**锁外并发/批处理分类，仅在最终毫秒级事务落库时短暂持锁（或独立 SQLite 库）**。

---

## 1. 目标覆盖池全量对账与异构标的盘点（对应 R4）

### 1.1 四表基数与集合对账矩阵

以下数据全部来自 `market.db` 与 `company.db` 只读实测：

| 数据源 / 集合定义 | 行数 | 去重 Symbol 数 | 关键说明 |
|---|---:|---:|---|
| `market.db:extended_membership`（全部历史行） | 1,089 | **1,011** | 含多次进出区间记录（如 `BRK-B` 2 条） |
| `extended_membership` **活跃主池** (`effective_to IS NULL`) | 856 | **856** | 当前 `$10B+` 默认美股主池；与 `sm_eligible` 交集 856（100%） |
| `extended_membership` **历史调出池** (`ext_all - ext_active`) | — | **155** | 曾进池后调出（含 `GOOG`、`FOXA`、`NWSA`、`ASTS`、`TEM`、`TWST` 等） |
| `market.db:security_master`（全表） | 1,405 | **1,405** | `eligible=1`: **1,254**（普通美股 1,100 + ADR 154）；`eligible=0`: **151** |
| `company.db:holdings`（全表） | 24 | **22** | `OPEN`: **4** (`AMZN, GOOG, MSFT, QQQ`)；`CLOSED` 独有: **18** |
| `company.db:watchlist`（全表） | 21 | **21** | 来自 `analysis` (8)、`core-retirement:analysis` (12)、`core-retirement:manual` (1) |
| `market.db:company_concept_tags`（现行概念表） | 1,102 | **1,102** | `rule`: 677，`llm`: 408，`manual`: 17（`needs_review=1` 为 0 行） |
| **并集 U1**: `ext_active ∪ holdings(OPEN) ∪ watchlist` | — | **875** | 当前实盘活跃最小全集 |
| **并集 U2**: `ext_active ∪ holdings(ALL) ∪ watchlist` | — | **882** | 含历史平仓标的复盘覆盖集 |
| **并集 U3**: `ext_all ∪ holdings(ALL) ∪ watchlist` | — | **1,033** | 含历史调出 Extended 的回测/PIT 全集 |
| **总并集 U4**: `U3 ∪ security_master ∪ company_concept_tags` | — | **1,454** | 本地库出现过的全部证券代码并集 |

#### 精确差集明细

- `ext_active − security_master` = **0 只**；`ext_active − security_master(eligible=1)` = **0 只**；`ext_all − security_master` = **0 只**。
- `security_master(eligible=1) − ext_all` = **255 只**（市值在 `$1B~$10B` Broad 池但从未触及 `$10B` Extended 门槛的合格普通股/ADR，如 `ABBNY, CRSP, JOBY, MXL, NTLA, QS`）。
- `holdings(OPEN) − ext_active` = **2 只**: `['GOOG', 'QQQ']`。
- `holdings(ALL) − ext_active` = **10 只**: `['01810', '03032', '07709', '09992', 'ASTS', 'DRAM', 'DXYZ', 'GOOG', 'MXL', 'QQQ']`（其中 `ASTS` 属 `ext_churned`，`MXL` 在 `sm_eligible`，其余 6 只 `['01810', '03032', '07709', '09992', 'DRAM', 'DXYZ']` 不在 `security_master`）。
- `watchlist − ext_active` = **17 只**: `['1548.HK', '2268.HK', '603259.SS', 'ABBNY', 'AXTI', 'CRSP', 'DXYZ', 'GRAL', 'JOBY', 'NTLA', 'NVTS', 'ONDS', 'QS', 'SES', 'TEM', 'TEST', 'TWST']`（其中 `TEM, TWST` 属 `ext_churned`；`ABBNY, CRSP, JOBY, NTLA, QS` 在 `sm_eligible`；其余 10 只不在 `security_master`）。
- **补充检查 `company.db` 其余业务表**：`option_positions` / `option_transactions` 另有韩国 ETF **`EWY`**（及 `QQQ`）；`config/concepts/concept_watchlist.json` 硬编码了 `['POET', 'OKLO', 'DXYZ']`。
- **现行 `company_concept_tags` 缺口 (`ext_active − cct`, 19 只)**:
  `['ANT', 'BSP', 'BSY', 'CART', 'DOCU', 'FRO', 'FROG', 'GME', 'JKHY', 'LTH', 'MANH', 'PAYC', 'PCOR', 'PSO', 'R', 'SAIL', 'SIRI', 'VMRK', 'W']`。
- **现行 `company_concept_tags` 历史残留与污染 (`cct − U3`, 116 只)**: 含 27 只 `-X` 共同基金、38 只优先股/次级债、多只次要股类（`BRK-A, FOX, FWONA, GOOGL, PBR-A`）及早期 Broad 池小票。

---

### 1.2 特殊与异构标的逐一分类盘点

#### a) 非美股交易所标的（共 7 只，全部位于 `holdings ∪ watchlist`，`market.db:company_profile` 全空）

| Symbol | 所在表与状态 | 市场与真实身份 | 现行状态与风险 |
|---|---|---|---|
| `01810` | `holdings` (CLOSED) | 港股（小米集团-W） | 无 FMP profile，未入 `cct` |
| `09992` | `holdings` (CLOSED) | 港股（泡泡玛特） | 无 FMP profile，未入 `cct` |
| `03032` | `holdings` (CLOSED), `cct` | 港股 ETF（恒生科技指数 ETF） | 曾被塞入 `consumer_internet_platform`，靠 `excluded_instruments.json` 事后过滤 |
| `07709` | `holdings` (CLOSED), `cct` | 港股杠杆产品（南方东英两倍做多） | 曾被塞入 `consumer_internet_platform`，靠 `excluded_instruments.json` 事后过滤 |
| `1548.HK` | `watchlist`, `analyses`, `oprms` | 港股（金斯瑞生物科技） | 无 FMP profile，未入 `cct` |
| `2268.HK` | `watchlist`, `analyses`, `oprms` | 港股（药明合联） | 无 FMP profile，未入 `cct` |
| `603259.SS` | `watchlist`, `analyses`, `oprms` | A 股上交所（药明康德） | 无 FMP profile，未入 `cct` |

> **注**：`ABBNY`（ABB Ltd，`watchlist` + `sm_eligible`）为美国 OTC 交易的瑞士工业巨头 ADR（`exchange='OTC'`, `country='CH'`），FMP profile 完整；另有 154 只 NYSE/NASDAQ 上市 ADR（如 `TSM`、`ASML`、`PDD`、`BABA`、`ARM`）在 `sm_eligible` 中，拥有完整美股量价与财务，应正常纳入美股主题篮子。

#### b) ETF / CEF / 共同基金 / 优先股债 / 无效测试代码

1. **ETF / 指数工具**：
   - `QQQ`：`holdings` (**OPEN**)、`option_positions`、`security_master(eligible=0, reason='etf')`、`report_concepts.json:etf_symbols`。
   - `SOXX`、`SPY`：`security_master(eligible=0, reason='etf')`、`report_concepts.json:etf_symbols`。
   - `EWY`（iShares MSCI South Korea ETF）：位于 `company.db:option_positions` 与 `companies`，不在 `market.db`。
   - `DRAM`：`holdings` (CLOSED)、`company_concept_tags(memory_chip)`、`excluded_instruments.json(ETF)`。**典型反面教材**：`company_profile` 为空，旧版 LLM 仅凭 ticker 字母猜测其为“DRAM 存储芯片制造商”，污染了 `memory_chip` 篮子。
   - `03032`、`07709`：港股 ETF / 杠杆产品（见上表）。
2. **封闭式基金（CEF）/ 信托（极易漏网！）**：
   - **`DXYZ`（Destiny Tech100 Inc.）**：同时存在于 `holdings` (CLOSED)、`watchlist`、`concept_watchlist.json`、`company_concept_tags(asset_management)`。`company_profile` 明确标记 **`isFund=True`, `isEtf=False`**（NYSE 上市封闭式私募科技股权基金，持仓 SpaceX/OpenAI 等未上市股权，溢价率剧烈波动），但**既不在 `security_master` 中，也未列入 `excluded_instruments.json`**！若不按 `isFund == True` 拦截，会混入资管股篮子扭曲等权涨跌。
   - `PSLV`（Sprott Physical Silver Trust）：`security_master(eligible=0, reason='fund')`，`isFund=True`。
3. **测试代码与缺 Profile 孤儿股**：
   - **`TEST`**：位于 `company.db:watchlist(source='analysis')`、`companies`、`analyses`，`market.db` 完全无记录，纯测试残留代码，必须在入口白名单/清洗门控直接剔除。
   - **`GRAL`（GRAIL, Inc.）**：位于 `watchlist`、`analyses`、`oprms_ratings`、`kill_conditions`，真实美股医疗早筛公司（2024 年从 Illumina 分拆，NASDAQ 上市），但尚未同步进 `market.db:company_profile` 与 `security_master`。
   - **`POET`（POET Technologies）**：位于 `concept_watchlist.json` 与 `company_concept_tags(datacenter_network)`，但 `market.db:company_profile` 无记录。
4. **现行 `company_concept_tags` 内的非普通股污染（共约 67 行）**：
   - **27 只五字母 `-X` 结尾共同基金**：`AAFTX, ABALX, ALLIX, AMFCX, AMFFX, BALFX, DFSVX, FESOX, FNPFX, FOCKX, FSNVX, FZTKX, GWPEX, HLMNX, HRCAX, LCEYX, LOLDX, PCBIX, POGAX, PRSVX, PRUIX, RGVGX, RICAX, RICBX, RNNEX, SSEYX, TAMVX, TWCUX`（全部混在 `asset_management`）。
   - **约 40 只交易所挂牌次级债（Baby Bonds）/ 优先股**：`AIZN, APOS, ATHS, BEPH, BEPI, BIPI, BMNP, BNH, BNJ, CCZ, CMS-PB, CMSA, CTA-PA, CTA-PB, DTW, DUKB, DUKU, ELC, EP-PC, FCNCN, FITB-PA, FITB-PM, FITBM, HBANZ, KKRS, MER-PK, MGRB, MGRD, MGRE, PFH, PPLC, RITM-PE, RZB, RZC, SATA, SOJC, SOJD, SOJE, SOMN, SREA, STRC, STRD, STRF, STRK, TBB, TCPA, TMUSI, TMUSL, UNMA, XELLL`。**根因**：旧落库脚本未强制校验 `security_master.eligible == 1`。

---

#### c) 同一公司多股类（Dual Share Class）与同 CIK 多代码全量核查

通过 `CIK` 归一化匹配 + 公司名归一化匹配 + `security_master.share_class_of` 三路交叉核验，在 `extended_membership ∪ holdings ∪ watchlist ∪ security_master` 中识别出以下三类关键情形：

##### 情形 1：真实普通股双股类对（共 18 组同公司多股类）

| CIK | 公司名称 | 主股类 (`SM:ok`) | 次股类 (`SM:secondary_share_class`) | 主股类当前状态 | `company_concept_tags` 双算现状 |
|---|---|---|---|---|---|
| `0001652044` | **Alphabet Inc.** | **`GOOG`** | **`GOOGL`** | ⚠️ **`GOOG` 于 2026-11-21 掉出 `ext_active`！仅靠 `holdings(OPEN)` 存活** | **双算**：`GOOG` 与 `GOOGL` 均在 `cct(search_engine)` |
| `0001067983` | **Berkshire Hathaway** | **`BRK-B`** | **`BRK-A`** | `BRK-B` 在 `ext_active`（曾于 10/24-11/07 短暂掉出）；`BRK-A` 在 `company.db:companies` | **双算**：`BRK-A` 与 `BRK-B` 均在 `cct(insurance)` |
| `0001754301` | **Fox Corporation** | **`FOXA`** | **`FOX`** | ⚠️ **`FOXA` 于 2026-11-07 掉出 `ext_active`！** | **双算**：`FOX` 与 `FOXA` 均在 `cct(streaming_content)` |
| `0001564708` | **News Corporation** | **`NWSA`** | **`NWS`** | ⚠️ **`NWSA` 于 2026-11-07 掉出 `ext_active`！** | **双算**：`NWS` 与 `NWSA` 均在 `cct(streaming_content)` |
| `0000014693` | Brown-Forman | `BF-B` | `BF-A` | `BF-B` 在 `ext_active` | 仅 `BF-B` 在 `cct` |
| `0000046619` | HEICO Corp. | `HEI` | `HEI-A` | `HEI` 在 `ext_active` | 仅 `HEI` 在 `cct` |
| `0000920760` | Lennar Corp. | `LEN` | `LEN-B` | `LEN` 在 `ext_active` | 仅 `LEN` 在 `cct` |
| `0000067887` | Moog Inc. | `MOG-A` | `MOG-B` | `MOG-A` 在 `ext_active` | 仅 `MOG-A` 在 `cct` |
| `0000105016` | Watsco, Inc. | `WSO` | `WSO-B` | `WSO` 在 `ext_active` | 仅 `WSO` 在 `cct` |
| `0000063754` | McCormick & Co. | `MKC` | `MKC-V` | `MKC` 在 `ext_active`，`MKC-V` 属 `ext_churned` | **双算**：两者均在 `cct(consumer_staples)` |
| `0001119639` | Petrobras | `PBR` | `PBR-A` | `PBR` 在 `ext_active` | **双算**：两者均在 `cct(integrated_oil)` |
| `0001160330` | Banco Bradesco | `BBD` | `BBDO` | `BBD` 在 `ext_active`，`BBDO` 属 `ext_churned` | **双算**：两者均在 `cct(mega_bank)` |
| `0001560385` | Formula One Group | `FWONK` | `FWONA` | `FWONK` 属 `ext_churned` | **双算**：两者均在 `cct(streaming_content)` |
| `0000012208` | Bio-Rad Labs | `BIO` | `BIO-B` | `BIO` 属 `ext_churned` | 仅 `BIO` 在 `cct` |
| `0002078416` | Liberty Live Group | `LLYVK` | `LLYVA` | `LLYVK` 属 `ext_churned` | 仅 `LLYVK` 在 `cct` |
| `0001614744` / 等 | Zillow / Under Armour / U-Haul / Molson Coors / Liberty Broadband / Andina | `Z` / `UAA` / `UHAL-B` / `TAP` / `LBRDK` / `AKO-B` | `ZG` / `UA` / `UHAL` / `TAP-A` / `LBRDA` / `AKO-A` | 均在 `$1B~$10B` `security_master` 层（未进 `ext_all`） | 未入 `cct` |

##### 情形 2：`security_master` 将公司更名/换代码记为 `secondary_share_class`（共 4 组，需识别为别名而非双股类）
- `PSTG -> P`（`CIK 0001474432`：Pure Storage 更名为 Everpure, Inc.，新代码 `P` 在 `ext_active`）。
- `BK -> BNY`（`CIK 0001390777`：纽约梅隆银行代码由 `BK` 切换为 `BNY`，`BNY` 在 `ext_active`，但 `BK` 和 `BNY` 同时留在 `cct`）。
- `EQR -> VMRK`（`CIK 0000906107`：Equity Residential / Vivmark Residential，`VMRK` 在 `ext_active` 且缺 `cct`，`EQR` 在 `ext_churned` + `cct`）。
- `HONA` / `HONAV`（`CIK 0002089271`：Honeywell Aerospace 分拆正股 vs When-Issued 临时代码，两者均在 `cct`）。

##### 情形 3：`security_master` 将优先股/次级债误标为 `secondary_share_class`（约 30 行）
- 例如 `STRC / STRD / STRK -> MSTR`（Strategy 永续优先股）、`SOJC / SOJD / SOJE / SOMN -> SO`（南方电力次级债/优先股）、`MGRB / MGRD / MGRE -> AMG`、`FITBI / FITBM -> FITB`、`KKRS -> KKR`、`APOS -> APO` 等。它们在 `security_master` 中 `eligible=0` 已被正确挡在 `extended_membership` 之外，但此前被错误导入了 `company_concept_tags`。

> **R4 架构结论**：
> 1. **篮子指数计算层（分母干净）**：严格限定 `security_master.eligible == 1` 且 `CIK` 级别唯一代表股（`representative_symbol`），彻底杜绝 ETF/CEF/优先股/双股类双算；同时修复 FMP screener 对多股类巨头（`GOOG`、`FOXA`、`NWSA`）的误调出，或对 `holdings(OPEN)` 中 `eligible=1` 美股自动纳入。
> 2. **持仓/观察池查询映射层（展示不断链）**：建立 `alias / share_class -> canonical_symbol` 映射（如查 `GOOGL` 命中 `GOOG` 的主题标签，查 `BRK-A` 命中 `BRK-B`）；对非美股（`01810`、`1548.HK`、`603259.SS`）和 ETF/CEF（`QQQ`、`DXYZ`）支持打标签供 PI 展示，但标记 `in_basket_calc = 0` 不参与美股篮子横截面统计。

---

## 2. 输入特征完备度实测（对应 R6/R8）

### 2.1 `market.db:company_profile` 覆盖率实测

| 评估集合 | 标的数 | 缺 `company_profile` | `sector` 为空 | `industry` 为空 | `description < 50` 字符 | `50 ≤ description < 150` 字符 |
|---|---:|---:|---:|---:|---:|---:|
| 活跃 Extended (`ext_active`) | **856** | **0** | **0** | **0** | **0** | **1** (`CIB`: 116 字符) |
| 历史累计 Extended (`ext_all`) | **1,011** | **0** | **0** | **0** | **0** | **2** (`CIB`: 116, `LLYVK`: 97) |
| 目标总并集 (`ext_active ∪ holdings ∪ watchlist`) | **882** | **10**（列于下） | **0** | **0** | **0** | **1** (`CIB`: 116) |
| 目标并集中的**美股单一公司候选**（剔除非美股及已知 ETF/CEF） | **872** | **2** (`GRAL`, `TEST`) | **0** | **0** | **0** | **1** (`CIB`: 116) |

- **缺失 `company_profile` 的全部 10 只标的完整清单**：
  - 非美股 7 只：`01810`, `03032`, `07709`, `09992`, `1548.HK`, `2268.HK`, `603259.SS`
  - 已退市/无档案 ETF 1 只：`DRAM`
  - 测试残留 1 只：`TEST`
  - **唯一真实美股缺口 1 只**：**`GRAL`**（GRAIL, Inc.，需调用 `FMPClient.get_profile("GRAL")` 补拉一次即可；另若纳入 `concept_watchlist.json` 则需同步补拉 `POET`）。

### 2.2 库内可用于辅助业务归属判定的其他表与字段

| 库 : 表 / 文件 | 行数 | 可用特征与对主题分类的实际价值 |
|---|---:|---|
| `market.db:company_profile` | 1,410 | JSON 含 `companyName`, `sector`, `industry`, `description`（99.9% ≥150 字）, `cik`, `isin`, `cusip`, `country`, `exchange`, `marketCap`, `isEtf`, `isFund`, `isAdr`, `isActivelyTrading`, `website` |
| `market.db:company_concept_tags` | 1,102 | 已有人工核验/LLM 生成的中文一句话 `business_role` 及 `manual` 锚点（17 行） |
| `config/concepts/company_concept_overrides.json` | — | 人工精校的高权重多业务巨头覆盖配置（如跨赛道科技巨头锚点） |
| `market.db:company_concept_evidence` | 11 | 已落地的 SEC EDGAR 10-K `Item 1. Business` 抓取表（含 `TEAM, OLED, VEEV, TWLO, FND` 等），证明通过 `company_profile.cik` 拉取 10-K 分部描述解决混合业务巨头归属的链路完全可行 |
| `market.db:fmp_etf_holdings_snapshot` | 16,676 | 截至 `2026-11-21` 的 `SOX`（费城半导体）、`IGV`（北美软件）、`QQQ`、`SPY`、`XLF` 成分权重快照，可作为半导体/软件纯度的客观外部交叉验证集 |
| `market.db:fmp_fund_disclosure_holdings` | 15,287 | `SOXX` 历史季报持仓披露明细 |
| `company.db:analyses` / `companies` | 161 / 168 | 40 只深度研究股票的 5-lens 报告与 `executive_summary` / `situation_summary`（含 `GRAL` 及港 A 股药明系/金斯瑞的深度业务拆解，可直接补足非美股无 FMP profile 的空白！） |

---

## 3. 现有分类分布尸检与 L2 重切数据底座（对应 R2/R3）

### 3.1 活跃 Extended（856 只）FMP Sector 与 Top Industry 分布底座

| FMP Sector | 活跃股票数 | 占比 | 内部主要 FMP Industry（只数） |
|---|---:|---:|---|
| `Technology` | **154** | 18.0% | `Software - Application`(36), `Semiconductors`(35), `Software - Infrastructure`(28), `IT Services`(15), `Hardware, Equipment & Parts`(13), `Computer Hardware`(11), `Communication Equipment`(8) |
| `Financial Services` | **139** | 16.2% | `Banks - Regional`(27), `Asset Management`(23), `Banks - Diversified`(19), `Insurance - P&C`(12), `Capital Markets`(11), `Credit Services`(11), `Data & Stock Exchanges`(9), `Insurance - Diversified`(7), `Insurance - Life`(7) |
| `Industrials` | **122** | 14.3% | `Industrial - Machinery`(26), `Aerospace & Defense`(21), `Specialty Business Services`(9), `Engineering & Construction`(8), `Integrated Freight & Logistics`(7), `Industrial - Distribution`(7), `Electrical Equipment & Parts`(6), `Airlines`(6), `Waste Management`(5) |
| `Healthcare` | **102** | 11.9% | `Biotechnology`(21), `Medical - Diagnostics & Research`(17), `Drug Manufacturers - General`(14), `Medical - Devices`(13), `Medical - Instruments & Supplies`(10), `Drug - Specialty & Generic`(7), `Healthcare Plans`(7) |
| `Consumer Cyclical` | **81** | 9.5% | `Specialty Retail`(19), `Restaurants`(9), `Auto - Manufacturers`(8), `Travel Services`(7), `Packaging & Containers`(6), `Residential Construction`(5), `Travel Lodging`(5) |
| `Energy` | **55** | 6.4% | `Oil & Gas Midstream`(16), `Oil & Gas E&P`(15), `Oil & Gas Integrated`(12), `Oil & Gas Equipment & Services`(5), `Oil & Gas Refining & Marketing`(4), `Solar`(2), `Uranium`(1) |
| `Basic Materials` | **49** | 5.7% | `Gold`(12), `Chemicals - Specialty`(9), `Construction Materials`(9), `Steel`(6), `Industrial Materials`(3), `Agricultural Inputs`(3), `Copper`(3) |
| `Consumer Defensive` | **45** | 5.3% | `Beverages - Non-Alcoholic`(8), `Packaged Foods`(7), `Household & Personal Products`(7), `Discount Stores`(6), `Beverages - Alcoholic`(4), `Tobacco`(3), `Food Distribution`(3) |
| `Utilities` | **40** | 4.7% | `Regulated Electric`(28), `Independent Power Producers`(4), `Diversified Utilities`(3), `Regulated Water`(3), `Regulated Gas`(2) |
| `Real Estate` | **38** | 4.4% | `REIT - Specialty`(9), `REIT - Residential`(8), `REIT - Healthcare`(5), `REIT - Industrial`(4), `REIT - Retail`(4), `Real Estate - Services`(4) |
| `Communication Services` | **31** | 3.6% | `Telecommunications Services`(16), `Internet Content & Information`(6), `Entertainment`(5), `Publishing`(2), `Advertising`(1), `Broadcasting`(1) |
| **合计** | **856** | **100.0%** | 共覆盖 **123 个**细分 FMP Industry |

---

### 3.2 现行 `concepts` 体系（11 L1 / 61 L2 / 34 L3）全量人数分布

> 活跃 Extended 856 只中已入 `company_concept_tags` 共 **837 只**（19 只未入库）。按活跃池统计，61 个 L2 严格呈：**`< 5` 只共 17 个**、**`5~25` 只共 34 个**、**`> 25` 只共 10 个**。

#### L1 分布总览（11 个）
`finance` (活跃 150 / 全表 231)、`industrial_aerospace` (120 / 155)、`consumer_retail` (105 / 115)、`pharma_life_sci` (102 / 124)、`energy_materials` (102 / 134)、`internet_software` (93 / 120)、`realestate_utility` (87 / 115)、`semiconductor` (42 / 55)、`ai_compute_cloud` (25 / 34)、`autonomy_robotics` (9 / 14)、`crypto` (2 / 5)。

#### 61 个 L2 全量分布表（按活跃 Extended 降序）

| 排名 | L2 `concept_id` | 中文名 | 所属 L1 | 活跃 Ext (837) | 全表 CCT (1102) | 规模诊断 |
|---:|---|---|---|---:|---:|---|
| 1 | `industrial_automation` | 工业自动化与机械 | `industrial_aerospace` | **51** | 62 | 🔴 臃肿 (`>25`) |
| 2 | `mega_bank` | 大型综合银行 | `finance` | **48** | 63 | 🔴 臃肿 (`>25`) |
| 3 | `integrated_oil` | 综合油气 | `energy_materials` | **47** | 56 | 🔴 臃肿 (`>25`) |
| 4 | `power_utility` | 电力与公用 | `realestate_utility` | **40** | 54 | 🔴 臃肿 (`>25`) |
| 5 | `consumer_staples` | 必需消费品 | `consumer_retail` | **37** | 41 | 🔴 臃肿 (`>25`) |
| 6 | `insurance` | 保险 | `finance` | **36** | 45 | 🔴 臃肿 (`>25`) |
| 7 | `enterprise_saas` | 企业SaaS | `internet_software` | **36** | 39 | 🔴 臃肿 (`>25`) |
| 8 | `residential_commercial_reit` | 住宅与商业REIT | `realestate_utility` | **31** | 43 | 🔴 臃肿 (`>25`) |
| 9 | `aerospace_defense` | 航空航天与国防 | `industrial_aerospace` | **29** | 38 | 🔴 臃肿 (`>25`) |
| 10 | `consumer_discretionary_brand` | 可选消费品牌 | `consumer_retail` | **28** | 31 | 🔴 臃肿 (`>25`) |
| 11 | `asset_management` | 资产管理 | `finance` | 23 | **62** | 🟢 (全表含 27 只共同基金污染) |
| 12 | `payment_fintech` | 支付与金融科技 | `finance` | 23 | 27 | 🟢 适中 |
| 13 | `medical_device` | 医疗器械 | `pharma_life_sci` | 23 | 26 | 🟢 适中 |
| 14 | `mining_metals` | 矿业与金属 | `energy_materials` | 22 | 30 | 🟢 (可拆贵金属 vs 工业金属/铀) |
| 15 | `large_pharma` | 大型制药 | `pharma_life_sci` | 22 | 24 | 🟢 适中 |
| 16 | `innovative_biotech` | 创新生物科技 | `pharma_life_sci` | 21 | 35 | 🟢 适中 |
| 17 | `health_services` | 医保健康服务 | `pharma_life_sci` | 19 | 22 | 🟢 适中 |
| 18 | `chemicals_gases` | 化工与气体 | `energy_materials` | 18 | 23 | 🟢 适中 |
| 19 | `telecom_operator` | 电信运营商 | `internet_software` | 17 | 24 | 🟢 (混入 `ERIC` 设备商) |
| 20 | `life_sci_tools_cro` | 生命科学工具与CRO | `pharma_life_sci` | 17 | 17 | 🟢 适中 |
| 21 | `logistics_shipping` | 物流与航运 | `industrial_aerospace` | 16 | 19 | 🟢 适中 |
| 22 | `big_box_retail` | 大型零售 | `consumer_retail` | 16 | 16 | 🟢 适中 |
| 23 | `investment_bank` | 投资银行与交易 | `finance` | 13 | 27 | 🟢 (混入 `CRCL` 稳定币) |
| 24 | `datacenter_network` | 数据中心网络设备 | `ai_compute_cloud` | 13 | 16 | 🟡 混装(服务器整机+光模块+交换机) |
| 25 | `engineering_construction` | 工程与建筑 | `industrial_aerospace` | 12 | 22 | 🟢 适中 |
| 26 | `consumer_internet_platform` | 消费互联网平台 | `internet_software` | 11 | 14 | 🟢 适中 |
| 27 | `industrial_materials` | 工业建材 | `realestate_utility` | 11 | 13 | 🟡 混入铁矿/铜巨头 `BHP, RIO, VALE, TECK` |
| 28 | `cybersecurity` | 信息安全 | `internet_software` | 11 | 11 | 🟢 纯正 |
| 29 | `semi_equipment` | 半导体设备 | `semiconductor` | 10 | 14 | 🟢 (混入 `COHR` 光模块龙头) |
| 30 | `restaurant` | 餐饮 | `consumer_retail` | 10 | 13 | 🟢 纯正 |
| 31 | `ip_eda_fabless` | 芯片IP/EDA/Fabless其他 | `semiconductor` | 9 | 14 | 🟡 混装(AI ASIC `AVGO/MRVL`+EDA+互连 `ALAB/CRDO`+封测 `ASX`) |
| 32 | `analog_power` | 模拟与功率 | `semiconductor` | 9 | 12 | 🟢 纯正 |
| 33 | `streaming_content` | 流媒体与内容 | `internet_software` | 8 | 18 | 🟢 适中 |
| 34 | `ev_oem` | 电动车整车 | `autonomy_robotics` | 8 | 11 | 🟡 含传统车企 `GM, F, TM, HMC, STLA` |
| 35 | `broker_exchange` | 经纪与交易所 | `finance` | 7 | 7 | 🟢 纯正 |
| 36 | `agriculture_fertilizer` | 农产与化肥 | `energy_materials` | 6 | 10 | 🟢 纯正 |
| 37 | `airlines` | 航空公司 | `industrial_aerospace` | 6 | 8 | 🟢 纯正 |
| 38 | `heavy_machinery` | 重型机械与农机 | `industrial_aerospace` | 6 | 6 | 🟢 纯正 |
| 39 | `gaming_interactive` | 游戏与互动娱乐 | `internet_software` | 5 | 7 | 🟢 压线 |
| 40 | `hyperscaler` | 超大规模云 | `ai_compute_cloud` | 5 | 6 | 🟢 压线 (`AMZN, MSFT, ORCL, BABA, NBIS`；缺 `GOOG`) |
| 41 | `comm_infra_reit` | 通信基建REIT | `realestate_utility` | 5 | 5 | 🟢 压线 |
| 42 | `consumer_electronics_brand` | 消费电子与品牌硬件 | `consumer_retail` | 5 | 5 | 🟢 压线 |
| 43 | `oil_services` | 油服与设备 | `energy_materials` | 5 | 5 | 🟢 压线 |
| 44 | `oversea_ecommerce_platform` | 海外电商平台 | `consumer_retail` | 5 | 5 | 🟢 压线 |
| 45 | `clean_energy` | 新能源 | `energy_materials` | **4** | 10 | 🔵 过碎 (`<5`) |
| 46 | `ai_platform_saas` | AI平台与数据SaaS | `ai_compute_cloud` | **4** | 6 | 🔵 过碎 (`<5`) |
| 47 | `memory_chip` | 存储芯片 | `semiconductor` | **4** | 5 | 🔵 过碎 (`<5`) |
| 48 | `foundry` | 晶圆代工 | `semiconductor` | **4** | 4 | 🔵 过碎 (`<5`) |
| 49 | `home_improvement` | 家居建材零售 | `consumer_retail` | **4** | 4 | 🔵 过碎 (`<5`) |
| 50 | `cpu_soc` | CPU与SoC | `semiconductor` | **3** | 3 | 🔵 过碎 (`<5`) |
| 51 | `gpu_accelerator` | 计算芯片/GPU加速器 | `semiconductor` | **3** | 3 | 🔵 过碎 (`<5`) |
| 52 | `datacenter_reit` | 数据中心运营 | `ai_compute_cloud` | **2** | 4 | 🔵 过碎 (`<5`) |
| 53 | `ecommerce_saas` | 电商SaaS | `internet_software` | **2** | 2 | 🔵 过碎 (`<5`) |
| 54 | `social_media` | 社交媒体 | `internet_software` | **2** | 2 | 🔵 过碎 (`<5`) |
| 55 | `crypto_treasury` | 加密储备/敞口 | `crypto` | **1** | 4 | 🔵 过碎 (`<5`) |
| 56 | `search_engine` | 搜索引擎 | `internet_software` | **1** | 3 | 🔵 过碎 (`<5`) |
| 57 | `datacenter_power_cool` | 数据中心电力与冷却 | `ai_compute_cloud` | **1** | 2 | 🔵 过碎 (`<5`) |
| 58 | `crypto_exchange` | 加密交易所/经纪 | `crypto` | **1** | 1 | 🔵 过碎 (`<5`) |
| 59 | `industrial_medical_robotics` | 工业/医疗机器人 | `autonomy_robotics` | **1** | 1 | 🔵 过碎 (`<5`) |
| 60 | `autonomy_perception` | 自动驾驶感知系统 | `autonomy_robotics` | **0** | 2 | ⚫ 空桶 (`<5`) |
| 61 | `crypto_mining` | 加密挖矿 | `crypto` | **0** | 0 | ⚫ 空桶 (`<5`) |

#### 34 个 L3 实测现状（列级断层与语义混杂）
- `company_concept_tags.tertiary_concept_id` 列：**837/837（100%）为 `NULL`**（全表 1,102 行同样 100% `NULL`）。
- 实际 L3 存储在 JSON 文本列 `theme_ids` 中（活跃池 **294/837 = 35.1%** 至少带 1 个 L3），人数 Top 10 依次为：`rate_sensitive`(78)、`defensive_stock`(48)、`ai_compute`(45)、`semi_cycle`(37)、`ai_application_layer`(37)、`tariff_exposure`(33)、`realestate_cycle`(31)、`industrial_capex`(30)、`china_exposure`(25)、`geopolitics`(23)；而真正具交易辨识度的产业题材如 `optical_comms`(5)、`datacenter_cooling`(5)、`nuclear_revival`(5)、`inhouse_chip`(2)、`commercial_space`(1) 覆盖极稀疏（且旧表 `concept_themes` 残留 5 行 `hbm/liquid_cooling/ai_pc/nuclear_revaluation/silicon_photonics` 已在代码中标记 `@deprecated`）。

---

### 3.3 17 个 `< 5` 只过碎 L2 逐一拆解与合并去向建议

| 过碎 L2 (`活跃数`) | 当前池内全部活跃个股（及非活跃残留） | 根因诊断与建议合并/重组方向 |
|---|---|---|
| `crypto_mining` (`0`) | 无（`IREN` 在 `datacenter_reit` 调出，`CIFR/WULF/RIOT` 未达 `$10B`） | 与 `crypto_exchange`(`COIN`)、`crypto_treasury`(`MSTR`) 及 `CRCL`（现误放 `investment_bank`）、`HOOD`（多归属）合并为统一的 **`crypto_digital_assets`（加密与数字资产，约 6~8 只含观察池/中盘弹性票）** |
| `autonomy_perception` (`0`) | 活跃 0（调出：`APTV, AUR`） | 并入 **`autos_autonomy_ev`（汽车、智驾与出行硬件）** |
| `crypto_exchange` (`1`) | `COIN` | 并入 **`crypto_digital_assets`（加密与数字资产）** |
| `crypto_treasury` (`1`) | `MSTR`（非活跃残留为 3 只优先股 `STRC/F/K`） | 并入 **`crypto_digital_assets`（加密与数字资产）** |
| `datacenter_power_cool` (`1`) | 仅 **`VRT`** 1 只！（调出 `FPS`） | **典型漏抓而非赛道小**：同逻辑的电气/液冷/暖通票被散落在 `industrial_automation`（`ETN, HUBB, NVT, TT, CARR, JCI, GNRC`）和 `engineering_construction`（`PWR, EME, FIX`）。聚拢后可形成 **10~14 只纯正「数据中心供电、电网设备与温控」独立 L2**！ |
| `industrial_medical_robotics` (`1`) | 仅 `SYM` 1 只 | 医疗机器人 `ISRG` 在医疗器械，工业自动化 `ROK/EMR/CGNX/TER` 在工业/半导体；建议 `SYM` 并入 **`factory_warehouse_automation`（工业与仓储自动化）**，跨赛道机器人用多标签/主题篮子表达 |
| `search_engine` (`1`) | 仅 `BIDU`（`GOOG` 因 Screener 抖动掉出） | 单独设“搜索引擎”仅 2 家公司；建议与 `social_media`(`META, RDDT`) 及数字广告/内容平台合并为 **`digital_ads_social_search`（数字广告、社交与搜索平台，约 10~14 只）** |
| `social_media` (`2`) | `META, RDDT` | 同上，并入 **`digital_ads_social_search`** |
| `ecommerce_saas` (`2`) | `SHOP, GDDY` | 仅 2 只；建议 `SHOP` 入电商/互联网平台或应用软件，`GDDY`（与 `VRSN`）入互联网基础设施/企业 SaaS |
| `datacenter_reit` (`2`) | `DLR, EQIX`（非活跃 `APLD, IREN`） | 与 `comm_infra_reit`（`AMT, CCI, SBAC` 等 5 只）及 `IRM`（现误放住宅商业 REIT）合并为 **`digital_comm_infra_reit`（数据中心与通信基建 REIT，8~10 只）** |
| `gpu_accelerator` (`3`) | `NVDA, AMD, CBRS` | 与 `cpu_soc`（`INTC, QCOM`）及现被塞在 `ip_eda_fabless` 的定制 AI ASIC/架构龙头（`AVGO, MRVL, ARM`）重组为 **`ai_compute_logic_chips`（AI 算力与核心计算芯片，8~10 只）**；嵌入式 MCU `MCHP` 归回 `analog_power` |
| `cpu_soc` (`3`) | `INTC, QCOM, MCHP` | 同上：`INTC, QCOM` 入核心计算/SoC，`MCHP` 入 `analog_power`（与 `NXPI, STM, TXN` 同属 MCU/模拟） |
| `foundry` (`4`) | `TSM, GFS, UMC, TSEM` | 加入现误放在 `ip_eda_fabless` 的封测龙头 `ASX`（日月光）及 `AMKR`，或与 `semi_equipment` 协同形成 **`foundry_osat`（晶圆代工与先进封装，5~6 只）** 或 **`semi_manufacturing_equipment`（晶圆制造、封测与设备，15~16 只）** |
| `memory_chip` (`4`) | `MU, SNDK, WDC, STX` | 可加入存储阵列/全闪存 `NTAP`、`P`（Everpure/原 Pure Storage，现误放在 `industrial_automation`！）组成 **`memory_enterprise_storage`（存储芯片与企业级存储，6~7 只）** |
| `ai_platform_saas` (`4`) | `PLTR, SNOW, MDB, NTNX` | 与 `enterprise_saas` 中的云原生数据/可观测/DevOps 基础设施（`DDOG, DT, TWLO, FROG, CFLT/IOT` 等）合并为 **`cloud_data_devops_ai`（云数据平台、AI 软件与可观测性，10~14 只）** |
| `clean_energy` (`4`) | `GEV, FSLR, NXT, BE`（非活跃 `OKLO, BEP, ENLT`） | 光伏/储能与燃气轮机/燃料电池/核电重估并入 **`power_gen_nuclear_clean`（独立发电、核电与新型电力装备，8~12 只）** |
| `home_improvement` (`4`) | `HD, LOW, FERG, MAS` | 与住宅建筑商 `DHI, LEN, PHM, NVR, TOL`（现分散在 `residential_commercial_reit`、`engineering_construction`、`consumer_discretionary_brand`！）及家居建材合并为 **`us_housing_home_improvement`（北美住宅建造与家居建材链，10~14 只）** —— 完美对齐利率与地产开工同一交易因子！ |

---

### 3.4 10 个 `> 25` 只臃肿 L2 内部构成尸检与重切验证（目标 6~25 只）

以下基于活跃 Extended 实测 FMP `industry` 与代表股拆解，验证每个大桶能否按机构真实交易逻辑切入 6~25 只黄金区间：

1. **`industrial_automation`（51 只）—— 最严重的“大杂烩垃圾桶”**：
   - **实测内部构成**：`Industrial - Machinery`(20: `PH, TT, EMR, ITW, CARR, ROK, IR, OTIS, DOV, XYL, GNRC...`)、`Industrial - Distribution`(5: `GWW, WCC, WSO, QXO, AIT`)、`Waste Management`(5: `WM, RSG, WCN, CLH, GFL`)、`Electrical Equipment`(4: `ETN, AME, HUBB, NVT`)、`Hardware, Equipment & Parts`(4: `APH, FLEX, FTV, TRMB`)、`Specialty Business Services`(3: `CTAS, ULS, RTO`)、以及误入的 `TEL`(连接器)、`P`(Everpure 存储！)、`ROP`(工业软件合集)、`JCI`(楼宇暖通)。
   - **干净重切方案（4 个高纯度赛道）**：
     - **电气电网、数据中心供电与暖通温控（~12 只）**：`ETN, HUBB, NVT, VRT, TT, CARR, JCI, GNRC` + 电网工程 `PWR, EME, FIX`。
     - **核心工业自动化、流体控制与精密仪器（~18~20 只）**：`PH, EMR, ROK, ITW, IR, AME, FTV, DOV, XYL, NDSN, IEX, VLTO, ZBRA, TRMB, OTIS, MMM`。
     - **环保固废与商业路线服务（8~9 只）**：`WM, RSG, WCN, CLH, GFL, CTAS, RTO, ULS, ROL`。
     - **工业 MRO 分销与专业工具（8~9 只）**：`GWW, WCC, WSO, QXO, AIT, SNA, LECO, SWK`。
     - **纠偏迁出**：`APH, TEL` 迁入「光通信与高速互连」；`P` 迁入「存储」；`ROP` 迁入「工业与垂直软件」。

2. **`mega_bank`（48 只）—— 货币中心大行与区域银行混装**：
   - **实测内部构成**：`Banks - Diversified`(19: `JPM, BAC, WFC, C, HSBC, RY, MUFG, TD, UBS, SAN, BBVA, BMO, BNS, ING, SMFG...`)、`Banks - Regional`(26: `USB, PNC, TFC, FITB, MTB, HBAN, CFG, FCNCA, RF, KEY, EWBC, PNFP, FHN` + 海外大型商业银行 `HDB, IBN, MFG, LYG, DB, NWG, BBD, ITUB`)、以及误入的按揭/金融科技 `RKT`、`CHYM`。
   - **干净重切方案**：
     - **全球系统重要性综合大行 / 货币中心银行（~20~22 只）**：`JPM, BAC, WFC, C` + 全球综合大行（`HSBC, RY, TD, MUFG, SMFG, MFG, UBS, DB, SAN, LYG, NWG...`）。
     - **北美区域银行与商业银行（~18~20 只）**：`USB, PNC, TFC, FITB, MTB, HBAN, CFG, FCNCA, RF, KEY, EWBC, PNFP, FHN` 等（严格受美债曲线与 CRE 区域信贷周期驱动，KRE 交易逻辑）。
     - `RKT, CHYM` 迁回 `payment_fintech`。

3. **`integrated_oil`（47 只）—— 上游勘探、中游管道/LNG、炼化一锅炖**：
   - **实测内部构成**：`Oil & Gas Midstream`(16: `ENB, WMB, EPD, KMI, TRP, LNG, TRGP, MPLX, OKE, VG, PBA, CQP, PAA, WES...`)、`Oil & Gas E&P`(15: `COP, CNQ, EOG, OXY, FANG, DVN, WDS, EQT, EXE, TPL, PR, OVV, APA, RRC, AR`)、`Oil & Gas Integrated`(12: `XOM, CVX, SHEL, TTE, BP, PBR, EQNR, E, SU, CVE, EC, YPF`)、`Refining & Marketing`(4: `MPC, PSX, VLO, DINO`)。
   - **干净重切方案**：
     - **中游油气管道与 LNG 基础设施（16 只）**：长约收费现金流 + 天然气/数据中心气电逻辑，与油价 β 显著分化。
     - **综合石油巨头与上游油气勘探 E&P（27 → 可微调拆为「国际综合油气巨头 12 只」+「北美独立页岩油气与天然气上游 15 只」，或合并上游约 25 只）**。
     - **下游炼化与油服工程（9 只）**：4 只炼厂（`MPC, PSX, VLO, DINO`）+ 原 `oil_services` 5 只（`SLB, BKR, HAL, FTI, NOV`）。

4. **`power_utility`（40 只）—— AI 核电/独立发电商与防御收息公用事业混装**：
   - **实测内部构成**：`Regulated Electric`(28: `NEE, SO, DUK, AEP, D, XEL, ETR, EXC, ED, PEG, PCG...`)、`Independent Power Producers`(4: `CEG, VST, NRG, TLN`)、`Diversified Utilities`(3: `SRE, BIP, AES`)、`Regulated Water`(3: `AWK, SBS, WTRG`)、`Regulated Gas`(2: `ATO, NI`)。
   - **干净重切方案**：
     - **核电、独立发电商（IPP）与清洁电力装备（~9~11 只）**：`CEG, VST, NRG, TLN` + `CCJ`(铀矿) + `GEV, BE, FSLR, NXT`（及观察池 `OKLO`）—— 高 β AI 缺电主线。
     - **受监管电网公用事业（~25 只）** 与 **燃气/水务/基建公用事业（~8~10 只）**（防御性美债久期替代资产）。

5. **`consumer_staples`（37 只）**：
   - **实测内部构成**：非酒精饮料(8: `KO, PEP, MNST, CCEP, KDP...`) + 酒精饮料/酒类(5: `BUD, DEO, STZ, ABEV, BF-B`) + 烟草(3: `PM, BTI, MO`) = **16 只**；包装食品/糖果/食材分销(12: `KHC, GIS, MDLZ, HSY, MKC, HRL, SJM, SYY, USFD, PFGC...`) + 日化家清个护(7: `PG, UL, CL, EL, KMB, CHD, CLX`) = **19 只**（另 `ROL` 迁商业服务、`SGI` 迁家居）。
   - **干净重切方案**：拆为 **「饮料、酒类与烟草（16 只）」** vs **「家庭个护与包装食品（19 只）」**，两桶均稳居 15~20 只区间。

6. **`insurance`（36 只）**：
   - **实测内部构成**：财险 P&C(12: `CB, PGR, TRV, ALL, WRB, CINF...`) + 多元财险/再保(10: `BRK-B, AIG, ACGL, HIG, EG, RNR...`) = **~22 只**；寿险与养老资管(8: `MFC, AFL, MET, PRU, PUK, UNM, GL, RGA`) + 保险经纪与风险咨询(5: `MRSH, AJG, AON, WTW, BRO`) + 产权险(`FNF`) = **~14 只**。
   - **干净重切方案**：拆为 **「财产险、特种险与再保险（~21~22 只）」** vs **「寿险养老与保险经纪咨询（~14~15 只）」**。

7. **`enterprise_saas`（36 只）**：
   - **实测内部构成**：应用软件 SaaS(19: `SAP, CRM, NOW, ADBE, INTU, ADP, WDAY, ADSK, VEEV, TEAM, DDOG, DT, PTC, TYL, FICO, APP...`)、IT 咨询与外包/分销(9: `IBM, ACN, INFY, CTSH, WIT, GIB, IT, CDW, SNX`)、基础设施/垂直软件(4: `VRSN, TWLO, IOT, TOST`)、专业数据信息服务(4: `RELX, TRI, VRSK, CSGP`)。
   - **干净重切方案**：
     - **企业核心工作流与垂直应用 SaaS（~18~20 只）**：`SAP, CRM, NOW, ADBE, INTU, ADP, WDAY, ADSK, VEEV, TEAM, PTC, TYL, GWRE, TOST, IOT, DOCU, PAYC, MANH, PCOR, BSY`（含补齐未入库的活跃 SaaS 票）。
     - **云数据平台、可观测性与开发基础设施（~10~12 只）**：与 `ai_platform_saas` 合并（`PLTR, SNOW, MDB, DDOG, DT, TWLO, NTNX, FROG` 等）。
     - **IT 咨询集成与商业数据信息服务（~13~15 只）**：`IBM, ACN, INFY, CTSH, WIT, GIB, IT, CDW, SNX` + 商业数据局 `RELX, TRI, VRSK, FICO, CSGP`（若信息征信与 `EFX, TRU, SPGI, MCO, MSCI` 合流则更纯粹！）。

8. **`residential_commercial_reit`（31 只）**：
   - **实测内部构成**：住宅租赁 REIT(7) + 住宅建筑商(3: `PHM, LEN, NVR`，误放入 REIT！) + 医疗健康 REIT(5: `WELL, VTR...`) + 工业仓储 REIT(4: `PLD, PSA, EXR, EGP`) + 零售/净租赁/体验 REIT(7: `SPG, O, KIM, REG, VICI, WPC, HST`) + 地产服务(3: `CBRE, JLL, BEKE`) + `IRM`(数据中心/档案 REIT) + `NLY`(mREIT)。
   - **干净重切方案**：住宅建筑商迁入「北美住宅建造与家居建材链」，`IRM` 迁入「数据中心与通信基建 REIT」，剩余 **~25 只传统商业/住宅/医疗/物流 REIT 与地产服务** 自然落入 ≤25 只区间（或拆「住宅与自助仓储 REIT」vs「商业、医疗与工业 REIT」各 ~12~14 只）。

9. **`aerospace_defense`（29 只）**：
   - **实测内部构成**：传统航空发动机与军工主承包商/零部件(21: `GE, RTX, LMT, BA, GD, NOC, HWM, TDG, HON, LHX, HEI...`) + 商业航天与太空经济(`SPCX, RKLB` + 调出/持仓 `ASTS`) + 国防 IT/公共安全电子(`AXON, MSI, LDOS, CACI, TDY`) + 航空钛合金特种材料(`ATI, CRS`)。
   - **干净重切方案**：可将 `ATI, CRS` 归回特种金属，或拆分为 **「航空制造与国防军工核心链（~20 只）」** + **「商业航天、无人机与国防科技安防（~8~10 只：`SPCX, RKLB, ASTS, JOBY, AXON, MSI, LDOS, CACI, TDY, ONDS`）」**。

10. **`consumer_discretionary_brand`（28 只）**：
    - **实测内部构成**：酒店(5: `MAR, HLT, IHG, H, HTHT`) + 邮轮旅游(3: `RCL, CCL, VIK`) + 博彩度假(2: `LVS, FLUT`) = **10 只出行文旅**；鞋服运动奢侈品(8: `NKE, LULU, DECK, ONON, RL, TPR, AS, ROST, BURL`) + 汽车经销/售后/豪华车/家居工具(8) = **18 只商品零售品牌**（且 `TOL` 豪宅建筑商应迁入住宅链）。
    - **干净重切方案**：酒店+邮轮+博彩与在线旅游 OTA（`BKNG, ABNB, EXPE, TCOM`）合并为 **「旅游酒店、邮轮与博彩出行（~14~16 只）」**；鞋服运动奢侈品与品牌专卖独立为 **「运动服饰、奢侈品与可选消费品牌（~15~18 只）」**。

#### 专项核验：「光通信与高速互连」vs「AI 服务器与企业硬件」重切底座
实测发现，当前市场核心主线「光模块 / 高速铜缆互连 / 数据中心网络交换」被撕裂在 5 个桶中：
- **可独立成桶的「光通信、高速互连与数据中心网络」（活跃池已达 13~15 只）**：
  - 光模块/光器件/光纤：`LITE` ($41.8B), `COHR` ($36.9B, 现误放 `semi_equipment`), `CIEN` ($33.2B), `GLW` ($81.3B), `FN` ($10.2B)（外加观察池 `POET, AXTI`）。
  - 高速铜缆/连接器与 SerDes/Retimer 互连芯片：`APH` ($172.2B, 现放 `industrial_automation`), `TEL` ($57.4B, 现放 `industrial_automation`), `ALAB` ($31.5B, 现放 `ip_eda_fabless`), `CRDO` ($18.8B, 现放 `ip_eda_fabless`), `MTSI` ($15.0B, 现放 `analog_power`)（外加 `SMTC, MXL`）。
  - 数据中心交换机与电信网络设备：`ANET` ($211.8B), `CSCO` ($357.3B), `NOK` ($37.2B), `ERIC` ($32.4B), `UI` ($35.7B)。
- **从 `datacenter_network` 剥离出的「AI 服务器整机、ODM 与企业硬件」（约 8~10 只）**：
  `DELL` ($176.9B), `HPE` ($54.3B), `SMCI` ($14.3B), `CLS` ($25.4B), `JBL` ($23.2B), `FLEX` ($24.5B), `NTAP` ($31.0B)（可与 `AAPL, LOGI, HPQ` 或存储硬件协同）。

---

## 4. 下游兼容契约与 `prosperity` 哈希红线核查（对应 R10/R11/R12）

### 4.1 `ConceptClassifier` 下游完整调用契约矩阵

工厂入口：`terminal/concept_classifier.py:323` `get_report_concept_classifier() -> ConceptClassifier`。

| 下游文件 | 调用行号 | 调用的属性 / 方法（含私有方法） | 精确输入与返回签名 | 实际业务语义与重构契约要求 |
|---|---|---|---|---|
| `scripts/morning_report.py` | `L93`, `L96` | `clf._active_bucket_order()` → 赋给模块级常量 **`CONCEPT_BUCKET_ORDER`** | `() -> list[str]` | ⚠️ **模块 import 期立即求值**！DB 可用且非空时返回全部有序 L2 中文名列表（现 61 个），否则回退 legacy 15 桶顺序。用于 `L1866` 排序及 `L2750, L2782, L2789, L2936, L2938` PNG 视觉表格分桶渲染 |
| `scripts/morning_report.py` | `L188` | `clf.display_tags(item)` | `(item: dict \| str) -> str` | 返回 `'L1中文 / L2中文 / L3中文'`（`' / '` 分隔），未注册标的回退 `classify(item)` 单桶名 |
| `scripts/morning_report.py` | `L311` | `clf.classify(item)` | `(item: dict \| str) -> str` | Legacy 粗粒度 15 桶兜底（仅当 `display_tags` 为空或未注册时在 `L191` 兜底） |
| `scripts/morning_report.py` | `L315` (经 `_grouping_bucket_for`) | **`clf._grouping_bucket(item)`**（私有方法被直接调用！） | `(item: dict \| str) -> str` | 共 **10 处调用**（`L1877, 2261, 2275, 2426, 2435, 2451, 2668, 2672, 2689, 2693`）。返回标的所属 L2 中文名；缺 L2 时经 `_CONCEPT_TO_LEGACY_BUCKET` 映射 L1 → legacy 桶名 → `classify(item)` |
| `scripts/morning_report.py` | `L378` | `clf.group_items(items)` | `(items: list[dict]) -> OrderedDict[str, list[dict]]` | 按 `_active_bucket_order()` 顺序聚合非空桶，未在主序列表中的 fallback 桶追加在末尾（trailing-extras） |
| `terminal/morning_html_report.py` | — | **零直接调用** | — | 完全接收 `morning_report.py` 预组装好的字段与分组，无独立概念库耦合 |
| `scripts/portfolio_intelligence.py` | `L618`, `L691` | **`classifier.bucket_order`** | `list[str]`（属性） | ⚠️ **注意与晨报差异**：PI 读的是 **粗粒度 15 桶** `bucket_order`（而非 61 桶 `_active_bucket_order()`），用于持仓主题暴露表排序 |
| `scripts/portfolio_intelligence.py` | `L619`, `L644`, `L680` | **`classifier.classify(item)`** | `(item: dict) -> str` | ⚠️ PI 传入 `{symbol, companyName, shortName, longName, sector, industry}`（**不含 `description`！**），返回粗粒度 15 桶名，并在 **`L719` 检查 `largest_theme['tracked_pct'] >= 0.40` 触发 `≥40% NAV` 集中度风控提示**！重构后 PI 的 `classify()` 必须改为「优先从 DB 新分类表的 L1（约 10~12 个一级大类）映射粗桶」，既消除旧关键字猜测，又保持 40% NAV 集中度风控口径不被 L2 切碎！ |
| `scripts/portfolio_intelligence.py` | `L620`, `L645`, `L803`, `L823` | **`classifier.business_role(item)`** | `(item: dict) -> str` | 优先从注册表返回单句中文业务角色（渲染在股票持仓表与期权持仓表第 2 列），未命中回退 legacy 规则 |

---

### 4.2 `prosperity` 引擎 `code_version` 静态检查与冻结链红线（`tests/test_prosperity_schemes.py`）

#### 源码机制精读
1. **字节级文件哈希（`terminal/prosperity/version.py:17-49`）**：
   - `CODE_VERSION_SOURCES` 显式包含 **`"src/data/market_store.py"`**（L24）与 **`"config/settings.py"`**（L28）。
   - `code_version()` 对列表内所有 `.py` / `.json` 文件执行 `path.read_bytes()` SHA256 摘要（当前仓库实测值：`4c9da30bd5001102`）。**哪怕删改一行注释、删一段废弃 DDL 或删 `THEME_KEYWORDS_SEED` 字典，都会立刻改变 `code_version()`！**
2. **AST 本地依赖静态门控（`tests/test_prosperity_schemes.py:110-135`）**：
   - `test_every_local_import_is_hashed_or_explicitly_excluded()` 用 `ast.walk` 扫描 `CODE_VERSION_SOURCES` 内所有 `.py` 文件的全部顶层及函数内局部 `import` / `from ... import`。
   - 只要首段包名属于 `("src", "terminal", "config", "scripts")`，目标模块路径**必须**位于 `CODE_VERSION_SOURCES` 或 `CODE_VERSION_EXCLUDED` 字典中，否则单测立即失败！
3. **对 `data/prosperity.db` 实盘冻结链的连锁影响（`src/data/prosperity_run.py:76-84, 383-395`）**：
   - 实测本地 `data/prosperity.db` 存有 `params`（184 行）、`board`（352 行）、`factor_value`（2,896,440 行）。
   - `run_week()` 每周六运行时，会按当前 `code_version()` 查询 `_frozen(store, version)` 并调用 `rebuild_freeze_chain()`（L383）。一旦 `code_version()` 变化，新版本号下历史季度冻结链为空，**下一次周更会自动从 `2021Q2` 起重跑全部约 22 个季度的 `quarter_final` 冻结板（新增 ~88 张 board、~200 万行因子明细，耗时显著，受 `timeout_seconds=7200` 约束）**；且任何绑定旧 `code_version` 的冻结参数包在直接调用 `standardize(..., frozen=...)` 时会抛 `ValueError("params_code_mismatch")`（见 `test_prosperity_schemes.py:151`）。

#### 精确合规演进路径（两阶段法）
- **阶段一（新建独立主题篮子模块 + 切换晨报/PI，零触碰红线）**：
  1. 新建代码放入独立目录（如 `terminal/theme_baskets/`、`src/data/theme_basket_store.py`、`config/theme_baskets/`），**绝不修改 `src/data/market_store.py` 与 `config/settings.py`**，也**绝不在 `CODE_VERSION_SOURCES` 涵盖的任何文件内 `import` 新模块**。
  2. 新表建议存储在独立 SQLite 文件（如 `data/theme_baskets.db` 或由 `theme_basket_store.py` 独立连接维护 DDL，不往 `market_store.py` 塞 DDL），此时 `code_version()` 恒定保持 `4c9da30bd5001102`，`test_prosperity_schemes.py` 零改动全绿，`prosperity.db` 零重算。
- **阶段二（未来统一清理旧概念表 DDL 与 `THEME_KEYWORDS_SEED`）**：
  1. **前置解耦清单**（缺一不可）：
     - `THEME_KEYWORDS_SEED` 现有全部调用点：`terminal/concept_classifier.py:102`、`scripts/scan_themes.py:33,147,153`、`tests/test_scan_themes.py:217`。
     - `market_store.py` 旧概念 DDL（`L457-525`、`L864-865`）与读写方法（`L3168-3411`）现有调用点：`terminal/concept_classifier.py`、`scripts/build_company_concept_registry.py`、`tests/test_market_store_concepts.py`、`tests/test_concept_v2_integration.py`。
     - **注意库内 3 张“幽灵旧表”**：实测 `data/market.db` 中还存在 `company_concept_candidates`（0 行）、`company_concept_evidence`（11 行）、`concept_override_log`（0 行），它们的 `CREATE TABLE` 甚至不在当前 `market_store.py` 源码中（属历史迁移残留），云端删表脚本需一并显式 `DROP TABLE IF EXISTS`。
  2. **合并单次提交**：将 `market_store.py` 与 `settings.py` 的旧代码删除合并在同一笔 commit 中，使 `code_version()` **只跃迁一次**，并在云端周六 16:30 cron 前主动执行一次 `scripts/prosperity_weekly.py --rebuild-chain --db data/prosperity.db` 预热冻结链。

---

## 5. 云端运行环境与可用 LLM 接口盘点（对应 R8）

### 5.1 `broad_universe_cron_wrapper.sh` Step 7 锁机制与错误处理审计

核查 `scripts/broad_universe_cron_wrapper.sh`（共 117 行）与 `scripts/build_company_concept_registry.py:988-1176`：

1. **调用时机与命令（`L96-109`）**：
   - 运行在周六 `09:00` 的 `weekly_refresh` 分支末尾，紧跟 Step 6 `refresh_extended` 之后：
     ```bash
     concept_rc=0
     run_step_with_market_writer_lock 0 "concept_weekly_sync" "$PYTHON" \
       scripts/build_company_concept_registry.py --weekly-sync --scheduled || concept_rc=$?
     if [ "$concept_rc" -ne 0 ]; then
       log "WARN concept_weekly_sync rc=$concept_rc (nonblocking, continuing)"
     fi
     ```
2. **文件锁机制隐患（`L51-74`）**：
   - 锁文件路径：`/tmp/finance-cron-locks/resource-market_db_writer.lock`（与 `refresh_extended`、周六 `10:45` 前瞻预期、`14:00` 基本面修复、`16:30` 景气周更共用同一把全局排他写锁）。
   - **`wait_seconds=0` 零等待抢锁**：Step 6 `refresh_extended` 等待 `1800s`，而 Step 7 `concept_weekly_sync` 传入等待时间为 **`0` 秒**——若此刻任一其他任务短暂持有 `market_db_writer` 锁，`flock -w 0` 立即返回 `rc=75` 并仅打一行 `WARN ... (nonblocking)` 静默跳过整周同步！
   - **锁粒度过粗（持锁跑外部 I/O 与 LLM）**：`concept_weekly_sync` 在持有 `market_db_writer` 排他锁的整个生命周期内，串行执行 FMP profile 拉取（2s/只）+ 逐只 `claude -p` 子进程（单只 `timeout=60s`，wrapper 层**无总超时上限**）。若某周新增或重跑几十只股票，将长时间霸占 `market_db_writer` 锁，直接阻塞 `10:45` 前瞻预期任务！
3. **内部事务与降级行为（`build_company_concept_registry.py:1004-1176`）**：
   - 仅 `source in ("manual", "rule")` 的确定性结果会走 WAL 备份 + 两阶段 CSV/DB lockstep 自动落库；所有 `llm` / `llm_failed` / `llm_fallback` 结果**即使 LLM 调用成功也不会自动写库**，而是全部打入 `needs_review_YYYY-MM-DD.csv` 人工审核队列（导致无人值守周更下新股长期缺标签）。

### 5.2 仓库内现有 LLM 客户端与 `.env` 模型凭证盘点

1. **现有 LLM 调用实现盘点**：
   - 全局检索确认：**仓库 Python 源码中目前没有任何基于 HTTP API 或官方 SDK（Anthropic / OpenAI / Gemini / Moonshot）的客户端封装**。
   - 现存全部自动化 LLM 调用（`terminal/llm_concept_prefill.py:63`、`forge/runner.py:320`、`scripts/auto_deep_analyze.sh:127`）100% 依赖宿主机 `claude -p <prompt> --output-format json` CLI 子进程。
   - 根据 `docs/issues/034-cloud-claude-cli-org-access-revoked.md`，云端 `claude` CLI 因 OAuth 登录态/组织权限失效报 `Your organization does not have access to Claude`，自 2026-06 起云端定时任务中的 `claude -p` 已不可用，证实了**无人值守 cron 严禁依赖交互式 OAuth CLI，必须改用静态 API Key 的直接 HTTPS/SDK 调用**。
2. **Python 虚拟环境依赖包状态（`.venv` 实测 vs `requirements.txt`）**：
   - `.venv` 已安装：`anthropic=True`, `httpx=True`, `requests=True`, `pydantic=True`；未安装：`openai=False`, `google.genai=False`, `google.generativeai=False`, `litellm=False`。
   - ⚠️ **部署注意**：`requirements.txt` 中目前仅声明了 `requests>=2.32`（未固定 `anthropic`、`httpx` 或 `openai`/`google-genai`），云端部署新客户端前需将选定 SDK 或直连 `httpx`/`requests` 写入 `requirements.txt` 并在云端 `.venv` 验证。
3. **本地 `.env` 模型相关环境变量存在性核查（仅输出布尔值，未读取任何值）**：

| 环境变量名 | `.env` 是否配置非空值 | 当前进程环境是否存在 |
|---|---|---|
| `MOONSHOT_API_KEY` | **`True`** | `False` |
| `ANTHROPIC_API_KEY` | `False` | `False` |
| `CLAUDE_CODE_OAUTH_TOKEN` | `False` | `False` |
| `OPENAI_API_KEY` | `False` | `False` |
| `GEMINI_API_KEY` | `False` | `False` |
| `GOOGLE_API_KEY` | `False` | `False` |
| `DEEPSEEK_API_KEY` | `False` | `False` |
| `OPENROUTER_API_KEY` | `False` | `False` |
| `DASHSCOPE_API_KEY` | `False` | `False` |
| `XAI_API_KEY` | `False` | `False` |
| `GROQ_API_KEY` | `False` | `False` |

> **R8 落地提示**：当前本地 `.env` 除数据类 Key（`FMP_API_KEY`、`FRED_API_KEY`、`FINNHUB_API_KEY`、`ADANOS_API_KEY`、`MARKETDATA_API_KEY` 均为 `True`）外，唯一现成配置的 LLM Key 是 **`MOONSHOT_API_KEY`**（兼容 OpenAI Chat Completions REST 协议，直接用已安装的 `httpx`/`requests` 即可零新增依赖调用）；若架构选型采用 Claude Haiku/Sonnet、OpenAI 或 Gemini Structured Outputs 作为主/校双模型，需提示 Boss 在本地与阿里云 `.env` 中补充对应的 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`。
