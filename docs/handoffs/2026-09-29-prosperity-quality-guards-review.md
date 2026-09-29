# 给CC：景气三项输入质量修复验收

> 最新状态见第9节：CC反馈P1/P2已修复，代码1bf1d496；第1–8节为首轮交付记录。
状态：**代码已完成并提交隔离分支；待CC验收，未合并、推送或部署。** 本次修复使用逻辑，不把供应商可疑原值直接改成“正确数据”。

## 1. 审查入口与已批准目标

- Worktree：`/Users/owen/.codex/worktrees/d9-recovery/Finance`
- 分支：`codex/prosperity-quality-guards`
- 代码提交：`96bae1fb8509fa03fedd476dcb6013136260a2bf`；基线：`fea08a09`。另有前置计划提交`f446fe1b`。
- 主仓库与云端仍为`fea08a09`，不要在主仓库直接跑旧代码来判断本次修复效果。
- Boss明确：当前龙头候选与未来持续数据优先，历史缺失可留空，不为补齐过去拖延当前工具。此次没有扩大补数，没有新API调用，没有生产数据写入。
- 计划：`docs/plans/2026-09-29-prosperity-m3-m4-quality-guards.md`（本worktree）。这只是M3/M4三项输入保护，不代表完整M4/打分引擎/榜单产品已完成。

## 2. 实现行为

| 原问题 | 新行为 | 不做的事 |
|---|---|---|
| 三表日期等于/早于财季末 | 历史模式要求合法、晚于财季末的accepted_date，否则可退到同表合法filing_date；无可信日期明确unknown，冻结到达也共用此判定 | 不以EPS公告日或固定天数猜填三表日期 |
| 历史日期未知误伤当前股票 | 当前模式验证归档观测时间和SHA，允许使用该观测时点已经存在的历史输入；as_of不得早于观测日 | 不将9/29快照伪装为6/30或9/26已知；不要求每条老财报有精确首发日期 |
| 同财季EPS数值矛盾 | 先按公告as_of过滤；财季组跨度≤20天，禁止链式合并；同值重复可折叠，冲突则该季值为None，保留来源 | 不取最新、不平均，不从GAAP抄值 |
| 只回溯了部分拆股历史 | 检查整个已存EPS序列中的street/GAAP量纲比例断点，结合已知拆股倍数输出suspect及证据 | 不自动乘除拆股倍数；GAAP只作量纲旁证 |
| 误把单因子缺失当整股不可用 | SUE/ΔSUE分别检查真实依赖季度；仅跨越受污染边界的结果缺失，旧窗口外冲突不永久封禁 | 尚未实现M6实际摊权排名，本次只输出可供它消费的判定 |

### 拆股检查的具体边界

断点两侧各3个同财季配对观测，间隔60–120天；每側比值相对中位数偏差≤35%，两侧比值范围必须不重叠。对数空间的拆股倍数匹配误差≤min(log(1.25), 0.25×|log拆股倍数|)。这样平滑序列不会因5:4等小比例拆股而落进宽容差。输出仅为suspect，不是拆股调整错误的最终裁决；一个断点可能对应多个历史拆股候选。

GAAP配对不足、没有拆股元数据、没有可比较窗口均显式单列；`no_suspect_detected`也不是“已证明干净”。历史当前表本身经过事后重述，借快照已知拆股信息检查其量纲属于retrospective诊断，不冒充严格PIT。

### 快照读取安全

仅对源SQLite主文件hash、随后直接mode=ro打开会消费旁边未计入hash的WAL。现在先边复制边hash到私有临时目录，匹配manifest后以SQLite `immutable=1`和`query_only=ON`读取同一份私有副本。源文件后续替换或WAL不会混入；临时副本自动清理，需约1.4GB临时空间。

## 3. 代码范围

