# 三指数 PE 上线记录

Boss于2026-09-12批准合并、push和晨报上线。主线起点4bb2906，验收分支3db5a9b（69个提交）。新数据先在当日生产副本演练，再在共享写锁、SQLite事务与备份下推广；不覆盖整库、不发额外Telegram测试消息。

## 最终生产结果（2026-09-12）

**已部署，功能代码b352520。** 16:45:14开始最终推广，16:55:11生产事务提交成功；之后独立连接确认行数、quick_check，再fast-forward云端代码并通过Python3.10导入/编译、bash语法与执行权限检查。原crontab未改；没有重跑已完成的上游采集，没有额外Telegram发送。

- 历史：as-of9/12整窗重算，SPY261、QQQ261、SOXX251，共773周，TTM与后视镜均全发布；样本50/篮子、15项检查全过，0 actual_only降级。SOXX首个有效周2021-11-26，披露起点前留白。
- 真实PIT：云端先以只读源/内存产品预验收，再在生产候选事务内原生生成并严格重放10期×6篮子=60行（7/13–9/12）。不复制本地浮点计算结果，不覆盖任何旧试跑冻结vintage。生产表此前为空。
- 独立SQL：事务外再核对1646个已发布数值、完整周集合、R5与旧PIT对应数值，零差异；60条PIT中部分blend按原门控为NULL，不伪造全覆盖。完整报告 `reports/rendered/index-pe-deploy-20260912/production-independent-comparison.json`。
- 源写入：income upserts21238（其中替换51）、旧MDT日期别名1条由既有fiscal writer归档移除；42家公司的1626条metrics按既有公式重算并归档。HMC upserts45964（替换3588）；新增splits2126、FX4155、披露14643。四张forward源表未覆写，正常9/12数据保留。
- 原始14643条披露内容及日期字段哈希前后一致。整窗推进使SPY/QQQ各滚出首周，并把三篮子末点9/10推进至9/11；历史TTM仅3点变化，最新共识尾部165点变化，最大约0.035x。
- 正式备份：云端`data/market.db.before-index-pe-final-20260912`；native前次备份仍保留，早两次已归档本地。未整库覆盖生产，未清理无关周备份。
- 最终候选SHA256 `aba0772a74c1f2da7bb0f5ad6c8d49dc078b1566ff23824122257182f55ba392`，本地/云端一致。上传后复核；正式提交后删除云端该冗余候选，本地完整候选继续留档。原传输中止的半文件已清理，未作为输入使用。
- PNG：`reports/rendered/index-pe-deploy-20260912/index-pe-production-20260912.png`，1800×1510，SHA256 `f6292686554340419cc432da91a39c6a7bc1826774ac4c5d2e315a8ba770910b`，已目视检查。最新TTM为SPY25.8071、QQQ30.7206、SOXX36.7533；9/12真实NTM为20.2498、23.1022、19.3178（均为本模型口径，非官网PE）。
- 晨报真实投递路径只读预览：保存的9/11行情输入，原有10块与Top50不变，恰好一个内嵌图像，HTTP/Telegram均0。该旧报告日自然只显示9期PIT（9/12当时尚不可用）；独立9/12PNG显示全部10期，未伪造日期。HTML/PDF共享图与失败不重复发送另有自动化回归覆盖。
- SSH会话在远端已提交、结束并释放锁后仍未退出；核实事务报告ended_at、外部数据库及后续锁获取后，仅终止本次本地SSH PID28107。该清理返回255是传输进程结果，不是数据库回滚或部署失败。

### 尚未宣称完成的事项

1. 新完整周频wrapper的首次自然运行，以及下一次自然08:00晨报实际投递，尚未观察。此次用了已经完成的自然ingestion + 零HTTP整窗重算/原生PIT/两类认证，未重复发起数千API调用；无新增automation。
2. 新调仓若真正需要live，仍须补足发行人证据（issue077）。当前窗口用正式披露；没有降低身份门，也没有声称未来live已经验收。
3. 17:17清理已验证的本次临时镜像后，云盘仍仅余2.6GB（95%使用）。旧周备份保留策略的issue046仍待Boss授权，未清理无关历史备份；下一轮周任务前需处理空间余量，不能把此次代码/数据验收当作磁盘容量验收。

