# 03 — 独立红队审查：跨报告矛盾对拍与长周期实盘盲点扫描

- **审查日期**: 2026-11-27
- **审查角色**: Phase 2 Round 3 独立红队审查员（只读源码与数据库核验）
- **对拍基准**:
  1. 已确认需求文档：`docs/design/stock-theme-requirements.md`（R1–R12）
  2. 外部参考研究：`docs/research/stock-theme-2026-11-27/01-external-reference-systems.md`
  3. 本地实测审计：`docs/research/stock-theme-2026-11-27/02-local-universe-and-tech-audit.md`

---

## 核心审查结论（Executive Summary）

1. **存储位置无悬念，必须落回 `data/market.db` 内建新表（纠正报告 02 §4.2 独立 `.db` 建议）**：
   源码核验确认，只要由独立模块（如 `src/data/theme_basket_store.py`）直连 `data/market.db` 建表读写、不改动 `src/data/market_store.py`，就能 **100% 保持景气引擎 `code_version`（`4c9da30bd5001102`）不变**，且零改动享受 `sync_to_cloud.sh --pull` 的在线一致快照备份与 `data_guardian.py` 归档保护，也不破坏 `data_health.py` 的子集表检查。反之，若按报告 02 拆出独立 `data/theme_baskets.db`，云端同步和备份恢复都会彻底漏掉该库。
2. **发现两份报告共同遗漏的头号盲点：线上 `portfolio_intelligence.py` 早已换源 Google Sheet，而非读 `company.db:holdings`**：
   需求文档 §5 与报告 02 §1.1 均按 `company.db:holdings`（仅 4 只旧 OPEN 记录）盘点持仓 Overlay；但源码实测显示，PI 自 2026-06-11 起已切换为实时读取 `PORTFOLIO_SHEET_ID`（`portfolio/holdings/sheet_book.py`），内含未满 `$10B` 的主动美股（如 `AAOI`）、带后缀合成行（`NVDA LEAPS`）、自定义篮子代码（`DRAM`）、韩股（`000660`）与 4 位港股码（`0700`）。更危险的是，`portfolio_intelligence.py:1001` 渲染视觉表格时只遍历固定 `bucket_order`，**不在列表内的桶名会被直接静默丢弃整行持仓**。
3. **发现第二处隐藏哈希地雷：`config/symbol_aliases.json` 同样在景气引擎哈希锁死范围内**：
   `terminal/prosperity/version.py:25-26` 将 `src/data/symbol_aliases.py` 与 `config/symbol_aliases.json` 列入字节级 SHA256 哈希源，改动一个字符就会使云端 21 季景气冻结参数全部失效（`params_code_mismatch`）。主题篮子模块只能**只读复用**该文件，严禁把日常双股类映射或新改名代码直接写入 `config/symbol_aliases.json`。
4. **实锤 R12 同步脚本地雷：`sync_to_cloud.sh` 硬编码拉取未纳入 Git 的 `reviewed_current.csv`**：
   `sync_to_cloud.sh:269-303` 在每次 `--pull` 时从云端 `scp` 拉取 `reviewed_current.csv`。因该文件未被 Git 跟踪，R12 若漏改 `sync_to_cloud.sh`，每天 09:00 自动同步都会把云端残留的旧 CSV 重新拉回本地复活；若传输偶发失败，`exit 1` 会直接中断整个 `market.db` 拉取。
5. **收敛出 5 项需提请 Boss 拍板的真实业务分歧（D1–D5）**：
   其余纯技术冲突（存储位置、写锁粒度、置信度生成机制、兼容层缓存、R12 清理清单）均已给出确定性工程最优解，无需占用 Boss 决策精力。

---

## 一、跨文档矛盾检查（Contradiction Check）

### C1. 存储位置冲突：`market.db` 内建新表（R1/R5）vs 独立 `data/theme_baskets.db`（报告 02 §4.2）

- **冲突点**：需求文档 R1、R5 明确规定分类表作为 `market.db` 数据层底座存在；报告 02（L29、L406）为规避 `market_store.py` 哈希与写锁，建议“存储在独立 SQLite 文件如 `data/theme_baskets.db`”。
- **源码逐项核验结果**：

