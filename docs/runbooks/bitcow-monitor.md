# BTC 奶牛（BitCow）日线信号监控

Status: 代码已在 `feat/bitcow-monitor` 分支完成并通过离线测试；尚未部署、尚未装 cron。

每天 UTC 日线收盘后跑一次，读 Binance BTCUSDT 1d 与 Deribit BTC DVOL，向 Telegram 私聊发一条面板。
有新信号时首行以 `🚨` 开头；没有新信号时只发状态面板。

## 信号定义

| 层 | 规则 | 报警（🚨）条件 |
|----|------|----------------|
| 慢层 | UTC 周日日线收盘 vs 50 周 SMA | 当天是周日且方向翻转 |
| 中层 | EMA84 ± Wilder ATR14 通道，严格突破翻向 | 翻向（= 触发 ATR 止损） |
| 中层预警 | 收盘跌破/站上 EMA84 中线（mid）；距止损沿不足 0.5 ATR（near） | 新进或从 mid 升级到 near；持续不重复报警 |
| 偏离 D | `D = (close - EMA84) / ATR14`，档位 ±4.5 / ±6.5 / ±7.0 | 新进、升档或符号翻转；同档持续不重复报警 |
| DVOL | 最近 365 个已收盘日的分位 | 只展示，不报警；不可用时写明原因 |

代码：`scripts/bitcow_monitor.py`；入口：`scripts/run_bitcow_monitor.sh`；测试：`tests/test_bitcow_monitor.py`（离线 fixture `tests/fixtures/bitcow/btcusdt_1d.csv`）。

## 运行语义

- 数据日期：`as_of` 固定为 UTC 昨日。Binance 末根不是 UTC 昨日、有缺口/重复、少于 900 根、OHLC 非法时 exit 1，不发面板、不写 state；cron_wrapper 发失败告警。
- DVOL 失败只降级：面板写 `DVOL：⚠️ 不可用（接口异常/数据过期/样本不足）`，其余照发。
- 防重：`data/bitcow_monitor/state.json` 记 `last_sent_date`。同日重跑直接 exit 0，不发网络请求。`--force` 忽略防重；`--dry-run` 只打印，不发不写。
- Telegram 发送返回失败时 exit 1，不写 state，下次运行会重发。
- state 只接受 `.json` 路径；损坏的 state 视为空并照发。

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

3. 冒烟（真实发一条私聊；用临时 state，不占用生产当日防重）：

   ```bash
   scripts/run_bitcow_monitor.sh --label SMOKE --state-path /tmp/bitcow-smoke/state.json
   cat /tmp/bitcow-smoke/state.json
   ```

4. 备份 crontab，编辑导出文件，再从文件安装并核对。不要把过滤后的输出直接管道进 `crontab`：

   ```bash
   mkdir -p /root/workspace/Finance/backups
   crontab -l > /root/workspace/Finance/backups/crontab-before-bitcow-$(date +%Y%m%d%H%M%S).txt
   crontab -l > /tmp/crontab.new
   # 在 /tmp/crontab.new 末尾追加下面这一行（只加一次）
   crontab /tmp/crontab.new
   crontab -l | grep -c finance_bitcow   # 期望 1
   ```

   ```cron
   03 8 * * * FINANCE_CRON_LOCK_BUSY_RC=75 /root/workspace/Finance/scripts/cron_wrapper.sh finance_bitcow cron_bitcow.log /root/workspace/Finance/scripts/run_bitcow_monitor.sh
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