### 本地缓存同步完成

本地旧market.db在全量测试中发现malformed，具体损坏起因未确定，不能归因于本次PE代码。云端真库/最终候选均quick_check通过。17:15从云端只读SQLite backup得到一致性镜像，经SHA256 `440ad396050d6330bd0af509ac8eaacfd1894ee0e06221b663285316602d19f6`双端核对、773/60计数和quick_check，通过本地sync互斥锁及两次无打开连接检查后原子替换缓存；旧db及sidecar原样归档，旧WAL确认0字节。company.db、universe、持仓均未改。结果 `local-mirror-install.json`，旧缓存留在 `local-cache-before-refresh/`。本地新缓存和一致性镜像均保留；验证后移除云端临时镜像，正式生产备份不动。

同步后从本地正式缓存再次核对14643条披露哈希一致；不做路径override重跑187项相关测试，187 passed/1 skipped。

## 16:18 继续执行（以下为历史过程）

- Boss确认继续；原10:45采集于12:27:32正常完成，wrapper rc=0、6151秒。1029 targets，1008 quarter_ok、7 failed、14 empty；这些源缺口仍由估值覆盖门决定，不冒充全覆盖。
- 四张forward源表只读导出并导入source-only隔离库：runs11、estimates193692、earnings85297、holdings8349；gzip SHA256 `f8d9bbd913e8ea6e09da15f67986578e48f268c4f0d23c488470b020034f86fa`。无新增HTTP。
- 首次9/12本地重算在逐股阶段前被未选用live身份拦住。修正1710450只排除同effective且披露更早可用的必败live候选（issue077）；原始表不改，实际选源、身份门和公式不变。三例TDD先红后绿；183回归本地与云端均通过。
- 独立逐日比较：SPY/QQQ/SOXX各1255个交易日，修正前后选源完全一致。当前全部用6/30正式披露，不是9/11漂移live权重；原始14643条行内容哈希保留于 `source-scope-proof.json`。
- 新完整重算16:15启动，as-of9/12，PIT仍延后到云端原生首次冻结；旧失败run/report保留，新run前缀deployment-selected-source。不能把旧9/10产品作为本次最终产物。
- 空间处理仅涉及本次临时副本：旧refreshed-candidate与本地data/market.db的SHA256同为`edf06d1be0c74abcccc31e3f7f71e13973710e61dbfadb1305e86dbfc8c11713`；retry生产备份已归档本地production-before-retry.db，双端SHA256 `37c3a48c176e032cb95a065301d719165cbfb974a9652b5d774408327bd87531`、quick_check=ok。验证后移除这两份云端冗余副本，剩余空间3.7GB，native生产备份与其他历史备份未动。
- 新调仓若真正需要live，发行人证据仍待补足；本次排除无用来源不代表该后续能力已经完成。
- 16:27代码修正已cherry-pick至main/origin `b352520`。首次全量3554 passed/12 failed/4 skipped：5项因缺ignored广度CSV，7项因本地旧market.db损坏导致registry/罗盘读取失败。补只读广度依赖、使用已校验备份的专用测试副本（未改真库），先187 passed/1 skipped，再完整3566 passed/4 skipped/17 warnings。迁移脚本12 tests再次通过；部署功能目录与1710450一致。
- 当日云端数据层verifier：ok=true，986/1029=95.82%有未来四季；TECH earnings为已记录的单票失败，failures=[]。保留结构性缺口漂移与未识别资产告警，不声称所有ticker完整。

## 11:25 停点（历史记录）

**代码已合并并push，生产尚未启用新入口。** main/origin为7bc7442，生产仍4bb2906。三次候选推广均整批回滚，正常生产数据未被旧候选覆盖：依次捕获HONA新增行情、跨Python浮点末位、ORCL新财报可用日期从9/10改到9/11。后两者说明旧验收结果不能直接当作更新中的生产源的当前结果。