| 核验维度 | 方案 A：独立模块在 `data/market.db` 内建新表 | 方案 B：拆出独立 `data/theme_baskets.db` | 核验源码依据 |
|---|---|---|---|
| **景气 `code_version` 兼容性** | **100% 兼容**（不改 `market_store.py`，也不在被哈希文件内 `import` 新模块，哈希恒为 `4c9da30bd5001102`） | 100% 兼容 | `terminal/prosperity/version.py:17-49`<br>`tests/test_prosperity_schemes.py:118-135` |
| **`MarketStore._VALID_TABLES` 拦截** | **互不干扰**（白名单仅管 `MarketStore` 内部通用表校验；独立模块直连 `sqlite3.connect(MARKET_DB_PATH)` 不经过它） | 互不干扰 | `src/data/market_store.py:855-880` |
| **`sync_to_cloud.sh --pull` 同步** | **零改动自动同步**，且与 `extended_membership`、`daily_price` 处于同一 SQLite 在线备份读事务快照 | **完全漏同步**（现脚本只拉 `market.db` 和 `prosperity.db`，需另写一套快照/lsof/sha256/替换逻辑） | `sync_to_cloud.sh:186-256, 308-312` |
| **`data_health.py` 健康检查** | **100% 兼容**（`_check_market_db` 用集合差 `MARKET_DB_EXPECTED_TABLES - tables`，多出新表仍返回 `PASS`；且 `data_health.py` 不受景气哈希限制，可直接新增分类覆盖率检查） | 漏检，需另建新库文件检查 | `src/data/data_health.py:34-43, 236-257` |
| **`data_guardian.py` 快照备份** | **自动纳入 `tar.gz` 备份与一键恢复** | **完全无备份**（`snapshot/restore` 硬编码只打 `market.db` 与 `company.db`） | `src/data/data_guardian.py:53-97, 199-210` |
| **`data_validator.py` 一致性** | **100% 兼容**（仅查 `ratios_annual` 与 `income_quarterly`） | 100% 兼容 | `src/data/data_validator.py:60-170` |
| **同库关联查询与写锁** | 原生 `JOIN extended_membership / security_master`；只要把 FMP/LLM 网络调用移出写锁外，毫秒级落库持锁零阻塞 | 跨库查询必须 `ATTACH DATABASE`；双库快照存在时间差 | `scripts/broad_universe_cron_wrapper.sh:51-74` |

- **确定性技术裁决（无需提请 Boss）**：**严格按 R1/R5 采用方案 A（存入 `data/market.db` 内建新表，由独立模块 `src/data/theme_basket_store.py` 维护 DDL 与读写）**，否决独立 `.db` 文件方案。
- **附带 SQLite 外键护栏**：新表的 SQLite `FOREIGN KEY` 只能约束 `l1_id / l2_id / theme_id -> theme_baskets(basket_id)`，**严禁在 `symbol` 列加 `REFERENCES security_master(symbol)`**——因为 `GRAL`、`DXYZ`、`EWY` 等 Overlay 标的不在 `security_master` 表中，加此约束会导致 Overlay 写入直接触发外键报错。

---

### C2. 异构 Overlay、双股类与「真实持仓信源错位」在 R4（成篮纯净）与 R10（展示不断链）间的冲突

