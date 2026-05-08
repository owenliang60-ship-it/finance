---
issue_id: 023
title: git worktree add 后目标目录不一定空 — `.gitignore` 反 ignore 的 tracked 文件会被 checkout
date: 2026-05-08
severity: medium
domain: ops
status: documented
---

## 现象

Plan 假设 `git worktree add /root/workspace/Finance-feature feature/...` 创建出来的 worktree 是「干净空目录加上 git 元数据」。基于这个假设，直接做 `ln -s /root/workspace/Finance/data /root/workspace/Finance-feature/data`，期望把整个 data 替换成 symlink。

实际行为：

```
$ ls /root/workspace/Finance-feature/data
breadth_buy_quality

$ ln -s /root/workspace/Finance/data /root/workspace/Finance-feature/data
$ ls -la /root/workspace/Finance-feature/data
breadth_buy_quality                  # 原 tracked 目录还在
data -> /root/workspace/Finance/data # symlink 嵌套进去了，不是替代

$ ls /root/workspace/Finance-feature/data/pool/extended_universe.json
ls: cannot access ...: No such file or directory   # 因为路径变成了 data/data/pool/...
```

下游 `extended_universe_manager.get_extended_only_symbols()` 找不到文件而报错，smoke 直接卡在第 4 步。

## 根因

`Finance/.gitignore` 用反向规则把单个 `.gitkeep` 列为 tracked：

```
/data/*                              # 第 23 行：data/ 下默认全部 ignore
!/data/breadth_buy_quality/          # 第 24 行：但 breadth_buy_quality/ 反 ignore
data/breadth_buy_quality/*.csv       # 第 25 行
data/breadth_buy_quality/charts/     # 第 26 行
!data/breadth_buy_quality/.gitkeep   # 第 27 行：保留 .gitkeep
```

只要 git 里有任何一个 tracked 文件落在 `data/` 树下，`git worktree add` 就必须把那个目录建出来。`data/breadth_buy_quality/.gitkeep` 即使是 0 byte 占位文件也会触发整条父目录链。

`ln -s SRC DEST` 在 `DEST` 是已存在的目录时不会替代，而是把 symlink 创建在 `DEST/` 内部（变成 `DEST/<basename SRC>`），这是 ln 的 historical UNIX 语义而非 bug。

## 触发场景

- 任何 plan 假设新 git worktree 是「空白沙盒」+ 用 symlink 共享父目录数据
- 反 ignore 规则下任何被 tracked 的 placeholder 文件（`.gitkeep` / `.keep` / `README.md` 占位）都会触发
- 不限 Finance 项目；任何用 `!path/file` 结构的 `.gitignore` 都中招

## 解决路径

`ln -s` 之前显式 `rm -rf` 目标目录：

```bash
ssh aliyun "rm -rf /root/workspace/Finance-feature/data"
ssh aliyun "ln -s /root/workspace/Finance/data /root/workspace/Finance-feature/data"
```

清理时反着来——rm symlink 后 worktree 的 `data/` 缺失，`git status` 报 tracked file deleted，必须先还原再 remove worktree：

```bash
ssh aliyun "rm /root/workspace/Finance-feature/data"
ssh aliyun "git -C /root/workspace/Finance-feature checkout HEAD -- data/"
ssh aliyun "git -C /root/workspace/Finance worktree remove /root/workspace/Finance-feature"
```

## 适用范围

- 所有用 `git worktree add` + symlink share 父目录数据的方案
- Finance 仓库当前 `.gitignore` 反 ignore 的所有路径（grep `^!` 查）

## 二次防线

- 写涉及 worktree+symlink 的 plan 时，**先在主工作区跑一次** `git worktree add /tmp/scratch <branch>` 看一下 `/tmp/scratch/` 实际有什么——不要靠想象 worktree 是空的
- 或者在 plan Step 里强制加 `ls <worktree>/<target> 2>&1` 探测一次，再决定 `ln -s` 还是 `rm -rf && ln -s`
- 别相信 "worktree add 出来是空目录" 这个直觉

## 备注

本次（2026-05-08）forward EPS 扩展池 smoke 一次踩中。Plan v4 已 codex 三轮 review 还是漏掉这个事实，因为 review 都是在主工作区里读代码，没人去本地或云端真跑一次 `git worktree add` 看输出。Plan v4 已加修正注解，下次任何 worktree+symlink 计划必须先做 dry-run 探测目录状态。
