# 股票主题篮子分类 — 领域研究汇总（Phase 2）

> **日期**: 2026-11-27
> **状态**: Boss 已审阅，D1–D5 已拍板（2026-11-27）
> **输入**: 已确认需求文档 `docs/design/stock-theme-requirements.md`（R1–R12）
> **配套术语表**: `docs/design/stock-theme-glossary.md`
> **三轮原始研究底稿**（`docs/research/stock-theme-2026-11-27/`）：
> - `01-external-reference-systems.md`：GICS / ICB / RBICS / 卖方主题篮子方法论、前视偏差实证与 LLM 封闭集工程实践（含 19 项一手文献链接）
> - `02-local-universe-and-tech-audit.md`：`market.db` / `company.db` 全池对账、61 个旧 L2 尸检与重切验证、下游契约与云端环境实测
> - `03-blindspot-and-cross-check.md`：Round 3 独立红队矛盾裁决与 1~2 年长周期实盘盲点扫描

---

## 1. 一页结论

1. **传统行业分类失效的病根是「历史会计营收锚 ≠ 市场定价锚」**：GICS（60% 营收线）与 FactSet RBICS Focus（50% 营收线）按过去一年账面总流水最大头分桶，把 `AMZN` 锁在百货零售、把同走 AI 缺电逻辑的 `VRT`/`GEV` 与 `CEG` 拆在工业与公用事业两个抽屉。本项目确立的 **「L2 互斥交易赛道（100% 资金加总）+ 跨界大主题（横向叙事）+ 核心票 Overrides 焊死定价锚」** 完全契合卖方交易台最佳实践。
2. **内部交易篮无需凑够 25 只，5~25 只是统计黄金区间**：公募主题 ETF（如 Global X、MSCI）硬凑 20~25 只下限是为了应付美国税法 RIC `25/5/50` 分散化合规；卖方定制篮子（高盛 Marquee、大摩）无此约束。主池内 **≥ 4~5 只（理想 8~25 只、上限 ≤ 30~35 只）** 即可将个股特质方差压至 ≤20%，并提供 ≥10 对两两相关性支撑拥挤度计算。
3. **跨界大主题防稀释的铁律是「只收卖铲人（Enabler），硬拒用铲人（Adopter）」**：摩根士丹利、S&P Kensho 与 MSCI 均严格隔离“直接提供瓶颈硬件/电力/设施的供给方”与“采购技术降本增效的下游企业”，否则全美股 500 强都会因简介提一句 AI 而混入篮子。
4. **回算历史拥挤度存在「前叙事分母失真」**：学术实证表明，事后按赢家成篮的主题 ETF 上市后 5 年风险调整后跑输约 **30%**（Ben-David 2023），投行定制指数实盘夏普中位数衰减 **73%**（Suhonen 2017）。若拿 2026 年的 `AI 电力缺口` 名单回算 2022–2023 年（当时工业与电力零相关），历史分母过低会导致当期拥挤度分位永久卡死在 95%+。数据层必须记录篮子 `narrative_start_date`（叙事起点）与归属 `assigned_at` 阻断跨代回算。
5. **本库 856 只主池数据完备（99.9% 有 ≥150 字简介），现行 61 个 L2 严重哑铃失衡但可干净重切为 ~48 个黄金赛道**：现表 17 个 L2 不足 5 只（含 2 个 0 只空桶、5 个 1 只孤儿桶），10 个 L2 臃肿在 28~51 只；按真实交易因子拆分大桶、合并碎桶并聚拢散落的「光通信与高速互连（13~15 只）」与「数据中心供电温控（10~14 只）」后，全部 L2 均可落入 **5~25 只**区间。
6. **大模型打标不能信口头置信度，须靠「推理前置 Schema + 异构双盲分歧 + 枚举外置校验」**：LLM 自报 `0.95` 严重虚高（Xiong ICLR 2024），且 Gemini/Kimi 对 >100 项严格 `enum` 会报 400 复杂度错误；应让模型先输出业务锚点与排除核对、再输出 `l2_id` 与校验期次优备选 `runner_up_l2_id`（`l1_id` 由代码查表生成），通过跨独立会话双盲运行定档 `locked / dual_agreed / single_pass_weekly / needs_spot_check / disagreed`。
7. **存储定论：独立模块在云端权威 `data/market.db` 维护 5 张新表，100% 不碰景气哈希源**：不改 `CODE_VERSION_SOURCES` 全部 11 项文件（含 `market_store.py`、`settings.py`、`symbol_aliases.py`、`symbol_aliases.json` 等），本地严禁写库、零改动复用 `sync_to_cloud.sh --pull` 快照同步、`data_health.py` 与 `data_guardian.py` 备份。
8. **三轮交叉扫描抓出两个关键线上盲点**：① 线上 `portfolio_intelligence.py` 早已从 **Google Sheet**（`company.db:holdings` 已退役）实时读持仓，且渲染图片时不在 `bucket_order` 内的桶会被**静默丢行**；② 因 FMP screener 对多股类的间歇抖动，**`GOOG`（Boss 当前持仓！）、`FOXA`、`NWSA` 已从活跃 `extended_membership` 掉出**，必须结合 `security_master.eligible=1` 与近 10 日 `historical_market_cap ≥ 100 亿美元` 做活跃防蒸发兜底。

