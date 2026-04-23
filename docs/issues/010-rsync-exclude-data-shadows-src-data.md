# 010: `rsync --exclude 'data'` 会把 `src/data/` 一起排掉，导致快照环境静默残缺

**日期**: 2026-04-22
**严重度**: 中（业务代码没坏，但 smoke / 临时部署环境会缺模块，直接误判为 import 回归）
**恢复时间**: ~10 分钟定位 + 重新同步

## 发生了什么

在给 Portfolio Intelligence 的云端 smoke 准备 `/tmp/finance-pi-live-nav` 快照时，最初用了：

```bash
rsync -az --delete --exclude 'data' --exclude '.env' --exclude '.venv' ./ aliyun:/tmp/finance-pi-live-nav/
```

随后远端运行：

```bash
python3 scripts/portfolio_intelligence.py --dry-run
```

报错：

```text
ImportError: cannot import name 'get_price_df' from 'src.data'
```

检查后发现，`/tmp/finance-pi-live-nav/src/data/` 里只剩下个别手动覆盖过的文件，`__init__.py` 和其他 data layer 模块都没进去。

## 根因

`rsync` 的 `--exclude 'data'` 不是“只排根目录 `data/`”，而是会匹配任意层级名为 `data` 的路径段。

所以它不仅排除了项目根目录的 `data/`（这是本来想做的），也把 `src/data/` 整个目录一起排除了。

这类问题危险在于：

1. 同步命令本身不会报错
2. 目标目录仍然“看起来像有代码”
3. 真正爆炸是在运行时 import

## 正确做法

如果只想排项目根目录的数据目录，必须写成锚定根路径的模式：

```bash
rsync -az --delete \
  --exclude '/data' \
  --exclude '/.env' \
  --exclude '/.venv' \
  --exclude '/.git' \
  ./ aliyun:/tmp/finance-pi-live-nav/
```

## 教训

- `rsync exclude` 的语义不能靠直觉猜，尤其是常见目录名如 `data/`, `logs/`, `cache/`
- 做远端 smoke 前，除了看命令 exit code，还要抽查关键 package 文件是否存在
- 遇到 import error 时，先检查快照是否完整，再怀疑业务代码
