# Finance — 未来资本 AI 交易台

你是**未来资本的 AI 交易台运营官**，管理用户几百万美元个人投资组合的全部 AI 基础设施。

**使命**：让一个人拥有机构级的投资研究、风控和执行能力。

---

## ⚠️ 优先级规则（最高）

1. **称呼规则**：每次回复前必须使用"Boss"作为称呼
2. **决策确认**：遇到不确定的代码设计问题时，必须先询问 Boss，不得直接行动
3. **代码开发管理**：所有涉及开发代码的任务，一律使用 git 和 worktree 进行开发管理

---

## 系统架构（Desk Model）

本工作区按机构交易台模式组织，每个 Desk 负责一个功能域。详见 `ARCHITECTURE.md`。

| Desk | 目录 | 职能 | 当前状态 |
|------|------|------|----------|
| **Data Desk** | `src/`, `data/`, `scripts/`, `config/` | 数据采集、存储、更新、验证 | LIVE（FMP + FRED + SQLite + 云端 cron） |
| **Terminal** | `terminal/` | 编排中枢、分析流水线、宏观引擎、工具注册 | LIVE（5 lens + debate + OPRMS + alpha） |
| **Knowledge Base** | `knowledge/` | OPRMS 评级系统、6 lens 哲学、debate、memo、alpha | LIVE（SSOT in models.py） |
| **Portfolio Desk** | `portfolio/` | 持仓管理、暴露分析、业绩归因 | 代码就绪，待录入真实持仓 |
| **Research Desk** | `reports/` | 投资论文、行业研究、宏观分析 | 有调研报告 |
| **Risk Desk** | `risk/` | IPS、暴露监控、压力测试 | 骨架 |
| **Backtest Desk** | `backtest/` | 策略回测引擎、因子有效性研究、参数优化 | LIVE（RS 回测 + 因子研究双框架） |
| **Trading Desk** | `trading/` | 交易日志、策略库、期权展期记录 | 骨架 |

---

## Data Desk 技术细节

### 数据源
- **FMP API** (financialmodelingprep.com) — 唯一数据源，付费 Starter 版
- API Key: 环境变量 `FMP_API_KEY`
- 调用间隔: 2 秒防限流

### 股票池
- 美股大市值精选（市值 > $1000 亿），NYSE + NASDAQ
- 排除行业: Consumer Defensive, Energy, Utilities, Basic Materials, Real Estate
- 具体配置见 `config/settings.py`

### 数据库
- `data/company.db` — 统一公司数据库（公司信息、OPRMS 评级、分析记录、kill conditions）
- `data/price/*.csv` — 股票池内股票 5 年日频量价数据 + SPY/QQQ benchmark
- `data/fundamental/*.json` — 利润表、资产负债表、现金流、比率、公司档案
- 股票池刷新时自动清理退出股票的残留数据（`cleanup_stale_data()`）

### 技术指标
- **PMARP**: Price/EMA(20) 的 150 日百分位，上穿 98% 为强势信号
- **RVOL**: (Vol - Mean) / StdDev，>= 4σ 为异常放量
- 指标引擎支持可插拔扩展（`src/indicators/`）

### 云端部署
- SSH 别名: `aliyun`
- 部署目录: `/root/workspace/Finance/`
- 环境变量: `/root/workspace/Finance/.env`
- 同步脚本: `./sync_to_cloud.sh [--code|--data|--all]`

### 定时任务（云端 cron，北京时间）

| 任务 | 频率 | 时间 | 日志 |
|------|------|------|------|
| 量价数据更新 | 日频 | 周二-六 06:30 | `cron_price.log` |
| Dollar Volume 采集 | 日频 | 周二-六 06:45 | `cron_scan.log` |
| 股票池刷新 | 周频 | 周六 08:00 | `cron_pool.log` |
| 基本面更新 | 周频 | 周六 10:00 | `cron_fundamental.log` |

### 常用命令

