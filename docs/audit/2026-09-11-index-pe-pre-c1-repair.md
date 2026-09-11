# 三指数 PE：C1 前四项修复验收

日期：2026-09-11。分支：`codex/index-pe-morning-chart`，接手提交 `f55a927`。

Boss 授权：修复当前问题，到 C1 停止。沿用原隔离 worktree，接手 4 个已有未提交文件；未实施 C1、未合并、未推送、未调用远端 API 或修改生产数据。

对应北极星第一层 Data → 第二层 Analysis；执行既有 plan 的 Task 0–4 修复，不扩展到 Task 5。主线程单遍审查，零 subagent。

## 修复结果

| 问题 | 当前行为 | 验证 |
|---|---|---|
| P1-A 旧表丢失 run_id | 空旧表重建，强制 NOT NULL，旧列缓存失效；有历史行则拒绝自动迁移并保留数据 | 旧空表写读成功、NOT NULL、缓存污染、非空拒绝；verifier 拒绝缺列 |
| P1-B hindsight 零对账放行 | 必需 window 全行检查；估计季度要求成员 snapshot date；某篮子样本全部无法重建则失败，并输出各篮子重建数 | 删除全部证据、删除非抽样行证据、虚构窗口、跨篮子抵消、缺估计 vintage 均被拦截 |
| P1-C manifest 起点与顺序 | 按物理 rowid 顺序检查起点唯一且第一、终态唯一且最后；不以 event_seq 重排事件 | 缺起点、逆序伪造、旧异常 run 后追加合法 run 均失败；正常 retry 保持通过 |
| P1-D 成员分母自证 | 先按 holding_date/source_kind 区分快照，再按估值日 effective/available 双门控及冻结优先级选一份；逐行核对快照归属、完整成员集和权重 | 删掉 40% 成员及缩小其权重被拦截；未来快照不污染历史；live/disclosure 并存、后披露前 live、covered_by 合并正常 |

选择规则与生产端同一契约、独立实现：最新 effective date；同日 disclosure 优先于 live；同源同生效日取最新 holding_date。保留现有成员 payload 的 holding_date/weight_basis 作为归属证据，无新增业务表或生产接口。

## 验证证据

- 首轮新增边界测试先得到 **8 failed / 1 passed**，失败均对应快照混算、缺快照跳过或非抽样证据遗漏；修复后通过。
- **473 passed**：覆盖 17 个测试文件，包括全部 MarketStore 套件、FMP forward client/ingestion、历史 SOXX 采集/验证、三指数计算与 verifier；10 个既有弃用警告。
- producer → 临时 SQLite → 独立只读 verifier：SPY/QQQ/SOXX 各 3 周，同窗口重跑两次，每篮子 6 个 hindsight 成员收入成功独立对账。使用 `sample=50`。该测试使用合成数据，不能替代云端真实五年覆盖验收。
- Python 3.10 语法检查与 `git diff --check` 通过。
- 本轮没有重跑全仓测试，不宣称先前的 2749/7 基线在当前环境得到复核。

## 明确停点

**C1 尚未实现。** `backfill_index_pe_history.py` 仍调用现有 batch upsert，没有增加窗口外删除或原子整窗替换。已有“增量 tail 刷新应拒绝”的测试保留；新增测试只验证相同窗口重跑，不把它冒充滑动窗口验收。

下一步仍是 C1：五年窗口整批写入、窗口外旧行处理及向前滑动一周回归；完成之前不应打开 recurring 生产写入。真实历史数据准备、云端覆盖认证与晨报接线也未在本次执行。
