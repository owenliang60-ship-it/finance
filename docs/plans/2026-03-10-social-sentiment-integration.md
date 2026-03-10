# 社交情感数据接入设计

> **日期**: 2026-03-10
> **状态**: Boss 已审批
> **北极星位置**: 数据层（社交数据源接入）+ 分析层（情绪分析 — 日频）

---

## 背景

北极星四层金字塔中，数据层完成度 ~85%，缺口是社交数据源。分析层情绪分析完成度 ~10%，缺数据源 + 分析框架。本设计补齐这两块。

### 已锁定决策（2026-03-02）

- **Adanos API** 替代 xpoz，Hobby 版 $20/月，无限调用，90 天历史
- xpoz Reddit 数据不可用（短 ticker 噪音 90%+、日期过滤失效、评论索引空）
- 官方 X API ($200/月) 和 Reddit API (被拒) 均排除

### API 实测结果（2026-03-10）

| 源 | Endpoint | NVDA 7天实测 | 数据质量 |
|---|---|---|---|
| Reddit | `/reddit/stocks/v1/stock/{ticker}` | 750 mentions, buzz 76.7, 29 subreddits | 优秀，MU 无噪音 |
| X/Twitter | `/x/stocks/v1/stock/{ticker}` | 2503 mentions, buzz 84.2, 含 likes/views | 优秀，含影响力指标 |
| News | `/news/stocks/v1/stock/{ticker}` | 200 mentions, 25 sources | 不接入（本期） |
| Polymarket | `/polymarket/stocks/v1/stock/{ticker}` | 96 markets, $5.9M liquidity | 不接入（本期） |

- 认证方式: `X-API-Key` header
- Rate limit: Hobby = 无限/月, 1000/min
- 本地代理问题: 需 `NO_PROXY` 或 `--noproxy`（云端无此问题）

---

## 架构

```
数据层 (Data Desk)                    分析层 (情绪分析)              消费端
┌──────────────────┐                ┌──────────────────┐        ┌─────────────┐
│ adanos_client.py │                │ social_attention │        │ morning     │
│ market_store.py  │──daily_trend──>│ .py              │──信号──>│ _report.py  │
│                  │   raw data     │                  │        │ Section F   │
│ social_sentiment │                │ weighted_buzz()  │        └─────────────┘
│ 表 (market.db)   │                │ attention_zscore()│       ┌─────────────┐
└──────────────────┘                └──────────────────┘──信号──>│ pipeline.py │
                                                                │ deep分析注入 │
                                                                └─────────────┘
```

### 数据流

```
Cloud cron 06:55 (周二-六)
  └─> update_data.py --social-sentiment
        └─> adanos_client.get_stock_sentiment(symbol, source='reddit', days=7)
        └─> adanos_client.get_stock_sentiment(symbol, source='x', days=7)
              └─> 解析 daily_trend 展开为多行
              └─> market_store.upsert_social_sentiment(symbol, rows)
                    └─> INSERT OR REPLACE BY (symbol, date, source)

Cloud cron 07:00 (晨报)
  └─> morning_report.py
        └─> social_attention.weighted_buzz(symbol)
        └─> social_attention.attention_zscore(symbol)
              └─> 读 social_sentiment 表 20 日历史
              └─> combined_mentions z-score
        └─> 输出 Section F: 社交情绪雷达
```

---

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据源 | Reddit + X（不接 News/Polymarket） | 社交为核心，两源已覆盖散户+机构声音 |
| 存储粒度 | 每源每天一行 (PK: symbol+date+source) | 保留源维度，因子研究可独立评估每源预测力 |
| 更新频率 | 日频，云端 cron 06:55 | 消费端是日频分析，多采无消费端 |
| days 参数 | 每次 days=7，upsert 覆盖 | 零额外成本，自动补漏，cron 挂一两天不丢数据 |
| 原文片段 | JSON 字段存 top_mentions | 保留溯源能力，不需独立查询。~1MB/天 |
| 全字段存储 | 存 Adanos 返回的所有字段 | 不丢数据，未来扩展不需重采 |

---

## 数据库 Schema

`market.db` 新增 `social_sentiment` 表：

```sql
CREATE TABLE IF NOT EXISTS social_sentiment (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    source TEXT NOT NULL,         -- 'reddit' | 'x'

    -- 核心聚合指标
    buzz_score REAL,              -- 0-100
    total_mentions INTEGER,
    sentiment_score REAL,         -- -1.0 to 1.0
    positive_count INTEGER,
    negative_count INTEGER,
    neutral_count INTEGER,
    bullish_pct INTEGER,          -- 0-100
    bearish_pct INTEGER,          -- 0-100
    trend TEXT,                   -- 'rising' | 'falling' | 'stable'
    total_upvotes INTEGER,        -- Reddit: upvotes, X: likes

    -- 源特有字段
    unique_posts INTEGER,         -- Reddit: unique_posts, X: unique_tweets
    subreddit_count INTEGER,      -- Reddit only, X 为 NULL
    is_validated INTEGER,         -- X only (布尔)

    -- 原文片段 (JSON)
    top_mentions TEXT,            -- JSON array, 10 条原文+sentiment+votes
    top_subreddits TEXT,          -- Reddit: JSON array [{subreddit, count}], X: NULL

    -- 元数据
    period_days INTEGER,          -- API 请求的 days 参数
    created_at TEXT NOT NULL,     -- 采集时间 ISO

    PRIMARY KEY (symbol, date, source)
);
CREATE INDEX IF NOT EXISTS idx_social_date ON social_sentiment(date);
CREATE INDEX IF NOT EXISTS idx_social_symbol ON social_sentiment(symbol);
```

### 字段映射

