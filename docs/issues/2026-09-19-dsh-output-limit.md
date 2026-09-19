# DSH output cap interrupted a broad implementation brief

2026-09-19; Crypto trend ranking development, isolated worktree.

The first configured DeepSeek-V41-Flash SDK invocation read the plan/source/tests, then ended with `reason.kind=max-tokens`, `ok=false`, at the default 8192 tokens per model response. Git status confirmed no implementation edits. The answer file only contained a progress sentence, not completed work.

The second attempt narrowed to pure math/pool logic and allowed 16384 tokens per response, but hit the same limit before editing. The third narrowed to ONE mathematical function and its tests: completed, RED missing module then 15 passed. Codex implemented the other files, avoiding concurrent writes to DSH-owned files. Three calls total, unchanged configured provider/model; no Codex subagents.

Review caught an incorrect variance floor (`syy <= eps*n`) that discarded representable drift at 1e-10/bar. Added a reproducing failure, removed the arbitrary floor, and kept only actual zero-variation handling. DSH's success/report was not acceptance evidence.

Do not treat an idle runtime, answer file, or process termination as task completion. Private status/events live under work/crypto-trend-pool/run-* and must not be committed. Prefer a single function/file brief with an explicit small output over increasing the output cap for a broad task.
