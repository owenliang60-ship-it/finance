# BTC 奶牛（BitCow）日线信号监控

Status: 代码已在 `feat/bitcow-monitor` 分支完成并通过离线测试；尚未部署、尚未装 cron。

每天 UTC 日线收盘后跑一次，读 Binance BTCUSDT 1d 与 Deribit BTC DVOL，向 Telegram 美股日报群组发一条面板（与晨报同一 bot，`channel="group"` / `TELEGRAM_GROUP_CHAT_ID`）。
`cron_wrapper.sh` 的故障告警仍发私聊（`TELEGRAM_CHAT_ID`），不进群。
有新信号时首行以 `🚨` 开头；没有新信号时只发状态面板。

## 信号定义

| 层 | 规则 | 报警（🚨）条件 |
|----|------|----------------|
| 慢层 | UTC 周日日线收盘 vs 50 周 SMA | 当天是周日且方向翻转 |
| 中层 | EMA84 ± Wilder ATR14 通道，严格突破翻向 | 翻向（= 触发 ATR 止损） |
| 中层预警 | 收盘跌破/站上 EMA84 中线（mid）；距止损沿不足 0.5 ATR（near） | 新进或从 mid 升级到 near；持续不重复报警 |
| 偏离 D | `D = (close - EMA84) / ATR14`，档位 ±4.5 / ±6.5 / ±7.0 | 新进、升档或符号翻转；同档持续不重复报警 |
| DVOL | 截至报告日的连续 365 个 UTC 日的分位（缺任一天即「样本不足」） | 只展示，不报警；不可用时写明原因 |

代码：`scripts/bitcow_monitor.py`；入口：`scripts/run_bitcow_monitor.sh`；测试：`tests/test_bitcow_monitor.py`（离线 fixture `tests/fixtures/bitcow/btcusdt_1d.csv`）。

## 运行语义

- 数据日期：`as_of` 固定为 UTC 昨日。Binance 末根不是 UTC 昨日、有缺口/重复、少于 900 根、OHLC 非法时 exit 1，不发面板、不写 state；cron_wrapper 发失败告警。
- DVOL 失败只降级：面板写 `DVOL：⚠️ 不可用（接口异常/数据过期/样本不足）`，其余照发。
- 防重：`data/bitcow_monitor/state.json` 记 `last_sent_date`。同日重跑直接 exit 0，不发网络请求。`--force` 忽略防重；`--dry-run` 只打印，不发不写。
- Telegram 发送返回失败时 exit 1，不写 state，下次运行会重发。
- state 只接受 `.json` 路径；损坏的 state 视为空并照发。
- 补报：`last_sent_date` 早于 UTC 前天（中间有漏发日）时，恢复当天的消息带 `🚨`，逐条补报漏发期间的慢层/中层翻向及原始日期。只自动补报报告日前 90 天内的翻向：这段数据前面有足够的指标预热，也不会因为长时间停跑而超出 Telegram 4,000 字上限。漏发超过 90 天时，先写一条「漏发超90天（上次 YYYY-MM-DD），仅补报近90天内翻向」。偏离 D 和中层预警不补报。`--dry-run` 不读 state，不补报。
- 起始日：约 1000 日窗口内没有观测到翻向时，面板写「至少自 YYYY-MM-DD」，不冒充真实翻向日。
- Binance 429：遵守 `Retry-After`（上限 15 秒）；超过上限或无法解析时立即失败，不提前重试，避免触发 418 封 IP。
- 并发（已知限制）：cron 重入由 `cron_wrapper.sh` 的 `finance_bitcow.lock` 防护；直接调用 `run_bitcow_monitor.sh` 不取锁。手动补跑请在当天 cron 结束或失败之后执行，不要与 cron 同时跑。极小的重叠窗口按 at-least-once 处理（可能重复发一条，不会漏发）。

## 安装（云端）

Cron 时区为 Asia/Shanghai。08:03 = UTC 00:03，在 UTC 日线收盘后 3 分钟。

1. 拉代码并跑测试与语法检查：

   ```bash
   cd /root/workspace/Finance
   git log --oneline -1
   python3 -m pytest tests/test_bitcow_monitor.py -q
   bash -n scripts/run_bitcow_monitor.sh
   ```