- **冲突点**：
  1. **ETF/非美股张力**：R4 规定“ETF 严禁混入个股 L2 赛道或跨界主题篮子”；但 R10 要求无缝接管晨报与 PI，而 PI 需要对账户内所有持仓（含 ETF、CEF、港股、韩股、合成期权行）输出主题分桶与中文 `business_role`。
  2. **PI 视觉表格静默丢行陷阱（新发现）**：核查 `scripts/portfolio_intelligence.py:997-1013` 发现，PI 绘制分组持仓 PNG/PDF 时只循环 `for bucket in deps["bucket_order"]:`，**没有任何兜底追加逻辑**！一旦兼容层的 `classify(item)` 对 ETF 或未覆盖标的返回了不在 `bucket_order` 内的字符串（或 R12 删除 `report_concepts.json` 后丢失 `"ETF/宏观工具"` 桶），该笔真实持仓会在 PI 图片报告中**无声消失**。
  3. **真实持仓信源错位（重大盲点）**：需求文档 §5 与报告 02 §1.1 均把 `company.db:holdings` 当作当前持仓来源（仅见 4 只旧记录 `AMZN, GOOG, MSFT, QQQ`）。但核查 `scripts/portfolio_intelligence.py:19, 1061` 与 `portfolio/holdings/sheet_book.py:19-48` 确认：**线上 PI 股票持仓唯一真源早已切换为 Google Sheet（`Summary_OSV`）**！Sheet 内实际存在：
     - 市值 `< $10B`、不在 `extended_membership` 也不在 `company.db` 的主动交易美股（如 `AAOI`）；
     - 带空格后缀的合成行（如 `NVDA LEAPS`、`X PUT`，见 `sheet_book.py:147` 与 `portfolio_intelligence.py:1121`）——旧版靠 `report_concepts.json` 子串关键字 `"nvda"` 碰巧命中，R12 删掉关键字规则后若直接按 `"NVDA LEAPS"` 查表必漏；
     - 自定义篮子代码 `DRAM`（`portfolio_intelligence.py:438` `SHEET_PRICED_US_SYMBOLS`）、杠杆/地区 ETF（`KORU`、`EWY`）；
     - 跨市场持仓及三种不统一的港股代码格式：Sheet 归一化后的 4 位码（`HKG:0700 -> 0700`）、`company.db:holdings` 的 5 位码（`01810`、`09992`）、`watchlist` 的带后缀码（`1548.HK`、`603259.SS`），以及 6 位韩股码（`000660` SK海力士）。
  4. **双股类“掉池失踪 vs 双算污染”**：`GOOG`（2026-11-21）、`FOXA`（2026-11-07）、`NWSA`（2026-11-07）因多股类结算问题已从 `extended_membership` 活跃池调出，而次股类 `GOOGL/FOX/NWS` 在 `security_master` 中 `eligible=0`，导致三家巨头在活跃主池中彻底归零；旧 `company_concept_tags` 把主次股类同时入表又造成篮子等权涨跌和成交额翻倍双算。

- **架构调和方案**：
  1. **数据层用 `asset_kind` + `in_us_basket_calc` 将「展示登记」与「成篮计算」物理隔离**：
     - 美股合格普通股/ADR 主代表股（每 `CIK` 唯一 1 只）：`asset_kind='us_equity'`, `in_us_basket_calc=1`，强制 `l1_id`、`l2_id` 非空；
     - ETF / 封闭式基金（`QQQ, SPY, SOXX, EWY, KORU, DRAM, DXYZ`）：`asset_kind IN ('etf', 'fund_cef')`, `in_us_basket_calc=0`, **`l2_id = NULL`**（100% 遵守 R4 不进个股篮子），单独提供 `display_bucket='ETF/宏观工具'`、`business_role` 及可选 `underlying_theme_id`（如 `DRAM -> 存储`，专供 CIO 穿透计算敞口）；
     - 非美股持仓/观察标的（`0700, 01810, 000660, 1548.HK, 603259.SS`）：若纳入（见 **D2**），标 `asset_kind='non_us_equity'`, `in_us_basket_calc=0`，仅供 PI 展示与 CIO 跨市场主题敞口归集，绝不混入美股篮子横截面统计。
  2. **只读查询接口内置三步符号标准化（Symbol Normalization）**：
     - 剥离期权/合成后缀（`"NVDA LEAPS" -> "NVDA"`）；
     - 统一港股代码格式（`00700` / `0700.HK` / `HKG:0700` 对齐标准键）；
     - 双股类与历史改名透明映射（查 `GOOGL` 自动返回主代表股 `GOOG` 的分类记录，查 `BRK-A` 返回 `BRK-B`，数据库只存 1 份主代表股记录，根治双算）。

---

### C3. 跨界大主题边界冲突：宏观/风格风险因子 vs 产业叙事主题篮子

- **冲突点**：现行 `concept_taxonomy_v2.json` 的 34 个 L3 中，混入了 **11 个宏观敏感度、风格因子或政策事件敞口**——包括人数最多的 `rate_sensitive`（78 只）、`defensive_stock`（48 只）、`tariff_exposure`（33 只）、`realestate_cycle`（31 只）、`industrial_capex`（30 只）、`china_exposure`（25 只）、`geopolitics`（23 只），以及 `inflation_hedge`、`antitrust_risk`、`crypto_regulation`、`ai_export_control`。
- **外部机构方法论核验**：
  - 在 MSCI（Barra 多因子风险模型 vs MSCI Thematic Indexes）与 S&P DJI（风格指数 vs S&P Kensho New Economies）标准架构中，**宏观/风格因子（利率久期、低波防御、关税/地域风险）**与**主题篮子（Thematic Baskets）**严格分离：前者属于定量风险暴露（应由收益率对利率/大盘回归 Beta、波动率或地区收入占比连续计算），后者属于跨行业产业结构性趋势（按供应链与商业变现环节选股）。
  - 把 78 只跨银行、地产、公用事业、软件的股票塞进一个静态 `rate_sensitive` 篮子，既无法计算有意义的主题拥挤度，也会在晨报 `display_tags` 中挤占真正的产业主线展示位。