---

## 2. 参考系统拆解

| 参考系统 | 值得借鉴的核心机制 | 明确不照搬的部分 |
|---|---|---|
| **FactSet RBICS**（`Focus` vs `With Revenue`） | 把「互斥单主业树（100% 不重不漏）」与「跨界多维暴露」拆成两套互补视图 | 不照搬 50% 历史会计营收滞后死线（改用市场定价锚 + `Overrides`）；不照搬 1,600 个过细 L6 节点 |
| **Goldman Sachs Marquee / Morgan Stanley Thematic Baskets** | 8~30 只高流动性交易篮规模；严格区分 **`Enabler`（供给侧卖铲人）** 与 **`Adopter`（下游使用者）**；按产业链瓶颈切篮 | 不照搬卖方复杂的流通市值/ADTV 连续倾斜权重（数据层只管纯净成分归属） |
| **S&P Kensho / MSCI Thematic Indexes** | 主题字典强制写清准入与排除规则；为核心直接受益股与大型综合平台股区分角色（`Core` vs `Platform Giant`） | 不照搬公募指数为满足税法 RIC `25/5/50` 硬塞 `Quasi-Play` 凑够 25 只的做法 |
| **ARK Invest 主题流程** | 允许尚未跨过 50% 历史会计营收、但已成为市场公认新主线核心标的的转型龙头入篮 | 不照搬纯主观远期叙事选股（易导致风格漂移，变成高 Beta 成长大杂烩） |
| **Hoberg-Phillips TNIC / BlackRock LLM 同伴研究** | 用公司实际商业产品与客户文本语义（而非粗糙行业名）识别真实同涨同跌同伴 | 不做成每日浮动的全动态黑盒聚类（篮子边界不固定会导致上层无法连续追踪资金流） |

---

## 3. 本库主池尸检与 L2 赛道重切底座（基于 856 活跃股实测）