最新20个财季组及截至9/11量价已准备在独立source-only库：`.worktrees/index-pe-live-source-refresh/data/latest-market.db`，尚未产生新产品。10:45的正常forward任务持有写锁，FMP阶段11:11启动、1029 targets。完成后会只读导出四张forward源表，在此隔离库完成as-of9/12整窗重算；首次PIT由云端解释器在生产候选事务内生成并严格认证。迁移保护测试现12项通过，原生PIT九期内存测试全过，未调整容差。

初轮生产备份现归档本地`reports/rendered/index-pe-deploy-20260912/production-before-initial.db`，1025384448字节与云端一致、quick_check和关键行数通过后清理其云端副本；后两轮备份仍在`data/market.db.before-index-pe-{retry,native}-20260912`。历史试跑冻结数据全部保留。本文以下为过程记录，不代表上述停点已经完成生产发布。

## 已完成的合并与生产副本验收

- 功能merge `fe52bf7`，首轮push `f508c4a`；功能目录与 `93c8a6f` 完全一致，沿用其3563 passed/4 skipped全量验收；合并后隔离回归490 passed/1 skipped，Python3.10 AST、bash -n、diff检查、敏感值/DB文件检查通过。
- 原主目录CLAUDE两行云端SSOT提醒已还原为未提交修改，cio-b修改未动；仅三个冲突路径做了定向stash，`index-pe-rollout preserve overlapping user docs 2026-09-12`继续保留。两个issue的原文另在本文完整留档。
- 副本演练：`aliyun:/tmp/finance-index-pe-deploy-20260912/staging.db`。第一次在Python3.10 authorizer清理失败，连接退出回滚；修复后9个迁移保护测试在本地/云端实际解释器通过（issue076）。第二次08:44:36至08:55:46提交成功，15项历史检查、九期PIT和quick_check全部通过。
- 源变更：income新增21,197、更新56、经既有fiscal writer归档并移除1条MDT日期别名；HMC新增42,408、更新3,588；新增splits2,126、FX4,155、披露14,643。46家受影响公司的1,749条metrics经现有公式重算，保留归档。预测输入四表没有覆写。
- 产品首次导入：775周频行、31条完整run事件（含原失败/成功顺序）、54条PIT（9期×6篮子）。产品表必须为空，导入采用原run_id、created_at及证据JSON，不利用bootstrap绕过C1或PIT不可变契约。
- 迁移先做原基线→已接受试跑→当前生产三方比较，当前行不同于原基线且不同于目标即拒绝；只允许指定source/product和metrics/archive表写入。候选在同一SQLite事务内认证，不通过即回滚。原始逐行差异、脚本、测试与报告保留在worktree `reports/rendered/index-pe-deploy-20260912/`。
- 全晨报只读预览复用今早`morning_20260912_080129.json`及已验收DV缓存：原有10块保持一致、Top50完整，新增0d恰好一张自包含PNG；三篮子均有9期PIT、无告警。报告日9/11滚动五年过滤后SPY/QQQ显示261点，SOXX251点；源产品仍分别262/262/251，并非漏周。PNG目视通过；HTTP/Telegram均0。
- 09:00固定维护前不启动生产长事务，等待维护正常完成后推广。副本阶段的全表price EXCEPT附加对拍因IO竞争取消，不把未完成的检查列为PASS；既有15项认证正常完成。

## 正常维护引起漂移后的完整重算