- **处理结论**：提列为 **D1** 请 Boss 拍板（强烈建议跨界大主题 100% 纯化为产业与技术结构性叙事主线，彻底剥离宏观风格与政策风险标签）。

---

### C4. LLM「置信度」契约冲突（R8/§5 数值置信度 vs 报告 01 §3.3 校准失效实证）

- **冲突点**：需求文档 §5/R8 要求模型输出“置信度”并拦截低置信度标的；报告 01 §3.3 证明大模型在 `strict: true` 受限解码下自报数值置信度（如 `0.95`）严重虚高失真，建议改用「双通比对分歧 + `runner_up_l2_id`」。
- **确定性技术裁决（无需提请 Boss）**：在分类表落地离散枚举列 `confidence_tier CHECK (confidence_tier IN ('locked', 'high', 'medium', 'low'))`，由确定性规则赋值：
  - `locked`：命中 Git `Overrides` 手工锁定表；
  - `high`：双通独立判定 `l2_id` 与 `cross_theme_ids` 完全一致，且模型未报出强竞争 `runner_up_l2_id`；
  - `medium`：`l2_id` 一致，但存在强竞争 `runner_up_l2_id` 或跨界主题存在单边分歧；
  - `low`：双通 `l2_id` 不一致（按 R8 先落主通道稳妥分类，并在周六 Telegram 摘要单列提醒 Boss）。

---

### C5. 跌出主池老票“保留分类”（R4）与“成篮人数门槛 / 成分股提取”（R3/R10）的口径冲突

- **冲突点**：历史累计 Extended 共 1,011 只，其中 155 只已调出（含市值跌破 `$10B` 及并购退市股）。若 R4 保留全部老票分类，而 R10 查询篮子成分股时未区分“当前在池活跃股”与“历史残留股”，会导致两个后果：① 像 `clean_energy`（全表 10 只、活跃仅 4 只）这类伪达标篮子蒙混过关；② 上层算当日篮子等权涨跌时把已退市或微盘股算进去。
- **确定性技术裁决（无需提请 Boss）**：
  1. R3 成篮人数门槛（`≥ 4~5 只`）严格以**当前活跃主池有效代表股**为唯一核定分母；
  2. R10 `get_basket_members(basket_id, scope=...)` 强制设默认 `scope="active"`（仅返回 `in_us_basket_calc=1` 且当前在活跃主池的代表股），同时提供 `scope="as_of"`（按 `extended_membership` 历史区间提取时点成分）与 `scope="all"`（含调出保留股）。

---

## 二、1~2 年长周期实盘盲点扫描（Blind-Spot Check）

### B1. 改代码、分拆（Spin-off）、主业剧变（Pivot）与 `symbol_aliases.json` 隐藏哈希红线

1. **隐藏红线发现（报告 01/02 均漏查）**：
   `terminal/prosperity/version.py:25-26` 将 `src/data/symbol_aliases.py` 和 `config/symbol_aliases.json` 纳入了景气引擎字节级哈希，且 `tests/test_prosperity_schemes.py:137-152` 强制校验“改动 `symbol_aliases.json` 即拒绝旧冻结参数包”。因此：**主题篮子模块严禁在日常运维中修改 `config/symbol_aliases.json`**，只能只读消费其现有 15 条改名映射，并在自身配置目录 `config/theme_baskets/` 内独立维护补充映射。