- **现状失衡**：活跃主池已入库 837 只分布在 61 个旧 L2 中——**17 个 `< 5` 只**（如 `crypto_mining` 0 只、`datacenter_power_cool` 仅 `VRT` 1 只、`search_engine` 1 只、`memory_chip` 4 只），**10 个 `> 25` 只**（如 `industrial_automation` 51 只、`mega_bank` 48 只、`integrated_oil` 47 只、`power_utility` 40 只）。
- **已验证可落入 `5~25` 只黄金区间的关键重切方向**（完整逐桶明细见底稿 `02` §3.3–3.4，将在 Phase 3 定稿字典）：
  1. **补齐核心漏篮**：聚拢散落在 5 个旧桶的光模块/铜缆/互连芯片/交换机龙头（`LITE, COHR, CIEN, GLW, FN, APH, TEL, ALAB, CRDO, MTSI, ANET, CSCO...`），独立成 **「光通信、高速互连与数据中心网络（13~15 只）」**；剥离 `DELL, HPE, SMCI, CLS, JBL, FLEX` 为 **「AI 服务器整机与企业硬件（8~10 只）」**；聚拢 `VRT, ETN, HUBB, NVT, TT, CARR, JCI, PWR, EME` 为 **「数据中心供电、电网设备与温控（11~14 只）」**。
  2. **拆分四大臃肿桶**：
     - `mega_bank`(48) → **全球货币中心综合大行（~20 只）** vs **北美区域银行（~19 只，KRE 利率/商业地产信贷逻辑）**；
     - `integrated_oil`(47) → **综合油气与上游勘探 E&P（~25 只，或再拆巨头 12 + 页岩油气 15）** vs **中游管道与 LNG（16 只）** vs **炼化与油服（9 只）**；
     - `power_utility`(40) → **核电、独立发电商 IPP 与新型电力（~10 只：`CEG, VST, NRG, TLN, CCJ, GEV, BE, FSLR`）** vs **受监管公用事业（~28 只，类债防御逻辑）**；
     - `industrial_automation`(51) → 拆分为供电温控、**工厂自动化与精密仪器（~19 只）**、**环保固废与路线服务（9 只）**、**工业分销与工具（8 只）**。
  3. **合并 17 个过碎桶**：加密三桶合一为 `加密与数字资产`；`search_engine` + `social_media` 并入 `数字广告、社交与搜索平台（10~14 只）`；`gpu_accelerator` + `cpu_soc` + AI ASIC（`AVGO, MRVL, ARM`）重组为 `AI 算力与核心计算芯片（8~10 只）`；`memory_chip` 吸纳误放工业桶的 `P`（原 Pure Storage）与 `NTAP` 组成 `存储芯片与企业存储（6~7 只）`；家居建材零售与误放 REIT 的住宅建筑商（`DHI, LEN, PHM, NVR, TOL`）合并为 `北美住宅建造与家居建材链（10~14 只）`。

---

## 4. 领域与工程陷阱清单（跨三轮研究汇总）