- 09:00维护于09:13:06正常结束，随后第一次正式推广在09:16回滚：SPY/QQQ的9/10 HONA member市值不匹配新源。生产income仍8081，产品、manifest和披露均0，quick_check=ok；仅预建空schema保留。备份`data/market.db.before-index-pe-20260912`。
- 从维护后生产备份导出相关源变化；114条HMC中82条需吸收，1,074条价格中6条需吸收，其余与已接受副本相同。没有把旧值覆盖回正常维护的新数据。
- 另建source-only隔离库，未复制/删除旧冻结产品，使用现有producer完成三个完整C1窗口和九期PIT。计算区为`.worktrees/index-pe-live-source-refresh`，09:25:28–09:44:31，HTTP=0；每个新run均重新冻结周集合/哈希并完成事务内sample=50认证。
- 775个周键与此前完全一致，TTM/后视镜均全发布，没有actual_only降级。独立SQL复算1640个值零误差；54个真实PIT数值全部不变。历史线只有4个标量变化，最大0.0248904倍（QQQ 2026-06-12后视镜），另三个变化均更小；整体估值结论无实质变化。
- 新候选传回`aliyun:/tmp/finance-index-pe-deploy-20260912/refreshed-candidate.db`，再次按完整推广与认证流程执行，不跳过门控。
- 云端本次staging副本已完整带回本地`reports/rendered/index-pe-deploy-20260912/accepted-staging.db`，大小1126715392字节一致、quick_check及775/54行数通过后清理云端副本。原始9/11各冻结试跑、生产备份和本地证据均保留。

## 合并提交范围（审批时）

```text
ad85659 docs(valuation): plan SOXX historical TTM PE proxy
ece3b58 docs(valuation): harden SOXX plan against mcap contamination
015b3cd test(fmp): freeze SOXX disclosure FX and split contracts
87aee12 feat(fmp): add disclosure FX and split endpoints
81fbb36 feat(store): add auditable historical basket valuation tables
7fab8e0 feat(valuation): normalize SOXX disclosure history
17506ec feat(valuation): gate historical market cap anomalies
6e82e99 feat(valuation): compute as-of SOXX GAAP TTM proxy
98856f1 feat(valuation): orchestrate idempotent SOXX history backfill
e9fbb5e feat(valuation): query and export basket TTM PE history
9560a64 feat(valuation): verify SOXX historical PE read-only
cd398c1 docs(valuation): document SOXX historical TTM PE pipeline
d1f25b3 fix(valuation): use FMP disclosure identity for CREE alias
44990ff fix(valuation): harden source identity and audit gates
ded698b docs(valuation): record SOXX read-only dry-run
1fb42ff fix(valuation): verify persisted SOXX evidence
d47f6a9 docs(valuation): record final audit boundaries
4b96d47 fix(valuation): close SOXX audit safety gaps
a82a446 docs(valuation): plan three-index weekly PE morning chart
1029910 docs(valuation): revise three-index PE plan per CC audit (R1-R7)
eb75969 Merge branch 'main' into codex/index-pe-morning-chart
f103497 Merge branch 'codex/soxx-historical-ttm-pe' into codex/index-pe-morning-chart
aae1576 docs(valuation): freeze post-merge test baseline
c17a4aa fix(valuation): fail-closed empty refresh window replacement
d90377d refactor(valuation): generalize historical basket disclosures
30ae539 docs(valuation): correct Task 1 stale file refs to post-merge reality
fc69283 fix(valuation): correct SOXX history boundary to 2021-09-20
bc34812 feat(store): add weekly basket PE history contract
0ec48a8 fix(store): reject cross-version overwrite of any existing weekly PE row
2172a55 feat(valuation): compute hindsight NTM basket earnings
a89813a fix(valuation): anchor hindsight window and narrow member guards
6f4bbda fix(valuation): share one aggregate PE kernel and fix mixed-tz acceptance
62b27af feat(valuation): declare a market-cap convention per share-class group
9255de1 feat(store): add an append-only backfill run manifest with repair hashes
12b889b refactor(valuation): thread basket config through the backfill stages
d82443a feat(valuation): backfill three-index weekly PE history
7008568 fix(valuation): close three fail-open gaps in the weekly PE backfill
0cd1913 fix(valuation): close historical dual-class gap structurally
46b6b4d docs(valuation): record Boss stage-1 review rulings in contracts
2956b8e fix(valuation): close five information-crossing and evidence gaps
2af463c fix(valuation): compare the frozen week set in both directions
0b9b9c1 fix(valuation): validate the frozen manifest instead of displaying it
f774779 feat(store): bind published rows to the run that wrote them
628c49d docs(valuation): pin full-rewrite certification contract pending Task 5 ruling
34e6742 fix(valuation): a run declares one outcome, once, at the end
a36fac0 fix(valuation): require terminal events to land in run order
94fd03e docs(valuation): C1 ruled — full-window rewrite with in-transaction pruning
f55a927 docs(research): five-year dual-class mcap band study — 1% band validated
2b51195 fix(valuation): close pre-C1 verification and schema gaps
2b54bd2 fix(valuation): publish verified weekly windows atomically
87f16a0 feat(valuation): compute and certify six-basket PIT consensus PE
96416f9 feat(morning): render and embed three-index weekly valuation chart
9fb7634 merge: align index PE pipeline with current Finance main
89beeb2 fix(valuation): bound bootstrap requests and disclosure window
9dae7ac docs(valuation): record local acceptance and bounded cloud trial
6a34b02 fix(valuation): isolate legacy PE guard and preserve failure diagnostics
83e074c docs(valuation): record isolated trial identity blockers and repair plan
dc61062 fix(valuation): separate filer and issuer identity with source evidence gates
26cec1b fix(valuation): withhold unverified Fox and News share-class caps
e249a69 docs(valuation): record issuer repair acceptance and remaining evidence gaps
78411b9 data(valuation): add reviewed issuer evidence for twelve historical securities
ebc5b78 docs(valuation): record issuer evidence progress and remaining identity contract decision
c7595c6 fix(valuation): resolve verified issuer keys without mandatory LEI
a85d883 data(valuation): close historical issuer gaps with reviewed SEC identities
d7110cd fix(valuation): quarantine invalid historical caps without aborting basket
641c29d docs(valuation): record verified issuer closure and partial offline PE results
803ba91 fix(valuation): contain invalid vendor market-cap ranges
93c8a6f fix(valuation): bind reviewed security aliases to financial issuers
3db5a9b docs(valuation): certify full weekly history and nine PIT vintages
```

