# 动量多空1号 — 阶段 1 证据与剩余门槛

日期：2026-10-20。**阶段 1 离线实现完成；门 1 尚未通过。**
Boss 已批准执行；demo 配置由 Boss 稍后提供。未调用 demo 或生产认证接口，未下单，未部署，未改 cron。

## 当前交付

|任务|状态|证据|
|---|---|---|
|1.0 来源同步|完成|v2 runbook 与主 checkout 哈希一致；D1–D6 过期措辞已修正|
|1.1 冻结夹具|完成|162/162 冻结哈希通过；6 个锚点，469 KB，附来源清单|
|1.2 策略内核|完成|GC1–GC4、GC6 通过；单次逻辑锚点调用，Decimal 成本和两波拆分|
|1.3 客户端/合约规则|离线完成|签名、错误分类、限频、并发、Decimal 规则测试通过；真实私有请求权重仍待 demo 测量|
|1.4 demo 探针|框架完成，实测未开始|G1–G5 基础流程和安全清理已实现；完整协议矩阵仍需实测/补充场景，见 probe-usage.md|
|1.5 准备核心|完成|公开行情自有客户端＋共享 build_report/评分/排名，CLI 与历史市场重放通过|

## 金样本

|检查|结果|
|---|---|
|GC1|3,750 行 Fisher/trigger/regime 对冻结结果完全一致，最大数值误差 0|
|GC2|89 个周锚点、1,272 个目标仓位一致；包含冻结账户初始周前的持仓和资金费|
|GC3|2,174 笔成本记录一致。Decimal 对 CSV/float 证据容差 rtol=1e-12、atol=1e-12；最大绝对误差约 6.64e-11|
|GC4|8 次止损的资格边界通过；实际重入由全历史 GC2 验证，不强制下一周重入|
|GC5|09-20 / 09-27 / 10-04 / 10-11 四日全池重算：集合、状态、分数、多空顺序一致；另独立核对 prepare 两侧输出|
|GC6|看多 100% 空＋50% 多、看空 100% 空、缺槽留现金通过|

GC5 来源：只读复制云端已有缓存，共 1,058 个文件；22 个缺少日线缓存的合约另取公开 REST 历史日线，单独记录来源和哈希。回放期间禁用网络，缓存写入临时目录。

10-11 的冻结存档包含 CTUSDT 历史不足状态。GC5 复现该状态；实盘 prepare 按 D5 拒绝产生 ready 结果。这是历史结果一致，不是该日实盘数据准入通过。

完整本机证据：`work/momentum-ls-01-protocol/goldcheck.json`。原始回放数据与 red/green 检查日志也在该目录，不进入源码提交。

## 协议门

G1–G5 均为 **NOT_RUN**。离线假响应不能证明交易所接受两张保护、减仓共存或真实 entryPrice 语义。

探针每个完整组只会返回 PARTIAL / BLOCKED / FAIL。单项读回成功不自动升级为完整协议 PASS。自然触发、部分成交、拒单、资金费/重启等剩余场景见 [探针说明](probe-usage.md)。

G2/G3 未完成前，北极星 §5.2 的“减仓是否换保护”仍未定死。若交易所不支持首选方式，按计划回设计。

## 审查与验证

- 独立只读审查覆盖内核、客户端、准备核心、GC5、CLI 与探针。
- 已修复并复核关闭 2 项 P2：清理在归属未知时保留保护；GC5 独立验证空头排名与 prepare 输出。
- 审阅修复后的专项测试：105 passed。Python 3.10 AST 语法检查与 py_compile 通过。
- 全仓回归：12 failed / 4,850 passed / 4 skipped，313.40 秒。基线：12 failed / 4,752 passed / 4 skipped；失败测试 ID 完全相同，无新增失败。全仓运行收集了 98 项新测试；审阅修复后另跑最终专项 105 passed，包含新增的 7 项回归测试。
- 基线与全仓日志：`work/momentum-ls-01-protocol/baseline-tests.log`、`final-tests.log`。只保留隔离 worktree 的数据依赖失败，没有链接或写入真实数据库来消除它们。

## 下一步

1. Boss 准备 `~/.momentum_ls01/demo.json`（0600，api_key/api_secret）及 demo 测试余额；不把密钥发进聊天。
2. 选择明确的测试合约、数量和触发价，运行 demo 探针，完成完整 G1–G5 矩阵并保留真实证据。
3. 根据 G2/G3 的真实结果明确减仓保护方式，并回填私有请求权重。
4. Boss 审阅门 1 后才进入阶段 2。当前没有实现或启用实盘调仓/分钟守护。

## 复现入口

```bash
python -m trading.momentum_ls01.cli goldcheck --help
python -m trading.momentum_ls01.cli prepare --help
python -m trading.momentum_ls01.cli probe --help
python -m pytest tests/test_momentum_ls01_*.py -q
```

本机使用主 checkout `.venv/bin/python`。完整 goldcheck 还需显式传入冻结目录、两份 BTC 周线源、回放目录及历史报告目录；缺任何材料不会默认为通过。