| # | 陷阱 | 实证 / 本库证据 | 架构应对措施 |
|---|---|---|---|
| **P1** | 跨前叙事时代回算拥挤度 | 2022–2023 年 `CEG` 与 `VRT` 零相关，纳入分母会使当期相关性分位永久卡在 95%+ | 篮子字典记录 `narrative_start_date`，归属变更记入只追加 `stock_theme_history`，建库日前查询强制打 `backfill_approximate` 标签 |
| **P2** | 多股类 Screener 抖动蒸发巨头 | **`GOOG`（2026-11-21）、`FOXA`、`NWSA` 已掉出活跃 `extended_membership`**，次股类又被标 `eligible=0` | `scope="active"` 对 `security_master.eligible=1` 且近 10 日 `historical_market_cap ≥ 100 亿美元` 的主代表股做防蒸发兜底；查询层支持主次股类双向透明映射 |
| **P3** | 同公司双股类重复入篮双算 | 旧表同时含 `GOOG+GOOGL`、`BRK-A+BRK-B` 等 **9 组双股类**，等权涨跌与成交额翻倍 | 同一 `CIK` 在库内严格只置 1 只 `in_us_basket_calc=1` 主代表股 |
| **P4** | 真实持仓源错位与 PI 图片静默丢行 | PI 读 **Google Sheet**（含 `<$10B` 美股 `AAOI`、合成行 `NVDA LEAPS`、自定义篮 `DRAM`、港韩股；`company.db:holdings` 已于 `7b9ad9d5` 退役），且 `portfolio_intelligence.py:926-929` 不在 `bucket_order` 的桶直接静默吞行 | 同步宽松只读纳入 Sheet ∪ `watchlist`；解析器保留 `LEAPS/PUT` 方向；固化原 15 桶清单保证 100% 闭合 |
| **P5** | PI `40% NAV` 告警与晨报粒度分叉 | 晨报按 ~50 个 L2 分组，PI 原有 222 条逐票规则跨多个 L2 算 `≥40% NAV` 集中度告警 | 晨报 `_grouping_bucket()` 读 L2；M4 逐票冻结存量票旧 PI 分桶落入 `pi_bucket`，100% 保住 `40% NAV` 口径不变 |
| **P6** | 非普通股与无档案代码污染篮子 | 旧表混入 **27 只 `-X` 共同基金、~40 只优先股/次级债**，且 `DRAM` 自定义篮被模型凭字母猜成存储芯片商；`DXYZ` 为 `isFund=True` 封闭基金 | Step A 确定性判定 7 类 `asset_kind`（严禁模型填），非普通股强制 `in_us_basket_calc=0`，跨市场加前缀防撞键 |
| **P7** | 改代码与分拆 When-Issued 污染 | `PSTG->P` 被误判工业、`EQR->VMRK` 无标裸奔、`HONAV` 临时代码入表 | 拦截 When-Issued 代码；读时合成 `security_master` + `symbol_aliases.json` + 补充别名归一至 `canonical_symbol` |
| **P8** | 景气 `CODE_VERSION_SOURCES` 11 项字节哈希地雷 | `terminal/prosperity/version.py` 锁死了 `market_store.py`、`settings.py`、`symbol_aliases.py`、`symbol_aliases.json` 等共 11 项路径，改 1 字节即改变 `code_version()` | 新模块与配置 100% 物理独立，4 张旧表先清空留空壳、死 DDL 搭车删除，确保实施前后 `code_version()` 恒等 |
| **P9** | 持 `market_db_writer` 写锁跑慢速网络与周六撞锁 | 旧周同步以 `wait=0s` 抢锁并在锁内跑网络；且云端周六 09:00 才刷池、10:45–17:00 连续占锁 5 小时 | 改至**周日 10:00** 在本机跑分类，仅经受限 SSH 在云端锁内跑 `<20ms` 纯内存事务 |
| **P10** | `sync_to_cloud.sh` 残留 CSV 拉取与本地句柄拦截 | `sync_to_cloud.sh:269-303` 硬编码 `scp reviewed_current.csv`，且 `:173-182` 遇本地非空 WAL 或打开句柄即 `exit 1` | 本地严禁写 `market.db`、读连接即用即关；M7 删除 `sync_to_cloud.sh` 残留 CSV 段并清理云端旧目录 |
| **P11** | JSON Schema 字段反置与大枚举报错 | 先出 `l2_id` 再出 `reasoning` 导致盲猜；>100 项严格 `enum` 在部分模型易报 400 错误 | 结构固定“业务锚点 + 排除核对”在前、`l2_id` 在后；`l1_id` 代码查表派生；Python + SQLite 触发器强校验 |
| **P12** | 多选跨界主题“宽松滥标”与同会话假双通 | 模型见简介含 `"AI"` 即乱挂标签，同会话跑两遍会相互锚定 | 每个篮子写死 `exclusion_rule`（排除下游 `Adopter`）；首轮强制跨主控与异构 Fable 真双盲校验 |
| **P13** | 公开 GitHub 仓库暴露私密观察小票 | `origin` 为公开仓，旧 `concept_watchlist.json` 曾把私密小票提交公开 | 废除公开 `concept_watchlist.json`；Git `overrides.json` 只放公开大盘龙头，私密小票走云端库内 `db_locked` |

---

## 5. 已锁定的确定性技术选型（经 Phase 3 终审对齐）