## 原主目录 issue 文本的无损留档

原主目录以下两份未追踪文件与分支同名但内容不同。为保留原始过程和未解决的非PE问题，合并前完整保存于此；正文是历史记录，不代表本轮重新验证其中每一个市场数字或链接。

### 原 issue035（主目录未追踪版本）

# Issue 035: KLAC 10:1 拆股跨表调整不一致（daily_price 未回溯 / market_cap 股数未更新 / income 残留行）

- **日期**: 2026-06-30（发现于 AMAT/LRCX/GLW/KLAC 四票 forward PE + 业绩增长分析）
- **状态**: OPEN（市值表/价格表为 market.db 云端独占写入，本地不改，待云端侧修复 + 重拉）
- **影响范围**: market.db 中 KLAC 的 `daily_price` / `historical_market_cap` / `income_quarterly` 三表；任何跨 2026-06-12 的 KLAC 价格级滚动指标（晨报 PMARP / 量能异常 / β6M / EMA / RVOL）；KLAC 市值口径

## 背景

KLA Corporation（KLAC）于 **2026-05 公告、2026-06-12 生效 10-for-1 forward split**。FMP / yfinance 对拆股的回溯调整在不同表里**步调不一致**，导致同一只票在不同表呈现不同的 share basis。当前价（$248.64）与当前 TTM EPS（$3.53，拆股后口径）都是拆股后基准，**所以现价 PE 计算正确（TTM 70.4x / fwd 49x）**，但跨拆股的历史序列全部失真。

## 现象（三处不一致）

**1. `daily_price` 未做拆股回溯调整 —— 绝对价在 2026-06-12 有 ~10x 断崖**

```
2026-06-11  close 2411.64   (拆股前绝对价)
2026-06-12  close  254.54   (拆股后绝对价)   ← ~10x 断点
```