- 新增`src/data/prosperity_quality.py`：日期可信性、季度EPS共识/冲突、拆股量纲断点的纯函数。
- `src/data/prosperity_history.py`：SUE/ΔSUE依赖范围阻断；异常原因不能误归为不可补的`zero_sigma`；冻结到达共用可信日期。
- `scripts/verify_prosperity_history.py`：历史与观测快照两种模式；报告旧规则计数、当前模式可用性、质量原因；新拆股检查进入正式摘要；质量待核实项不导出到EPS自动补数名单。
- `scripts/backfill_extended_fundamentals.py`：仅抽取并复用原连续窗口纯内核。旧采集目标/manifest的原判定语义保留，没有自动重试或改状态。
- 真实样本fixture：`tests/fixtures/prosperity_quality_cases.json`，从已冻结快照最小化摘录ORLY/ANET/KLAC/BHP/FER/COIN/DEO，不需要联网。
- 北极星和术语表澄清SUE：分子不减历史同比均值；分母仍是普通样本标准差，未换公式。

## 4. 真实样本验收

- KLAC：2024Q2 6.60 → Q3 0.733，已定位2024Q3量纲边界，而非仅看2026-06拆股日附近。
- ANET：2024Q2 2.10 → Q3 0.60，定位2024Q3边界。
- ORLY：2024Q3 11.41 → Q4 0.66，定位2024Q4边界。
- BHP、FER、COIN：同季度冲突值均保留原始来源，解析值缺失，不再默认最新公告正确。
- FER/BHP占位公开日期不能用于提前到达；当前归档模式仍可证明这些数据在观测时点已知。
- 覆盖正常反例：同值重复、零值、真实经营下滑但量纲比值稳定、平滑的小比例拆股、冲突在窗口之外、SUE不跨边界但ΔSUE跨边界。

## 5. 验证结果与独立审查

- 先看到原行为失败：冲突EPS仍sue_ok=True；占位日期arrival_day=0；小比例拆股误报；源WAL可绕过主文件SHA。对应回归修复后通过。
- 相关测试：**137 passed**；Python3.10语法检查通过。
- 全量：**4438 passed / 4 skipped / 12 failed**，288.92秒；12项与上一轮完全相同（7项广度研究缺本地数据、5项晨报概念分类），此前已在未修改main复现。全量运行后最后新增ΔSUE原因输出及单侧窗口用例，已纳入最终137条专项复验。
- 1个独立reviewer，high审查，首轮发现2 P1+1 P2：小拆股平滑误报、WAL旁路、摘要仍用旧检测器。三项均修复并复审；复审独立60 tests passed，另验证5种真实比例断点均仍可识别，无新增阻断。没有由reviewer修改代码。
- 原始归档SHA保持不变；本轮生产写入0、API请求0。main及云端未变。

## 6. 最终报告（主仓库路径）

`/Users/owen/CC workspace/Finance/reports/prosperity/quality-guards-final-20260929/`

- `validation-manifest.json`：源码提交、数据SHA、退出码、验证摘要和工件SHA。
- `d9-coverage-current-96bae1fb.md/.json`：**2026-09-29归档观测模式**，915只当时活跃且合格成员。
- `d9-coverage-historical-96bae1fb.md/.json`：20季历史公开日期模式；用于诚实披露，不作为本轮继续补历史的理由。
- `full-tests.log`：全量测试原始输出。

当前归档结果（不能与6/30或9/26不同截面直接作净变化比较）：

| 指标 | 结果 |
|---|---|
| 三表可用 | 872/915 = 95.30%，三表门通过，report rc0 |
| SUE可计算（已拦截检出问题） | 799/915 = 87.32% |
| ΔSUE可计算 | 791/915 = 86.45% |
| 三表与SUE都可用 | 788/915 = 86.12% |
| SUE缺失主因：同季EPS冲突 | 59只 |
| SUE缺失主因：拆股量纲疑似 | 8只（原因有优先级，不代表全库仅8只疑点） |
| 拆股诊断能力 | 530只无拆股元数据；369未检出疑似；12疑似；3无可比窗口；1配对不足 |