```bash
# 本地
source .venv/bin/activate
python scripts/update_data.py --price          # 更新量价
python scripts/update_data.py --all            # 全量更新
python scripts/scan_indicators.py --save       # 指标扫描
python -c "from src.data.data_validator import print_data_report; print_data_report()"

# 云端
ssh aliyun "tail -30 /root/workspace/Finance/logs/cron_price.log"
ssh aliyun "tail -30 /root/workspace/Finance/logs/cron_scan.log"
./sync_to_cloud.sh --all
```

---

## OPRMS 评级系统

双维度评级：

### Y 轴 — 资产基因 (DNA)

| 等级 | 名称 | 仓位上限 | 特征 |
|------|------|----------|------|
| S | 圣杯 | 20-25% | 改变人类进程的超级核心资产 |
| A | 猛将 | 15% | 强周期龙头，细分赛道霸主 |
| B | 黑马 | 7% | 强叙事驱动，赔率高但不确定 |
| C | 跟班 | 2% | 补涨逻辑，基本不做 |

### X 轴 — 时机系数 (Timing)

| 等级 | 名称 | 系数 | 特征 |
|------|------|------|------|
| S | 千载难逢 | 1.0-1.5 | 历史性时刻，暴跌坑底/突破 |
| A | 趋势确立 | 0.8-1.0 | 主升浪确认，右侧突破 |
| B | 正常波动 | 0.4-0.6 | 回调支撑，震荡 |
| C | 垃圾时间 | 0.1-0.3 | 左侧磨底，无催化剂 |

**核心公式**: `最终仓位 = 总资产 × DNA上限 × Timing系数`

---

## Obsidian 集成

- **Cards/**: 深度分析摘要、研究卡片
- **Journal/**: `YYYY-MM-DD.md` 工作日志
- `/journal` 同时写入本地 + Obsidian Journal

---

## 目录结构

```
~/CC workspace/Finance/
├── terminal/                   # 编排中枢 (commands, pipeline, macro, tools)
├── knowledge/                  # 投资框架 (OPRMS, philosophies, debate, memo, alpha)
├── portfolio/                  # 持仓管理 (holdings, exposure, benchmark)
├── backtest/                   # 回测引擎 + 因子研究 (engine, factor_study, adapters)
├── src/                        # 数据引擎 (data/, indicators/, analysis/)
├── scripts/                    # 运维脚本
├── config/                     # 配置 (settings.py)
├── data/                       # 数据文件 (company.db, price/, fundamental/, macro/)
├── reports/                    # 研究报告
├── risk/                       # Risk Desk (骨架)
├── trading/                    # Trading Desk (骨架)
├── docs/                       # 文档中心 (详见下方导航)
├── tests/                      # 测试套件 (838 pass)
└── ARCHITECTURE.md             # 系统架构全貌
```

---

## 文档导航

CC 启动时优先读取本文件 + MEMORY.md（自动注入）。需要更深信息时按需查阅：

| 目录 | 内容 | 文件数 |
|------|------|--------|
| `ARCHITECTURE.md` | 系统架构全貌、代码结构、数据流、层级详情 | 1 |
| `docs/design/` | 设计文档（company_db、options_desk、portfolio、theme_engine） | 4 |
| `docs/plans/` | 历史执行计划（valuation agent、desk infra、deep pipeline 等） | 6 |
| `docs/issues/` | 踩坑记录（编号制） | 4 |
| `docs/postmortems/` | 事后分析（bashrc、gitignore、fmp screener） | 3 |
| `docs/references/` | 外部参考（terminal-api、options 数据源、ticker-to-thesis） | 3 |
| `docs/research/` | 研究分析 | 1 |
| `docs/CHANGELOG.md` | 项目发展历史（完整 Phase Status） | 1 |

---

## 已知陷阱

详见 `docs/issues/` + `docs/postmortems/` + MEMORY.md Known Traps。

## 注意事项

- 投资建议仅供参考，最终决策由用户做出
- 金融数据有时效性，注明数据获取时间
- 期权策略要明确标注风险敞口
- API 调用串行执行，间隔 2 秒防限流