2. **本库真实公司行为尸检（只读查询 `market.db` 实证）**：
   - **改代码断链与错判**：
     - `PSTG -> P`（Pure Storage 更名 Everpure，同 `CIK 0001474432`）：新代码 `P` 进活跃池后，被旧死规则按 `Industrial` 误判进 `industrial_automation`（工业机械），丢失存储芯片/阵列身份；
     - `EQR -> VMRK`（Equity Residential 更名 Vivmark，同 `CIK 0000906107`）：旧代码 `EQR` 有标签，新代码 `VMRK` 自 `2026-10-31` 进活跃池至今无标签裸奔；
     - `BK -> BNY`（同 `CIK 0001390777`）：新旧代码同时残留在 `company_concept_tags`，造成双算。
     - **防线设计**：周六新票入池时，先用 `CIK` 反查库内是否已有同 `CIK` 前身代码。若前身命中 `Overrides`，自动继承分类并在 Telegram 告警提示更新 `Overrides` 代码；同时将旧代码标为 `is_superseded=1` 移出活跃成篮集。
   - **分拆上市（Spin-off）的子母双向盲区**：
     - 本库已发生 `GE -> GEV + GEHC`、`HON -> HONA`（且伴随 When-Issued 临时代码 `HONAV` 混入分类表！）、`ILMN -> GRAL`、`MMM -> SOLV`。
     - **两大漏洞**：① 分拆过渡期的 When-Issued 临时代码（如 `*V` / `*-WI`）必须在准入门控拦截；② 母公司剥离大块业务后自身主业可能改变（如 `HON` 剥离航空后不再是航空军工股），仅扫新进池子代码会漏掉母公司重审。
   - **主业剧变（Business Pivot）—— 1~2 年运行期的最大静态腐化源**：
     - 本库实盘案例：比特币矿企转型 AI 算力数据中心（`IREN`, `APLD`, `WULF`, `CIFR`——其中 `WULF` 与 `CIFR` 在现库竟被分在 `investment_bank` 投资银行！）、软件公司转型比特币金库（`MSTR` 更名 `Strategy Inc`，FMP `industry` 仍挂 `Software - Application`）、`YNDX` 重组为 AI 算力云 `NBIS`。
     - **漏洞**：R8 目前只检测“新进池股票”，在池老股即使更名或主营彻底转型也永不重审。具体重审触发策略提列为 **D4**。

---

### B2. `sync_to_cloud.sh` 遗留 `reviewed_current.csv` 同步逻辑核查（实锤阻塞隐患）

- **源码定位**：`sync_to_cloud.sh` 第 `65-67` 行、第 `83-85` 行、第 `269-303` 行。
- **故障推演**：
  1. `git ls-files reports/concept_registry/` 实测确认：**`reviewed_current.csv` 与 `reviewed_current_manifest.json` 未纳入 Git 版本控制**。
  2. 当 R12 在代码库删除旧概念栈并推送后，阿里云 06:25 `git pull` **不会删除云端磁盘上的 `$REMOTE_DIR/reports/concept_registry/reviewed_current.csv`**。
  3. 若 R12 漏改 `sync_to_cloud.sh`：① 本地每天 09:00 自动执行 `--pull` 时，L275 `ssh test -f` 恒为真，L296-299 会自动重建 `reports/concept_registry/` 目录并把旧 CSV 拉回本地（死灰复燃）；② 一旦 `scp` 预取偶发超时，L280 `exit 1` 会直接中止整个 `market.db` 同步任务！
- **确定性处置**：将「清理 `sync_to_cloud.sh` L65-67 / L83-85 / L269-303 + 显式删除云端 `$REMOTE_DIR/reports/concept_registry/`」写入 R12 必选验收步骤。

---

### B3. CIO「单一主题 35% 上限」穿透机制与现行 PI 口径错位

- **多维核查发现三个断层**：
  1. **赛道拆分掩盖共跌风险**：若组合持有 `NVDA` (14%, L2: AI计算芯片) + `MU` (14%, L2: 存储) + `VRT` (12%, L2: 供电温控) + `COHR` (10%, L2: 光互连)，单一 L2 均 `≤ 14%`，单一 L1 均 `≤ 28%`，但四者同受 AI 资本开支周期驱动，在跨界大篮子 `AI 算力基建` 下合计敞口达 **50%**，一旦叙事退潮将同步回撤（对应 `cio_layer.md` 第一大痛点）。
  2. **平台巨头“全额计入”失真**：若 `AMZN`（15%）与 `GOOG`（15%）也作为无差别成员挂入 `AI 算力基建`，仅两只综合平台股就占掉 30% 额度，导致风控误报。
  3. **现行代码与总账规则脱节**：原生记忆 `reference_us_equity_book_sheet.md:44` 记录 Boss 已于 2026-07-31 拍板**「单一主题上限 = 35% of Whole Port Net Total」**，且存在用 `DRAM` ETF 替代单股持仓的实操（L58）；但 `scripts/portfolio_intelligence.py:719` 至今仍按粗粒度 15 桶检查 `tracked_pct >= 0.40`（40% 美股单账本 NAV，且不穿透 ETF 与期权腿）。