`change_pct` 看起来是拆股感知的（记录 +7% 而非 -89% 裸跌），但**收盘价绝对值未回溯**：6-12 之前是 ~$1900-2400，之后是 ~$250。任何基于价格水平的滚动计算（PMARP 百分位、EMA120、β 若用价位回归、RVOL 若 volume 也未 ×10）跨 6-12 全部被污染。KLAC `in_pool=1`（Technology，核心池）→ 进晨报三件套，6-12 之后的指标不可信。

**2. `historical_market_cap` 全程钉死 ~130.9M 股（拆股前股数）**

`market_cap = 固定 130.9M × daily close`：
- 拆股前（≤06-11）：130.9M × ~$1921 = ~$251B ✓ 正确（KLAC 真实市值）
- 拆股后（06-12 ~ 06-23，约 8 个交易日）：130.9M × ~$248 = **~$32B ✗（10x 偏低）**，真实应 ~$320-327B（web 确认 $323-339B）

即拆股后股数没跟着 ×10。最新行 2026-06-23 = $32.0B 是错的。

**3. `income_quarterly` 已回溯到拆股后口径，但最老一行残留未调整**

近 8 季全是拆股后（~1320M 股 / EPS ~$0.6-0.9），唯独窗口最老的 **2024-03-31 Q3 残留拆股前**（EPS $4.43 / 135.9M 股）。它不在常用的 8 季窗口内，但会当 Q3'25 的同比基数 → 算出 -82% 垃圾 YoY（真实应 +81%）。

## 根因（推测）

FMP/yfinance 对 split 的回溯调整按表/按拉取批次进行，调整未原子化：
- `income_quarterly` 在拆股后重拉时整体回溯了（漏了窗口边缘最老一行）
- `daily_price` 存的是**当日实际成交价**，历史段未回溯（或回溯任务未对 KLAC 触发）
- `historical_market_cap` 的股数字段是某次快照的常量，拆股后未刷新

## 规避 / 修复

**分析侧（已用）**：YoY 基数改用「按股数归一化」EPS——`eps_norm = eps × 该季股数 / 最新股数`，自动把残留行 /10 修正，对正常行 ≈ ×1 无害。现价 PE 不受影响（现价与现 EPS 同为拆股后）。

**数据侧（云端待修）**：
1. `daily_price`：重拉 KLAC 全历史的 split-adjusted close + volume（或对 ≤2026-06-11 的 close ÷10、volume ×10）
2. `historical_market_cap`：拆股后股数刷新为 ~1309M（或重拉），修 06-12~06-23 段
3. `income_quarterly`：补调 2024-03-31 行（EPS ÷10、股数 ×10）
4. 加一道 split 守卫：daily_price 单日 |change| 与 close 跳变背离（如 change_pct≈+7% 但 close 跳 -89%）时告警，提示未回溯拆股

**验证**：修复后 `market_cap / close` 全程应得稳定股数；`daily_price` 跨 6-12 无 10x 跳变；KLAC 晨报指标重算。

## 关联

- Issue 019 / 033（worktree 空 market.db 影子）—— 同为 market.db 数据完整性类
- `docs/issues/036`（GLW GAAP vs core 口径）—— 同批分析发现的跨源口径问题
- 9b9461a `fix(indicators): compute_beta 剔除非正价格坏数据行` —— 价格脏数据影响指标的同类先例

### 原 issue037（主目录未追踪版本）

# Issue 037: 【误报 / 已解决】MU 最近两季"物理不可能"的财务——基于陈旧认知误判，实为 SEC 核实的真实超级周期数字

- **日期**: 2026-06-30（发现于内存上游 AMAT/KLAC/LRCX 设备产业链报告的 ground-truth 核对）
- **状态**: **RESOLVED — FALSE ALARM**（数据无误；本条保留为过程教训）
- **影响范围**: 无（market.db MU 数据正确）。本条记录的是**分析者认知层面的踩坑**，不是数据 bug

## 一句话

我一度判定 MU `income_quarterly` 最近两季（FY2026 Q2/Q3）"损坏、物理不可能"，并据此起草了数据污染 issue + 在 data_context 打了 ⛔ 警告。**联网核实 SEC 8-K + Micron 官方新闻稿后证明：那些数字逐行真实**——是 AI-HBM 超级周期下纯涨价驱动的真实井喷。错的是我的陈旧认知，不是数据。

