# 010: worktree 里的空壳 market.db 会遮蔽主仓库共享数据

**日期**: 2026-04-23
**严重度**: HIGH
**恢复时间**: ~20 分钟

## 发生了什么

在独立 worktree 里跑新的事件研究 CLI 时，脚本能启动，但 `USStocksAdapter` 始终只加载到 `0` 只股票。日志里没有价格读取报错，看起来像 universe 为空。

## 根因

问题不是 `extended_true` 本身，而是 worktree 自己带了一个体积很小的 `data/market.db` 空壳库。

旧逻辑只看：

- `data/` 目录是否存在
- `data/market.db` 文件是否存在

只要这两个条件成立，就把当前 worktree 视为“可用数据根”。结果：

- 路径解析命中 worktree 的空壳 DB
- `daily_price` 表为空
- 所有依赖 `market.db` 的回测/研究适配器都可能静默得到空 universe

## 修复

共享数据根的判定改成：

- 不是只看文件存在
- 而是检查 `market.db` 里是否真的存在 `daily_price` 数据

若当前 worktree 的 `market.db` 是空壳，则回退到主仓库的数据根。

## 教训

- worktree 场景下，`文件存在` 不等于 `数据可用`
- 共享数据路径解析必须验证最小可用性，而不是只做路径判断
- 这类问题最危险的地方在于：不会报出明显错误，只会让研究结果看起来像“没有样本”