| 决策项 | 工程裁决 | 依据 |
|---|---|---|
| **数据库物理位置与写点** | 独立模块 `src/data/theme_basket_store.py` 在云端权威 `data/market.db` 维护 5 张新表；本地严禁写库，仅靠 `--pull` 拉取 | 满足 R1/R5，零改动复用 `sync_to_cloud.sh --pull` 与备份，不触发本地 WAL 拦截，不碰景气哈希 |
| **SQLite 外键与触发器范围** | 每连接开启 `PRAGMA foreign_keys = ON`，外键与触发器锁死 `tier` 层级与跨界表纯度，不在 `symbol` 列绑 `security_master` | 允许 `GRAL`、`AAOI`、`DRAM`、`DXYZ`、`QQQ` 等不在 `security_master` 的 Overlay 标的合法登记 |
| **一致性状态与历史回放** | 离散五档 `locked / dual_agreed / single_pass_weekly / needs_spot_check / disagreed` + 只追加 `stock_theme_history` | 语义诚实可复现，上线日起支持真实 `as_of` 回放（建库日前标 `backfill_approximate`） |
| **成分股查询默认口径** | 短连接 `get_basket_members(basket_id, scope="active")` 返回 `in_us_basket_calc=1` 且在活跃主池（或 `eligible=1` 且近 10 日历史市值 ≥ 100 亿美元）的代表股 | 防止历史调出股、双股类或 ETF 污染当期均值，同时彻底救回掉出满 21 天的 `FOXA/NWSA/GOOG` |
| **本机三明治自动化通道** | 周日 10:00 `launchd` 跑前后置脚本 + 仓外交换目录 + 零命令权限 AGY Sidecar + `restrict,command` 受限 SSH | 零 API 账单、避开周六池滞后与写锁高峰、物理隔绝提示词注入越权 |

---

## 6. 需要 Boss 拍板的 5 项设计分歧（进入 Phase 3 前确认）

