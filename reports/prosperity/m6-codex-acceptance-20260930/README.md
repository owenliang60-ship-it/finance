# 景气 M5–M6 独立验收（2026-09-30）

结论：主体功能验证通过；发现 1 个 P2 版本身份缺口，建议修复后完成最终验收。本次未修改产品代码、未合并、未推送、未部署。

- 分支：`prosperity/m5-m6-kernel`
- 被验收提交：`00edb0e7`；基线：`31fcdf38`
- 方案：主工作区 `docs/plans/2026-09-29-prosperity-m5-m6-scheme-and-kernel.md` v1.3，D-1…D-9
- code_version：`c453bf09028a8e6f`
- 数据：已验收固定快照 `data/backups/prosperity/phase0-accepted-input-20260929/market.db`，没有改用当前云库
- SHA256：`3caffdb7e7c2d2d9c5124fcbf364e46cf32d51307dde3d9ea49bf8ecf54800e2`

## 待修复：P2 回放成员配置未进入版本哈希

位置：`terminal/prosperity/version.py:17–28`，尤其 `src/data/symbol_aliases.py` 登记项（第 25 行）。

引擎记录了别名解析 Python 源码，却没有记录它读取的 `config/symbol_aliases.json`。别名或并购生效日期改变会改变历史成员集合，继而改变标准化截面与榜单；此时 `scheme_hash` 和 `code_version` 都不变，冻结参数包的版本校验仍放行。计划的源码清单也漏列该 JSON，需要一并补齐。

主线程独立复现：把全部哈希源码及 JSON 复制到临时目录，只改临时 JSON；在内存 SQLite 中放 ABC 与 COR 同日各 200 亿美元市值。删除 ABC→COR 配置前成员为 `[COR]`，删除后为 `[ABC, COR]`；两次版本均为 `c453bf09028a8e6f`，旧冻结包仍通过 `standardize` 校验。未修改真实别名文件或数据库。完整结果见 `alias-version-repro.json`。

最小修复：将 `config/symbol_aliases.json` 纳入版本哈希，增加真实文件内容变更会改变版本、旧冻结包因版本不符被拒绝的回归用例。无需调整公式、权重或投资口径。

## 已通过的验收

| 检查 | 本次独立结果 |
|---|---|
| 景气测试 | `232 passed in 1.47s` |
| Python 3.10 语法 | 18 个变更 Python 文件通过 AST 3.10 grammar 解析；不是云端运行时测试 |
| live，2026-09-29 | 915 包，0 错误、0 泄漏；9.5 秒 |
| replay，2024-08-17 | 817 包，0 错误、0 泄漏；11.0 秒 |
| 旧验收榜单重现 | 两日期×四方案共 8 份 JSONL 逐字节一致；summary 只有耗时不同 |
| 点名样本 | summary 与原验收一致，13 只的已记录边界均保留 |
| 原始因子独立复算 | 18 项比较全部吻合，包含明确缺失值的一致性 |
| 打分独立复算 | 5,832 个排名行；原始覆盖门、标准化、摊权、斜率调整、评级/降级、排序一致；最大差 2.842170943040401e-14 |
| D9 SUE 抽取 | 同快照、同 run_ids 的 `build_report` 与抽取前报告所有计算字段一致 |
| 只读 | CLI 使用 `MarketStore(read_only=True)`；已有专门测试通过 |

打分复算读取方案配置，独立实现均值/总体标准差、线性分位点、中位秩、合成和评级，没有调用评分内核；未把相同内核重跑当独立复算。详见 `independent-scoring.json`、`independent_check.json`、`verification.json`。

D9 首次重跑时误用了只含 `sha256` 的本地保存 manifest，CLI 拒绝（缺 `created_at`）；改用同一快照调用既有只读 `build_report`。最初未传历史 `run_ids` 导致缺口归因不同；恢复原报告的 `d9-full-20260928` 后全部计算字段一致。这两项是验收命令输入差异，未修改产品逻辑。

## 命令

在该 worktree，统一使用主工作区 `.venv/bin/python`：

```bash
PY='/Users/owen/CC workspace/Finance/.venv/bin/python'
DB='/Users/owen/CC workspace/Finance/data/backups/prosperity/phase0-accepted-input-20260929/market.db'
OUT='reports/prosperity/m6-codex-acceptance-20260930'
"$PY" -m pytest tests/test_prosperity*.py -q --tb=short
"$PY" scripts/prosperity_score.py --as-of 2026-09-29 --mode live --observed-at 2026-09-29 --db "$DB" --out-dir "$OUT/live"
"$PY" scripts/prosperity_score.py --as-of 2024-08-17 --mode replay --db "$DB" --out-dir "$OUT/replay" --no-beta
"$PY" reports/prosperity/m6-acceptance-20260930/independent_check.py --db "$DB" --board "$OUT/live/board-F1-2026-09-29.jsonl"
"$PY" -m pytest tests/ -q --tb=short
```

## 已知限制与范围

不足四季的 revision 是否年化、负 NTM E/P 是否单列，均为此前待拍板口径；当前实现符合已批准方案，本次不将其列为新增缺陷。历史期没有 NTM 周快照、BMNR 历史市值异常、COST 财季配对、APH 短孤岛等既有限制仍存在；通过 M5/M6 工程验证不等于历史排名已具备投资有效性，也不代替 M7 对拍与后续研究验收。

使用 2 名独立审查代理：因子/方案、内核/版本边界。主线程检查源码、独立重跑数据与测试，并物理修改临时副本复现上述 P2。

## 全量回归对照

- 分支全量：`12 failed, 4623 passed, 4 skipped, 17 warnings in 281.40s`，见 `pytest-full.txt`。
- main 全量：`1 failed, 4574 passed, 17 warnings in 510.72s`，见 `pytest-baseline.txt`。
- 两套目录的未跟踪数据不同，不能直接比较 12 与 1。分支的 5 项失败来自缺 `data/breadth_study_1b/daily_breadth.csv`，2 项来自本地骨架库无价格，5 项来自概念注册表缺失后回退旧分类。
- 对分支全部 12 个失败节点，在 main 源码下重新运行，进程内仅将 `run_breadth_buy_quality` 的数据路径及 `concept_classifier` 使用的只读 MarketStore 指向同一 worktree 数据：`12 failed in 0.28s`。失败节点集合完全一致，见 `pytest-baseline-matched-data.txt` 与 `verification.json`；没有改动文件或产品代码。
- main 的单个失败是历史成交额集中度与研究 CSV 对拍（47.82090163626441 对 47.80391692200165），并非景气变更；分支缺少对应真实数据，因此在该环境中跳过。相关晨报、广度实现及测试与 main 没有代码差异。
- 全量分支比基线多 64 项测试，均已覆盖；上述数据环境归因与定向基线复现未发现新增产品回归。全套测试并非全绿，P2 版本指纹问题也不被这些通过结果抵消。