- **数据层支撑结构（对应 D3）**：
  - 数据层对每只美股同时暴露**互斥 `l2_id`**（供 100% 不重不漏切分）与**多选 `cross_theme_ids`**（供跨赛道大叙事并集穿透）；
  - 跨界关联表增加极简角色字段 `role CHECK (role IN ('core', 'platform_giant'))`，并为行业/主题 ETF 预留 `underlying_theme_id`（`in_us_basket_calc=0`，不进个股行情篮，但允许 CIO 汇总敞口时穿透识别 `DRAM -> 存储`、`SOXX -> 半导体`）。

---

### B4. 其余源码级遗漏盲点汇总（报告 01/02 未覆盖）

1. **双股类巨头从活跃主池“蒸发”未闭环（`GOOG`、`FOXA`、`NWSA`）**：
   报告 02 仅指出 `GOOG` 靠持仓 Overlay 留在总并集，但忽略了 `FOXA`、`NWSA` 无持仓已彻底掉出活跃池，且上层篮子涨跌计算若直接 `JOIN extended_membership WHERE effective_to IS NULL`，连 `$2T` 市值的 `GOOG` 也会被漏掉！`get_basket_members(scope="active")` 或上游池刷新必须补上多股类主代表股防漏机制（当同 CIK 公司仍在美股正常交易且市值达标时保留主股类）。
2. **R12 三代旧栈清理漏掉的 5 处耦合点**：
   - `terminal/commands.py:375-385`：`theme_status()` 仍在 `from terminal.themes import get_theme`（第一代 `themes.py` 的残存入口）；
   - `tests/test_legacy_telemetry.py:127`：白名单硬编码了 `"scripts/scan_themes.py"`，删脚本时必须同步移除该行否则单测挂红；
   - `src/data/fundamental_collector.py:282, 307`：记录了旧 `build_company_concept_registry.refresh_profiles` 绕过数据库直写 `profiles.json` 的过渡债——新分类器为新票（如 `GRAL`）补拉 FMP Profile 时，**必须走 `MarketStore.upsert_company_profile()` 落表**，严禁再写 JSON；
   - `sync_to_cloud.sh:65-67, 83-85, 269-303`（见 B2）；
   - 10 个专属旧概念单测文件（报告 02 仅列 3 个，完整清单含 `test_concept_v2_integration.py`、`test_market_store_concepts.py`、`test_scan_themes_universe.py`、`test_build_concept_registry.py`、`test_concept_v2_cli_semantics.py`、`test_concept_manifest_fallback.py`、`test_concept_classifier.py`、`test_a3_weekly_concept_sync.py`、`test_company_concepts.py`、`test_scan_themes.py`）。
3. **晨报导入期缓存固化（`morning_report.py:96` + `concept_classifier.py:320`）**：
   `CONCEPT_BUCKET_ORDER` 在模块 `import` 顶层立即求值并全局缓存。新兼容层应支持按需懒加载与 `reset_cache()`，避免测试或长进程读到过期桶列表。
4. **公开仓库（Public Repo）下的 Git 配置隐私红线**：
   Finance 仓库为 GitHub 公开库。旧 `config/concepts/concept_watchlist.json` 曾把 Boss 私密关注的微盘股（`POET, OKLO, DXYZ`）直接提交进公开 Git。新架构必须废除公开 `concept_watchlist.json`：Git `Overrides` **只放市场公认的大盘多业务巨头与公开龙头**，个人持仓/观察小票由云端从 Google Sheet 与 `company.db` 读取后写入 gitignored 的 `market.db`。

---

## 三、需提请 Boss 拍板的设计分歧清单（D1–D5）

> 仅保留真正决定业务口径与外部依赖的 5 个问题；存储选型、哈希隔离、锁粒度与兼容实现已由工程层确定最优解。