2. dry-run（不发送、不写 state）：

   ```bash
   scripts/run_bitcow_monitor.sh --dry-run
   ```

3. 冒烟。这一步会真实发一条带 `【SMOKE】` 标签的消息到美股日报群组，群成员都能看到。每次新建临时目录做 state，不占用生产当日防重，也不会读到上次冒烟的旧 state。任何一步失败，整段以非零码退出：

   ```bash
   (
   set -euo pipefail
   cd /root/workspace/Finance
   SMOKE_DIR="$(mktemp -d /tmp/bitcow-smoke.XXXXXX)"
   trap 'rm -rf -- "$SMOKE_DIR"' EXIT
   scripts/run_bitcow_monitor.sh --label SMOKE --state-path "$SMOKE_DIR/state.json"
   python3 -c 'import json, sys, datetime as dt; got = json.load(open(sys.argv[1]))["last_sent_date"]; want = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat(); print("last_sent_date", got, "expected", want); sys.exit(0 if got == want else 1)' "$SMOKE_DIR/state.json"
   echo "smoke OK"
   )
   ```

4. 安装 cron。只从一份备份生成候选文件。安装前再比对一次线上表：备份之后若有人改过 crontab，就中止，不覆盖。安装后核对整张表与候选文件完全一致。任何一步校验不过就停下。不要把过滤后的输出直接管道进 `crontab`：

   ```bash
   (
   set -euo pipefail
   mkdir -p /root/workspace/Finance/backups
   BACKUP="/root/workspace/Finance/backups/crontab-before-bitcow-$(date +%Y%m%d%H%M%S).txt"
   CAND="$(mktemp /tmp/crontab.bitcow.XXXXXX)"
   trap 'rm -f -- "$CAND"' EXIT
   crontab -l > "$BACKUP"                                   # 失败即停
   test -s "$BACKUP"                                        # 备份必须非空
   if grep -q finance_bitcow "$BACKUP"; then echo "already installed" >&2; exit 1; fi
   cp "$BACKUP" "$CAND"
   printf '%s\n' '03 8 * * * FINANCE_CRON_LOCK_BUSY_RC=75 /root/workspace/Finance/scripts/cron_wrapper.sh finance_bitcow cron_bitcow.log /root/workspace/Finance/scripts/run_bitcow_monitor.sh' >> "$CAND"
   diff -u "$BACKUP" "$CAND" || true                        # 人工看一眼：只有 1 行 +
   ADDED="$(diff "$BACKUP" "$CAND" | grep -c '^>' || true)"
   REMOVED="$(diff "$BACKUP" "$CAND" | grep -c '^<' || true)"
   test "$ADDED" -eq 1                                      # 恰好 1 行新增
   test "$REMOVED" -eq 0                                    # 0 行删除
   test "$(wc -l < "$CAND")" -eq "$(( $(wc -l < "$BACKUP") + 1 ))"   # 行数恰好 +1
   diff -u "$BACKUP" <(crontab -l)                          # 备份后线上表被改过 → 非零退出，不覆盖
   crontab "$CAND"
   diff -u "$CAND" <(crontab -l)                            # 安装后整张表必须与候选文件一致
   test "$(crontab -l | grep -c finance_bitcow)" -eq 1
   echo "installed; backup=$BACKUP"
   )
   ```

5. 部署后在 `ARCHITECTURE.md` 的「云端 cron（北京时间）」表加一行 08:03 Daily BitCow（cron 时间表 SSOT；`scripts/cron_jobs.md` 已过时，不再更新）。

## 日常检查

```bash
tail -30 /root/workspace/Finance/logs/cron_bitcow.log
cat /root/workspace/Finance/data/bitcow_monitor/state.json
```

日志关键字：`sent`（已发）、`already_sent`（同日防重跳过）、`binance_unavailable`（数据失败，exit 1）、`dvol_unavailable`（DVOL 降级）、`send_failed`（Telegram 失败，exit 1）。

## 回滚

1. 备份当前 crontab，导出到文件，删掉 `finance_bitcow` 那一行，从文件安装，再确认 `crontab -l | grep -c finance_bitcow` 为 0。
2. 代码可保留不用；需要时通过 git revert 相关提交。
3. 保留 `data/bitcow_monitor/state.json` 与日志，不删除。无服务需要重启。
