# M9 本地实现与验收 — 2026-10-24

T0–T5 已实现；T6 的历史重建及独立验证通过。本周 live（A8）等待今日基本面刷新后执行。未 commit、merge、push 或部署。D5 已定为 B + A（云端留两个版本、旧版本归档到本地；备份 gzip），实现见计划 Task 8。

## 检查结果

| 检查 | 结果 |
|---|---|
| 相关回归 | 430 passed；见 target-tests.txt |
| 全量回归 | 4919 passed、12 failed、4 skipped；12 项与 M8 基线一致，无新增失败 |
| 数据依赖复核 | 接入只读数据后 187 passed、1 skipped，覆盖上述 12 项 |
| 独立审查 | 2 个子代理交叉审查非本人实现；成立发现已红绿验证修复 |
| 历史重建 | 21 季、168 张榜单，每批读回与内存一致 |
| A9 | 21 季冻结日独立重算一致；60 个源库财季日期/quarter_bucket 抽查一致 |
| 全链耗时（缓存优化前） | 1271.15 秒 |
| 结果库 | 307,412,992 bytes（307.4 MB） |
| 峰值内存（优化前全链） | 2.50 GiB |
| 缓存优化后最大季度复测 | 933 只，74.28 秒，峰值 1.65 GiB；八榜读回一致 |

缓存只在同一季度的未达标候选周之间复用；决定冻结后，在准备八榜前释放历史缓存。优化后单季结果与优化前同季业务内容逐项比对见 memory-comparison.json。未重复宣称测得优化后的全链耗时。

## 数据与范围

- engine code_version: `1d24cbda444df7fa`
- 固定输入 SHA256: `39caa73e5aadc89e23d55e0ab405db56988d3449e1607a7fbc86339cbf6e7ef7`
- 输入快照: `data/m9-acceptance/market-history.db`
- 结果库: `data/m9-acceptance/prosperity_research.db`
- 输入源为本次云端 pull 后的固定只读副本；运行前后 SHA256 相同。
- 历史行保留 M4 原有 approximate / strict 标签；独立验证不声称重新实现全部 M4 成员资格逻辑。
- 本周 live 尚未执行：应在 finance_fundamental 结束后先复制本周质量报告，再 pull 新快照，并验证 report.after.as_of ≤ cutoff。
- 没有发布挂点，M9 本身不发送 Telegram、不生成精选池或网页。

## 冻结结果

| 财季 | 冻结周六 | 原因 | 覆盖率 |
|---|---|---|---|
| 2021Q2 | 2021-09-04 | coverage | 98.87% |
| 2021Q3 | 2021-12-04 | coverage | 98.12% |
| 2021Q4 | 2022-03-19 | coverage | 95.31% |
| 2022Q1 | 2022-06-04 | coverage | 97.49% |
| 2022Q2 | 2022-09-03 | coverage | 98.49% |
| 2022Q3 | 2022-12-03 | coverage | 97.34% |
| 2022Q4 | 2023-03-25 | forced | 93.85% |
| 2023Q1 | 2023-06-03 | coverage | 96.63% |
| 2023Q2 | 2023-09-02 | coverage | 98.31% |
| 2023Q3 | 2023-12-02 | coverage | 97.60% |
| 2023Q4 | 2024-03-23 | forced | 94.02% |
| 2024Q1 | 2024-06-01 | coverage | 96.67% |
| 2024Q2 | 2024-08-31 | coverage | 98.82% |
| 2024Q3 | 2024-11-30 | coverage | 98.10% |
| 2024Q4 | 2025-03-22 | forced | 94.39% |
| 2025Q1 | 2025-05-31 | coverage | 97.54% |
| 2025Q2 | 2025-08-30 | coverage | 98.76% |
| 2025Q3 | 2025-11-29 | coverage | 98.77% |
| 2025Q4 | 2026-03-21 | forced | 96.13% |
| 2026Q1 | 2026-05-30 | coverage | 98.48% |
| 2026Q2 | 2026-08-29 | coverage | 98.85% |