| 编号 | 决策议题 | 核心冲突与实盘影响 | 候选方案与推荐默认项 |
|---|---|---|---|
| **D1** | **跨界大主题篮子的纯度边界：是否彻底砍掉宏观/风格风险标签？** | 旧 L3 混有 `rate_sensitive`(78 只)、`defensive_stock`(48 只)、`tariff_exposure`(33 只)、`china_exposure`(25 只) 等宏观敞口；机构标准（MSCI / S&P Kensho）将宏观风格因子与产业主题严格分离。保留它们会稀释主题拥挤度信噪比并挤占晨报标签位。 | **方案 A（推荐）**：跨界大主题 **100% 限定为产业与技术结构性叙事**（精选约 8–12 个，如 `AI 算力基建`、`AI 电力缺口`、`加密生态`、`商业航天与国防科技`、`自动驾驶与机器人`、`核能复兴`、`GLP-1 与代谢` 等），彻底删除宏观/风格/政策风险标签。<br>**方案 B**：保留极少数宏观标签但用 `basket_type='macro'` 与产业主题分榜隔离。 |
| **D2** | **实时持仓 Overlay 范围与非美股/ETF 的登记方式** | 线上 PI 实际从 Google Sheet 读持仓（含 `<$10B` 美股如 `AAOI`、韩股 `000660`、港股 `0700`、自定义篮 `DRAM`、合成行 `NVDA LEAPS`），仅扫 `company.db` 会漏掉真实持仓导致 PI 分桶断链或丢行。 | **方案 A（推荐）**：云端周同步覆盖 `Extended ∪ company.db(holdings/watchlist) ∪ Google Sheet 当前持仓`（不进公开 Git）；表内设 `in_us_basket_calc=1/0`——仅美股单公司主代表股 `=1` 参与篮子表现/拥挤度统计；ETF 与港/韩/A 股持仓以 `in_us_basket_calc=0` 登记展示名片与底层主题归属，兼顾 PI 展示与美股篮子纯净。<br>**方案 B**：`market.db` 只收美股标的，非美股在 PI 兼容层用简单市场规则兜底。 |
| **D3** | **CIO「单一主题 35% 上限」的跨界穿透口径与超级平台股角色** | 持有 `NVDA+MU+VRT+COHR` 在 L2 各自 `<15%`，但跨界 `AI 算力基建` 合计达 50%；若把 `AMZN/GOOG` 等零售/搜索占大头的平台巨头也全额算进同一跨界篮，又易虚增敞口触发误报；另需支持 `DRAM` 等主题 ETF 穿透计入存储敞口。 | **方案 A（推荐）**：数据层在跨界关联表提供极简角色枚举 `role IN ('core', 'platform_giant')`，并为主题 ETF 提供 `underlying_theme_id`（`l2_id=NULL`）；供上层 CIO 默认按「单一 L2 赛道」与「跨界大主题（可筛 `core` 或含 `platform_giant`）」双维度校验 35% 红线。<br>**方案 B**：跨界大篮子在准入时直接排除 `AMZN/GOOG/META` 等多业务平台巨头（只收纯卖铲人），不做角色分层。 |
| **D4** | **在池老票「主业剧变（Pivot）」与分拆母公司的重审策略** | 矿企转 AI 数据中心（`IREN/APLD/WULF`）、软件转比特币金库（`MSTR`）等在 1–2 年内会让“只标新进池票”的静态分类腐化；但自动改标又会导致历史篮子口径跳变。 | **方案 A（推荐）**：「自动侦测 + 只报不改」——周六 cron 检测到在池老票发生**公司更名**或 **FMP `sector/industry` 变更**时跑一次复核，若建议赛道变化，**不自动改库**，仅在 Telegram 周摘要推送提示，由 Boss 确认后生效。<br>**方案 B**：在池老票完全冻结不重审，仅靠人工发现后写 `Overrides`。 |
| **D5** | **首轮精标与云端周频增量的持久化大模型 API 渠道** | 云端 `claude` CLI 因登录态失效已瘫痪（issue 034）；当前本地 `.env` 仅配置了 `MOONSHOT_API_KEY`，尚未配置 OpenAI / Anthropic / Gemini API Key。 | **方案 A（推荐）**：配置持久化 `OPENAI_API_KEY`、`GEMINI_API_KEY` 或 `ANTHROPIC_API_KEY`（写入本地与阿里云 `.env`），走原生 `strict: true` JSON Schema 双通校验（首轮 ~1,000 只一次性成本约 \$1–\$5，周频全年 < \$2）。<br>**方案 B**：直接复用现有 `MOONSHOT_API_KEY`（需确认阿里云 `.env` 已同步该 Key，并在代码端加严格枚举重试校验）。 |