| Adanos 字段 | DB 列 | Reddit | X |
|---|---|---|---|
| buzz_score | buzz_score | Y | Y |
| total_mentions | total_mentions | Y | Y |
| sentiment_score | sentiment_score | Y | Y |
| positive/negative/neutral_count | 同名 | Y | Y |
| bullish_pct / bearish_pct | 同名 | Y | Y |
| trend | trend | Y | Y |
| total_upvotes | total_upvotes | upvotes | likes (映射) |
| unique_posts / unique_tweets | unique_posts | unique_posts | unique_tweets (映射) |
| subreddit_count | subreddit_count | Y | NULL |
| is_validated | is_validated | NULL | Y |
| top_mentions / top_tweets | top_mentions | top_mentions | top_tweets (映射) |
| top_subreddits | top_subreddits | Y | NULL |

---

## 分析引擎

`src/indicators/social_attention.py`（归分析层，不是数据层）

### 信号 1：加权 Buzz Score

```python
weighted_buzz = (reddit_buzz * reddit_mentions + x_buzz * x_mentions)
                / (reddit_mentions + x_mentions)
```

- 按 mentions 数量加权，数据量大的源话语权大
- 任一源缺失则直接用另一源的值

### 信号 2：注意力异动 (Attention Z-Score)

```python
combined_mentions = reddit_mentions + x_mentions   # 当日两源合计
mean_20d = rolling_mean(combined_mentions, 20)      # 20 日均值
std_20d  = rolling_std(combined_mentions, 20)       # 20 日标准差
attention_zscore = (combined_mentions - mean_20d) / std_20d
```

- 窗口 20 交易日（~1 个月），和 RVOL 一致
- Z >= 2.0 → 注意力异动，Z >= 4.0 → 极端异动
- 至少 10 天历史数据才开始计算（冷启动期）

---

## 晨报集成

`scripts/morning_report.py` 新增 **Section F: 社交情绪雷达**

```
F. 社交情绪雷达
━━━━━━━━━━━━━━━━━━━━━━━━
🔥 注意力异动 (Z >= 2.0):
   NVDA  Z=3.2  buzz=81  sentiment=+0.18  (Reddit 750 + X 2503 mentions)
   MU    Z=2.4  buzz=66  sentiment=+0.07  (Reddit 232 + X 180 mentions)

📊 情绪极端 (bullish% >= 60 或 <= 20):
   NVDA  bullish 53%/33%  (X偏多/Reddit中性)

📉 情绪反转 (trend rising→falling 或反向):
   GOOGL  Reddit: falling  X: stable
```

三个子板块：
1. **异动捕捉** — 谁突然被大量讨论（attention_zscore >= 2.0）
2. **极端情绪** — 谁被一边倒看多/看空（bullish_pct >= 60 或 <= 20）
3. **反转信号** — 趋势刚从 rising 变 falling 或反向

---

## 文件清单

| 文件 | 层级 | 动作 | 说明 |
|------|------|------|------|
| `src/data/adanos_client.py` | 数据层 | 新建 | API 客户端（rate_limit + retry + NO_PROXY） |
| `src/data/market_store.py` | 数据层 | 修改 | 新增 social_sentiment 表 schema + upsert/query 方法 |
| `scripts/update_data.py` | 数据层 | 修改 | 新增 `--social-sentiment` flag |
| `src/indicators/social_attention.py` | 分析层 | 新建 | weighted_buzz() + attention_zscore() |
| `scripts/morning_report.py` | 消费端 | 修改 | Section F 社交情绪雷达 |
| `config/settings.py` | 配置 | 修改 | ADANOS_BASE_URL + ADANOS_CALL_INTERVAL |
| `tests/test_adanos_client.py` | 测试 | 新建 | 客户端单元测试 |
| `tests/test_social_attention.py` | 测试 | 新建 | 分析引擎单元测试 |
| `tests/test_social_sentiment_store.py` | 测试 | 新建 | 存储层单元测试 |

---

## 云端部署

| 项目 | 值 |
|------|-----|
| cron 时间 | 周二-六 06:55（量价 06:30 + DV 06:45 + IV 06:50 之后） |
| cron 命令 | `cd /root/workspace/Finance && source .env && .venv/bin/python scripts/update_data.py --social-sentiment >> logs/cron_social.log 2>&1` |
| 日志 | `logs/cron_social.log` |
| 预计耗时 | 145 ticker × 2 源 × 2s 间隔 ≈ 10 min |
| 代理 | 云端无代理问题，本地需 NO_PROXY 设置 |
| Python 兼容 | 3.10+（无 3.12 特性） |

---

## 验收标准

1. `python scripts/update_data.py --social-sentiment --symbols NVDA,AAPL,MU` 成功采集 3 ticker × 2 源，数据入库
2. `sqlite3 data/market.db "SELECT count(*) FROM social_sentiment"` 返回 > 0
3. 每条记录包含全部 Adanos 返回字段（buzz_score, mentions, sentiment, bullish/bearish_pct, trend, top_mentions JSON 等）
4. 重复运行 upsert 不产生重复行
5. `social_attention.attention_zscore('NVDA')` 在有 10+ 天数据后返回有效值
6. 晨报 Section F 正确输出异动/极端/反转三个子板块
7. 云端 cron 06:55 稳定运行，日志无报错
8. 所有新代码有单元测试，现有测试不 break

---

## 未来扩展（不在本期 scope）

- News + Polymarket 两源接入（schema 已兼容，加 source 值即可）
- 因子研究验证 attention_zscore 预测力
- OPRMS 三维化注入情绪维度
- Deep Pipeline prompt 注入社交信号
- 历史回填（Hobby 版支持 90 天历史）