| # | 分歧议题 | 候选方案 | 推荐 | 理由 |
|---|---|---|---|---|
| **D1** | **跨界大主题的纯度边界**：旧 L3 混有 `利率敏感(78)`、`防御股(48)`、`关税敞口(33)`、`中概(25)` 等宏观/政策风险标签，是否保留？ | **(a)** 100% 纯化为**产业与技术结构性叙事**（约 10~15 个，如 `AI 算力基建`、`AI 电力缺口`、`加密生态`、`商业航天`、`核能复兴`、`GLP-1` 等），彻底砍掉宏观/风格/地域风险标签；<br>**(b)** 保留少量宏观标签并拆成另一类 `macro` 篮子 | **(a)** | 机构标准（MSCI / Kensho）将宏观 Beta 风险与产业主题严格分开；78 只跨行股塞一篮算不出真拥挤度，还会污染标签展示 |
| **D2** | **实时持仓 Overlay 与异构资产（ETF/港韩A股）登记**：线上 PI 实际从 Google Sheet 读持仓（含 `<$10B` 美股、ETF、港韩股），新表如何覆盖？ | **(a)** 云端同步只读纳入 `Extended ∪ company.db ∪ Google Sheet 当前持仓`（私密持仓只进云端 `market.db` 不进公开 Git）；表内用 `in_us_basket_calc=1/0` 物理隔离——仅美股单公司主代表股 `=1` 进篮子统计，ETF 与非美股持仓以 `=0` 登记展示桶与角色；<br>**(b)** `market.db` 只存美股个股，ETF 与非美股留在 PI 代码硬编码兜底 | **(a)** | 既 100% 严守 R4“美股篮子零污染”，又让 PI 图片报告永不静默丢行，统一一套数据源 |
| **D3** | **CIO「单一主题 35% 上限」穿透支持**：持有 `NVDA+MU+VRT+COHR` 在各 L2 均 `<15%` 但跨界 `AI 算力基建` 达 `50%`；同时若把 `AMZN/GOOG` 全额算进跨界篮又易虚增分子 | **(a)** 跨界关联表加极简角色列 `role IN ('core', 'platform_giant')`，并为主题 ETF（如 `DRAM`、`SOXX`）预留 `underlying_theme_id`，供未来 CIO 同时按 L2 和跨界核心股穿透校验 35% 上限；<br>**(b)** 跨界大篮子直接排除多业务平台巨头（只收纯卖铲股），不加 `role` 列 | **(a)** | 多花 1 个枚举列即可同时兼顾“看大票参与度”与“CIO 算纯核心敞口不误报”，零过度设计 |
| **D4** | **在池老票主业剧变（Pivot）怎么管**：如矿企转 AI 数据中心（`IREN/WULF`）、软件转屯币（`MSTR`）、大集团分拆（`HON`） | **(a)** **自动侦测 + 只报不改**：周日同步任务发现老票公司更名或 FMP `sector/industry` 变更时自动跑一次复核，有变动仅在 Telegram 周摘要提示，Boss 点头才改库；<br>**(b)** 老票落库后完全冻结，仅靠人工发现改 `Overrides` | **(a)** | 兼顾 1~2 年防分类腐化与历史口径稳定（绝不背着 Boss 偷偷跳篮子） |
| **D5** | **云端持久化模型 API Key 选型**：本地 `.env` 目前仅有 `MOONSHOT_API_KEY`，无 OpenAI / Gemini / Anthropic API Key | **(a)** 在本地与阿里云 `.env` 配入一个主流持久化 Key（`OPENAI_API_KEY`、`GEMINI_API_KEY` 或 `ANTHROPIC_API_KEY`），首轮 ~1,000 只双通精标成本仅约 \$1–\$5，周频全年 <\$2；<br>**(b)** 直接用现有 `MOONSHOT_API_KEY`（需确认阿里云 `.env` 已同步，代码侧加严格枚举重试防 10-K 阶段曾遇到的不稳定） | **(a)**（或双支持） | 首轮 ~1,000 只精标质量直接决定底座寿命，主流模型对美股公司业务细节认知更准、结构化输出更稳 |

---

### 拍板记录（2026-11-27 Boss 确认）

- **D1 → 按推荐 (a)**：跨界大主题 100% 纯化为产业与技术结构性叙事，彻底砍掉宏观风格与地域/政策风险标签。
- **D2 → 按推荐 (a)**：覆盖 `Extended ∪ Google Sheet 当前持仓 ∪ company.db:watchlist`（`company.db:holdings` 已退役；私密持仓不进公开 Git），表内通过确定性 `asset_kind` 与 `in_us_basket_calc=1/0`（跨市场代码加前缀）物理隔离美股篮子计算与异构持仓展示。
- **D3 → 按推荐 (a)（Phase 3 精简修订）**：跨界关联表设 `role IN ('core', 'platform_giant')` 支撑 CIO 35% 单一主题上限双维穿透；不单设粗糙单值列 `underlying_theme_id`——对 `fmp_etf_holdings_snapshot` 已覆盖的基准 ETF（`SOX/IGV/QQQ/SPY/XLF`）由上层按真实成分权重穿透，其余行业 ETF 或自定义篮（如 `DRAM`）待上层 CIO 制定穿透公式时专门处理。
- **D4 → 按推荐 (a)**：在池老票发生更名或 FMP 行业变更时「自动侦测 + 只报不改」，仅在周摘要提示候选，经 Boss 确认后才更新入库。
- **D5 → Boss 拍板升级为「本地 AGY 订阅驱动（零云端付费 API）」**：第一版全池 ~1,000 只直接在当前开发阶段精细完成一次；后续每周增量与异动复核改由**本机 Antigravity（AGY）定时自动化任务**使用订阅配额在本地运行，不在云端配置或调用大模型 API。