## 部署前容量

按当前21季冻结链每版本 0.307 GB、每年26个新版本及0.69 GB周榜估算，含两份备份约 26.0 GB/年；每周换版本约 50.0 GB/年。未来季度增加还会扩大冻结链，不能把此数字当固定上限。D5 已定为 B + A：实测 gzip 压缩比 27.5%（307 MB → 84.5 MB），云端稳定在约 3 GB。Task 8 完成前不部署。

## 复现入口

- `acceptance.py`：固定源快照、落库读回、时间/内存/体积测量。
- `verify_freezes.py`：不导入景气代码的候选周与 SQL 抽查。
- 运行与恢复说明：`docs/runbooks/prosperity-weekly.md`。

## D5 实现（Task 8，2026-10-24）

| 检查 | 结果 |
|---|---|
| 景气全套 | 436 passed（新增 A18–A21 共 14 项） |
| 全量回归 | 4946 passed、4 skipped、0 failed；接入只读 live 数据后，原 12 项数据依赖基线失败全部通过 |
| 真实库副本冒烟 | 21 季单版本 307 MB：export 168 s，verify 151 s，remove 156 s，峰值 RSS 约 0.68 GB；移除后 0.1 MB，quick_check ok，移除前 gzip 备份 97 MB |
| 验收库 | 冒烟只用私有副本，`data/m9-acceptance/` 未改动 |

备份格式升为 `prosperity-bundle-v2`（数据库 gzip，清单记录压缩前 SHA256），v1 备份不再被识别；云端未部署，无存量。
操作步骤见 `docs/runbooks/prosperity-weekly.md`「按版本归档」。`/code-review high` 尚未运行，排在 A8 之后、commit 之前。


## A8 本周 live 验收（2026-10-24，通过）

- 顺序：云端 finance_fundamental 15:01 CST 结束（FAIL rc=1，50 只未补全）→ 复制质量报告 `fundamentals-20261024T060001Z.json`（sha256 `13c37c8b…`，after.as_of 07:00:29Z，覆盖 859/894 = 96.1%）→ 19:59:45 CST `sync_to_cloud.sh --pull` → `.backup` 固定只读副本 `data/m9-acceptance/market-live-20261024.db`（sha256 `36ddf0eb…`，quick_check ok）。
- observed-at 取本地 pull 完成时刻 2026-10-24T11:59:45Z，不是云端快照生成的精确时刻；两者都晚于报告、同属本周六，不影响门控。
- 四个门控全 pass：integrity、fundamental_refreshed（ratio 0.961）、input_health（894 成员 0 错误，ranked 841 ≥ 上周 871×0.9）、forward（本周 complete）。
- 先重建 21 季冻结链，冻结周与历史验收逐季一致；22 次落库（21×8 榜 + 本周 4 张周榜）读回全部一致；运行前后输入 SHA 不变。
- 耗时 1445.7 s，峰值 RSS 1.68 GiB，结果库 319.6 MB。明细见 live-a8.json、live-a8.log。

## 主窗口审查（2026-10-24）

- 一项成立并已修：`sync_to_cloud.sh` 在 `set -e` 下，景气结果库拉取失败会中止整个 pull（market.db 已换新，fundamental/universe/健康检查被跳过）。现改为失败只告警、保留本地旧副本、后续步骤继续。先补红测试 `tests/test_sync_to_cloud.py::test_prosperity_pull_failure_does_not_abort_remaining_pull`，修后转绿。
- 云端 Python 3.10.12 临时副本跑景气全套 436 passed（修复前）；临时目录已删。
- 修复后本地：sync + 景气测试 445 passed。
- T7 风险：云端 2 核 3 GB（可用约 2 GB），本地 live 峰值 1.68 GiB；首次云端运行需重建 21 季链。部署时先手动跑链重建并观察内存，再装 cron。
