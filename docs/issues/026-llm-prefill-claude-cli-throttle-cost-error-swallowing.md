---
issue_id: 026
title: Concept v2 LLM prefill 经 `claude -p` 子进程 — 撞 API 限流、每次调用 $0.2、失败错误被脚本吞没
date: 2026-05-15
severity: medium
domain: concept-registry
status: documented
---

## 现象

Task 13 Step 3 全量 533 dry-run，跑到中途 67 个 symbol 的 LLM prefill 失败：

- 1 个（DELL）`timed out after 60 seconds`
- 66 个连续 `claude CLI failed (rc=1):` —— **错误信息全空白**，每个间隔 ~3s
  （那是 FMP 限流节奏，不是 LLM 耗时 → 说明子进程瞬间失败）
- 时间线：17:52 起一次超时 → 中间 13 分钟正常 → 18:05 起撞墙，66 连失败 → 18:09 结束

直接重测 `claude -p` 正常（exit 0）—— 是外部 API 临时限流，已自行恢复。
67 个失败票后续单独重跑：66 个修复，剩 RELX 再单独补 1 次成功。

## 根因

`terminal/llm_concept_prefill.py` 的 prefill 通过 `subprocess.run(['claude','-p',...])`
为每个 symbol 起一个独立 `claude` CLI 进程。三个问题：

1. **限流**：`claude -p` 子进程与当前会话共用同一 Anthropic 账户配额。Step 2(37 次)
   + Step 3 前段(124 次成功)累计 ~161 次调用后撞上 Anthropic 侧限流（rate/额度
   窗口），4 分钟内全拒，之后自行恢复。533 单进程串行调用必然中途撞限流。

2. **成本**：每次 `claude -p` 都是全新进程，要重建 ~26K token 的 prompt 缓存
   （返回 JSON 里 `cache_creation_input_tokens: 26189`），且**下次又是新进程、缓存
   永不命中**。一次 trivial 调用就 `total_cost_usd: 0.166`，带 taxonomy 的真实
   prefill 约 **$0.18-0.22/次**。Task 13 至此已花 ≈ $45。

3. **错误吞没**：`_run_claude_cli`（llm_concept_prefill.py:63）在 rc≠0 时只记
   `completed.stderr[:300]`。但 `claude -p --output-format json` 失败时错误是打在
   **stdout**（json envelope），stderr 为空 → 日志里 66 行错误全空白，根因诊断被
   脚本自己挡住。

## 影响

- 全量 dry-run 无法一次跑完，必然产生一批 `llm_failed`，需人工识别 + 单独重跑 +
  把修好的行拼回主 CSV（本次已用一次性脚本完成 66 行 merge）。
- 单价 $0.2 × 全量 LLM 调用数，刷新成本不可忽视；plan 从未估算过这块成本。
- 失败时日志无信息，诊断需手动直跑 `claude -p` 抓 stdout。

## 修复路径（候选，未实施）

1. **错误日志**：`_run_claude_cli` 失败时同时记 `stdout`（错误真正所在）。低成本，优先。
2. **成本**：prefill 改用 Anthropic SDK + prompt caching，taxonomy 段缓存一次、
   后续 533 次走 cache read（~10% 成本），可省 ~5-10×。属代码重构。
3. **限流**：加指数退避重试 + 分批；或改用 Batch API；或全量跑拆成小批跨时段。
4. **重跑机制**：脚本目前无「只重跑 failed」模式 —— `--symbols <json>` 会把
   priority list(107) 一并拖入。建议加 `--retry-failed <csv>` 直接读上轮 llm_failed 行。

## 教训

- 把 `claude -p` 当批量 API 调 N 百次：①与本会话抢配额、②每次重建缓存极贵、
  ③串行必撞限流。批量 LLM 打标应走 SDK + prompt caching + 退避重试，不是 spawn CLI。
- 子进程封装捕获错误时，**要确认目标 CLI 把错误打到 stdout 还是 stderr** ——
  `claude -p --output-format json` 是 stdout，只记 stderr 等于把诊断信息丢了。
- 长流程（533 串行）要预设「中途部分失败」是常态，设计阶段就要有重跑/续跑入口。
