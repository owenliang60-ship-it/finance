# 013: 云端 cron wrapper 不能假设项目 `.venv` 存在

**日期**: 2026-04-24
**严重程度**: 中高（wrapper 直接起不来，broad universe maintenance cron 会持续空跑）
**根因**: `scripts/broad_universe_cron_wrapper.sh` 写死 `.venv/bin/python`，但 aliyun `/root/workspace/Finance` 的实际运行环境只有系统 `python3`

---

## 发生了什么

Broad universe v3 在云端 rollout 时，代码已经 pull 到最新，但第一次手工启动：

```bash
bash scripts/broad_universe_cron_wrapper.sh weekly_refresh
```

任务立刻失败，日志里是：

```text
.venv/bin/python: No such file or directory
```

这不是业务逻辑错误，也不是数据问题，而是 wrapper 连解释器都没找到。  
如果直接把这版写进 crontab，不会有告警，只会每天静默空跑。

## 根因分析

这次 broad wrapper 默认照搬了本地开发习惯：

```bash
PYTHON=".venv/bin/python"
```

但云端 Finance 不是"每个 repo 自带 venv"的运行模型，而是：

- 代码目录：`/root/workspace/Finance`
- 运行解释器：系统 `python3`
- `.env` 负责环境变量，不负责解释器路径

所以 wrapper 的隐含假设错了：

| 假设 | 实际 |
|------|------|
| 云端 repo 一定有 `.venv/` | 没有 |
| 本地能跑的 wrapper 云端也能跑 | 不成立 |
| 只要脚本 `chmod +x` 就能进 crontab | 还差目标机解释器 smoke |

## 修复

wrapper 改成运行时探测解释器：

```bash
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi
```

并在正式写入 crontab 前，先在 aliyun 手工跑一次：

```bash
bash scripts/broad_universe_cron_wrapper.sh weekly_refresh
```

确认 wrapper 本身能启动，再部署以下三条 broad maintenance cron：

- `15 7 * * 2-6 ... daily_hmcap`
- `20 7 * * 2-6 ... daily_price`
- `30 8 * * 6 ... weekly_refresh`

## 教训

- **cron wrapper 的第一责任不是业务编排，而是可靠启动**。解释器路径错了，后面所有任务设计都没有意义。
- **云端 runtime 是目标机事实，不是本地习惯的投影**。凡是 wrapper / entrypoint / shebang 类脚本，上云前都要在目标机器 smoke 一次。
- **`.env` 和 Python runtime 是两回事**。前者解决配置，后者解决可执行入口，不能混为一谈。
- **新 cron 落地必须按“代码 pull → wrapper smoke → 写 crontab → 再观察日志”顺序执行**，不能直接跳到最后一步。