**rc0只说明三表门通过，不表示SUE或所有输入均已认证。** 未引入SUE硬覆盖门或90%软提示。`quality_issues`包含窗口外历史诊断，不能把里面出现过的股票整只排除；当前受阻结果应看`gaps.street_eps`，内核调用方看`sue_missing/dsue_missing`和具体依赖。

历史模式20期均未过90%，rc1；2026-06可信公开时间覆盖765/933=81.99%，旧规则原始计数886/933=94.96%仍作为对照。符合Boss允许历史缺失的最新优先级，不应自动发起更大补数。

## 7. CC建议复核步骤

```bash
cd /Users/owen/.codex/worktrees/d9-recovery/Finance
git diff fea08a09..96bae1fb -- src/data/prosperity_quality.py src/data/prosperity_history.py scripts/verify_prosperity_history.py scripts/backfill_extended_fundamentals.py
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_prosperity_quality.py tests/test_prosperity_history.py tests/test_backfill_runner.py tests/test_backfill_street_eps.py tests/test_market_store_fmp_forward.py -q
```

当前归档复算（只读，不使用主工作区当前可能已被自动pull替换的market.db）：

```bash
"/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/verify_prosperity_history.py report \
  --db-path "/Users/owen/CC workspace/Finance/data/backups/prosperity/phase0-accepted-input-20260929/market.db" \
  --snapshot-manifest "/Users/owen/CC workspace/Finance/reports/prosperity/phase0-close-20260929T040937Z/cloud-manifest.json" \
  --as-of 2026-09-29 --run-id d9-full-20260928 \
  --date cc-quality-review --out-dir /tmp/prosperity-cc-quality-review
```

请重点审：1）是否存在错误放行/误伤正常值；2）当期与历史模式能否互相绕过；3）SUE/ΔSUE依赖是否正确、未知是否暴露；4）拆股启发式的适用限制是否诚实。不要为保持旧覆盖率调松质量门，不擅自修原值、补数、merge/push/deploy。

## 8. 尚未做的部分

- 未查证并修复每一个供应商原始冲突值；未给未知日期伪造出处。
- 未实现全部M4/M6、NTM口径适配、每周归档调度或正式候选榜；后续必须接入本次纯函数与归档模式，不能绕过它们直接读原值。
- 现有自动pull的并发一致性问题、AEM/BIP来源日期回写风险仍是此前单独事项。本次保留的独立验收快照不受普通pull路径覆盖。
- SUE缺失的股票在未来M6可按已有规则使用其他因子并标记；是否满足最低历史和有效权重门槛仍需实际排名内核判断，本次不承诺全部新股进入主榜。


## 9. CC验收意见闭环：最新交付（2026-09-29）

**代码提交：`1bf1d496e580ed986e00766b812c749677802e76`，同一分支；P1/P2均已修复，尚未merge/push/部署。** 本节数字和接口说明覆盖上面首轮记录；CC原始意见在主仓库`docs/handoffs/2026-09-29-prosperity-quality-guards-cc-review.md`。

### P1：只接受小整数拆股记录

- 分子和分母均必须是1–20之间的整数，且不相等；接受SQLite的2.0等整值浮点，拒绝bool、NaN、小数、非正数和大于20的数。
- 不靠split_type，不把903/500等大分数约分后放行。被排除的事件保留在`ignored_split_events`，全部不合格时状态为`no_eligible_split_events`，不宣称已确认“没有拆股”。这也意味着真实但超出该比例协议的事件需另行核实。
- DELL、FTV、LH完整真实窗口夹具在改前均复现误拦，改后无此疑点；ANET、KLAC、ORLY、APH、MNST真实拆股边界仍被识别。FISV仍有一个由合格整数拆股记录支持的历史疑点，位于当前依赖窗口外，不阻断当前SUE。

### P2：历史已有日期的公告下界