## 我当时为什么误判（错误链条）

DB 显示 MU FY26 Q3（2026-05-28）：营收 $41.46B、毛利率 84.6%、净利 $28.24B、EPS $24.67、股价 $1,145；且 COGS 被"钉死"在 ~$6B 不随营收（13.6→23.9→41.5B）增长、`depreciation_and_amortization` 字段为 -$21.18B。

我的（错误）推理：
1. "DRAM/NAND 厂毛利率不可能 84.6%，2018 顶点也就 ~61%" ← **锚定了 2025 年及更早的陈旧毛利率认知**
2. "MU 单季 $41B 营收 = 年化 $166B，比肩台积电，不可能" ← **锚定了陈旧的营收量级**
3. "$1,145/股 × 11.45 亿股 = $1.31T 市值，存储厂不可能" ← **锚定了陈旧的股价/市值**
4. "COGS 钉死 + D&A 负值 = 源数据坏掉铁证"

## 真相（2026-06-30 联网核实）

- **SEC 8-K（EDGAR）+ Micron 官方 GlobeNewswire 新闻稿 + CNBC + StockTitan** 一致：FQ3 2026 营收 **$41.46B**、GAAP 净利 **$28.24B / $24.67 稀释 EPS**、毛利率 **84.9%（公司纪录）**、Q4 指引 $50B / GM ~86%。FQ2 同样逐行吻合（$23.86B / 74% GM / $12.08 EPS）。
- **股价**：MU ~$1,133–1,213（Macrotrends 52 周区间 ~$103→$1,200+），6/22 与 Anthropic 战略协议 + 6/24 blowout 财报催化。$1,145 真实。
- **84.9% 毛利率的物理解释**：DRAM 合约价单季 **+90-95% QoQ**（TrendForce）。营收三倍来自**纯涨价**而非放量 → COGS（多为固定折旧）基本不动维持 ~$6B → 毛利率机械地冲到 85%。我当成"造假铁证"的"COGS 钉死"，恰恰是纯涨价超级周期的**正确特征**。`D&A` 字段负值是 FMP 该行的符号/重分类怪异，不影响利润表三大指标（已与 SEC 逐项核对无误）。

## 教训（真正的踩坑）

1. **MEMORY 反模式"评估数据质量前必须先查 ground truth / 训练知识里的股价财务全部视作过期"——这次我险些反向踩中**：用陈旧 prior 把真实数据判成失真。该反模式的正确读法是**双向**的：既不能轻信 DB 也不能轻信记忆，唯一裁判是**当前 primary source**（SEC/公司 IR）。
2. **物理一致性论证有边界**：它能否定"内部自相矛盾"（如负折旧），但**不能否定"超出我记忆量级"的真实极端值**。84.9% GM 看似违反"制造业物理"，实为价格冲击下的真实结果。下"物理不可能"结论前，必须先用 primary source 校准量级基线。
3. **两个 subagent（数据 agent + R3）都说"真实"时我仍坚持己见**——怀疑精神可贵，但应导向**独立验证**（我最终直接 WebSearch SEC）而非**固执于 prior**。怀疑的出口是查证，不是否决。
4. 流程上做对的一点：**起草了 issue 但没急着 commit / 没据此删 MU 数据**，先验证后定论，避免了把错误固化进代码库。

## 关联

- `docs/issues/035`（KLAC 拆股跨表失真，**真实** data issue）/ `036`（GLW 口径，真实）—— 同批分析（2026-06-30）的另两条，那两条是真 bug，本条是误报
- MEMORY: `feedback_verify_ground_truth_before_quality_judgment` —— 本条是该反模式的"反向"实战案例，建议在该卡补一句"prior 也是过期数据，量级判断同样需 primary source 校准"
- 报告产物：`reports/semicap-upstream-2026-06/`（MU 8 季表已全部启用）
