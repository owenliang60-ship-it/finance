# 景气引擎 D9 历史补数运行手册

> Plan：`docs/plans/2026-09-27-prosperity-m2-d9-history-backfill.md` · 北极星第一层 P2/P3
> 全部在云端 `/root/workspace/Finance` 执行。**每个写库步骤前停下来等 Boss 确认。**
> 写库步骤都拿 `market_db_writer` 锁，锁忙退出 75、不留痕迹，换个时间重跑即可。
> 避开 06:25–08:30（Tue–Sat 更新与晨报）和整个周六。

## 0. 前置

- M1、M2 已 merge、push，云端 `git pull --ff-only` 后 `git log -1` 与本地一致
- `df -h /root` 可用空间 ≥ market.db 大小 × 1.5 + 1G（备份 + 补数增量约 200MB）

## 1. 冻结目标清单（只读）

```bash
python3 scripts/verify_prosperity_history.py targets --out data/prosperity/d9_targets.json
```

预期约 1,184 只（20 个季末）。

## 2. 补数前基线报告（只读）

```bash
python3 scripts/verify_prosperity_history.py report --targets data/prosperity/d9_targets.json \
    --date baseline-$(date +%F)
```

## 3. 备份（写文件，不写库）

```bash
python3 -c "import sys; sys.path.insert(0, '.'); from pathlib import Path; \
from scripts.build_company_concept_registry import _backup_sqlite; \
print(_backup_sqlite(Path('data/market.db'), 'pre-d9-backfill'))"
```

手动 `pre-*` 标签不参与自动修剪；验收后经 Boss 确认再删。

## 4. 三表 canary（写库，20 只 × 3 表 = 60 次调用）

```bash
python3 scripts/backfill_extended_fundamentals.py --run-id d9-canary-YYYYMMDD \
    --targets-file data/prosperity/d9_targets.json \
    --datasets income,balance,cashflow --limit-quarters 40 --canary 20
python3 scripts/backfill_extended_fundamentals.py --run-id d9-canary-YYYYMMDD --verify-only
```

检查：20 只都有接近 40 季的三表、`fundamental_vintage` 只追加、`profiles.json` 的 mtime 没变。

## 5. 三表全量（写库，约 3,550 次调用，约 2 小时）

**用新的 run_id**：canary 已把 20 只冻结进它自己的清单，同一 run_id 加 `--resume` 只会续跑那 20 只。

```bash
nohup python3 scripts/backfill_extended_fundamentals.py --run-id d9-full-YYYYMMDD \
    --targets-file data/prosperity/d9_targets.json \
    --datasets income,balance,cashflow --limit-quarters 40 \
    > logs/d9_backfill_full.log 2>&1 &
```

中断或熔断：原命令加 `--resume` 续跑。结束后 `--verify-only`。

## 6. 三表补完后的报告（只读）

```bash
python3 scripts/verify_prosperity_history.py report --targets data/prosperity/d9_targets.json \
    --run-id d9-canary-YYYYMMDD --run-id d9-full-YYYYMMDD --date after-statements-$(date +%F)
```

看三表门槛和 street EPS 缺口的分布。这一步**不写**缺口清单。

## 7. remap（写库，不调 API）→ 冻结 street EPS 缺口清单（只读）

```bash
python3 scripts/backfill_street_eps.py --targets-file data/prosperity/d9_targets.json --remap-only
python3 scripts/verify_prosperity_history.py report --targets data/prosperity/d9_targets.json \
    --run-id d9-canary-YYYYMMDD --run-id d9-full-YYYYMMDD --date after-remap-$(date +%F) \
    --eps-targets-out data/prosperity/d9_eps_gaps_r1.json
```

remap 只改 `match_method='none'` 且财季为空的已公告行，标 `statement_window`，数值不动。
缺口清单一经写出即冻结：`--eps-targets-out` 拒绝覆盖已存在的文件，因为补数进度绑定清单文件的 sha。

## 8. street EPS 补数（写库，按冻结清单，预计 ≤400 次调用）

```bash
python3 scripts/backfill_street_eps.py --targets-file data/prosperity/d9_eps_gaps_r1.json
```

进度在 `data/prosperity/street_eps_progress_d9_eps_gaps_r1.json`，中断后原命令续跑；返回空的票每次续跑都会重试。
已有财季映射永不被覆盖；新映射与已占用财季冲突时置为 `none`，汇总里的 `mapping_conflicts` 要逐只看。
最终报告：重跑第 6 步的命令（`--date final-...`），**不要**带 `--eps-targets-out`。
还要补下一轮时，写新文件 `d9_eps_gaps_r2.json`，它会用自己的 progress 文件。

## 9. 完整性

```bash
python3 -c "import sqlite3; print(sqlite3.connect('file:data/market.db?mode=ro', uri=True).execute('pragma quick_check').fetchone())"
```

本地：`./sync_to_cloud.sh --pull`，再对本地 `data/market.db` 跑同样的 quick_check（顺带替换损坏的本地副本）。

## 10. 收尾

报告结论写进 issue / audit；更新北极星"各层完成度"和冻结参数复核值；Boss 看报告定 street EPS 门槛；Boss 确认后删 `pre-d9-backfill` 备份。

## 回滚

三表按 (symbol, date) 覆盖更新，vintage 只追加；earnings 按 (symbol, announce_date) 覆盖更新。出现异常时：停掉 cron 写入 → 用 `pre-d9-backfill` 备份整库恢复（按 M0 的方式先写临时文件，`quick_check` 通过后再原子替换）。