三个函数新增**可选keyword-only**参数，原调用兼容，但CC的M4历史路径必须传入EPS行才能启用这项新保护：

```python
statement_availability(row, *, earnings_rows=None)
statement_known_on(row, *, observed_at=None, earnings_rows=None)
arrival_day(tables, qe, *, earnings_rows=None)
```

同财季±20天、actual为有限实数（0有效）、公告日在财季末之后的行中，取最早公告日。仅当原始公开日期本来合法时，使用max(原公开日期, 最早公告日)；未知公开日期仍None。返回的`reported_public_date`保留原可信字段日期，`earnings_floor`标下界，延后时添加`statement_date_before_earnings`原因。

当前observed路径明确忽略这项历史下界，继续以归档观测证据工作。报告的known_dates/当期锚/冻结到达均已接入；`public_date_floor_adjustments`列出调整证据。此规则是基于现有资料的保守历史限制，不声称法律上所有公司必定先发业绩再提交文件，也不证明供应商公告日期永远正确。

真实回归：ASML2022-06-30的7/2延到7/20；HALO2025-09-30的10/3延到11/3。另覆盖未知不得填、最早而非最新、0 actual、预排null/NaN/非法日期不作证据、其他财季不匹配、当前模式不变，以及report当期锚和arrival_day集成。

### 已同步Boss决定

北极星已明确：质量层只检测，不做换算；M4在同断点所有匹配拆股比例一致确认时于内存换算、挂`eps_split_rescaled`，未确认的断点前季度记缺失并挂`eps_split_unconfirmed`，market.db不改。这部分M4逻辑由CC按其plan实现，本轮没有抢做。

同财季冲突继续整段依赖窗口拦截，代码未改变，不放宽为1%容差。现有函数仅容忍浮点噪声（rel_tol=1e-9、abs_tol=1e-12）。`depth_ok/consecutive`仍表示日期深度，不表示冲突季度有可信数值，列为已知展示限制。

### 最终验证与实际数字

- 相关 **162 passed**；Python3.10语法通过。
- 全量 **4464 passed / 4 skipped / 12 failed**，301.78秒；12项为原有广度数据/晨报分类基线失败，没有新增失败。
- 1个独立reviewer复审：**86 passed**，无新增阻断，未修改代码或生产数据。
- 同一9/29观测快照、915只成员，三表仍872；SUE **799→802**（87.65%），恢复**DELL/FTV/LH**；ΔSUE **791→795**（86.89%），额外恢复DHR；三表与SUE联合 **788→791**（86.45%）。
- SUE的冲突原因仍59只，拆股疑似原因8→5；没有放松冲突窗口规则，没有因小数比例被过滤而修改原EPS。
- 当前report rc0；历史report rc1，20期历史时间证据仍未过90%，6/30仍765/933=81.99%。六个历史季的冻结到达统计因公告下界略微延后，真实结果已保存，不当作当前榜的新阻塞。
- 生产写入0，API调用0，保留快照SHA仍为3caffdb7e7c2d2d9c5124fcbf364e46cf32d51307dde3d9ea49bf8ecf54800e2。

新证据目录：`/Users/owen/CC workspace/Finance/reports/prosperity/quality-guards-cc-fixes-20260929/`，含current/historical两份`*-1bf1d496`报告、`current-factor-delta.json`逐票前后结果、`full-tests.log`、`validation-manifest.json`（工件SHA/退出码）。

CC复核本轮增量：

```bash
cd /Users/owen/.codex/worktrees/d9-recovery/Finance
git diff b7dc04be..1bf1d496 -- src/data/prosperity_quality.py src/data/prosperity_history.py scripts/verify_prosperity_history.py tests/test_prosperity_quality.py docs/design/prosperity-engine-north-star.md
```

原§7的专项命令仍有效，现预期162 passed。重跑报告请取新的输出文件名，不覆盖本次证据。待Boss单独批准merge，再分别push/部署。
