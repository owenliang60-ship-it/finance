"""
Finance 工作区配置 (Data Desk)
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 自动加载 .env（API keys 等敏感配置）
load_dotenv(PROJECT_ROOT / ".env")

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
POOL_DIR = DATA_DIR / "pool"
PRICE_DIR = DATA_DIR / "price"
FUNDAMENTAL_DIR = DATA_DIR / "fundamental"
RATINGS_DIR = DATA_DIR / "ratings"
MACRO_DIR = DATA_DIR / "macro"

# FMP API 配置 (从环境变量读取)
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# 股票池配置
MARKET_CAP_THRESHOLD = 100_000_000_000  # 1000亿美元
EXCHANGES = ["NYSE", "NASDAQ"]  # 交易所过滤已足够，不再按注册国过滤

# 行业过滤规则 (只保留这些行业)
ALLOWED_SECTORS = [
    "Technology",
    "Financial Services",
    "Healthcare",
    "Consumer Cyclical",
    "Communication Services",  # 仅 Entertainment
]

# 行业内细分过滤
ALLOWED_INDUSTRIES = {
    "Industrials": ["Aerospace & Defense"],  # 只保留航空航天/国防
    "Communication Services": ["Entertainment"],  # 只保留娱乐
}

# 永久排除的股票 (不论市值和行业，永远不加入股票池)
PERMANENTLY_EXCLUDED = {
    # 债券/优先股 (非普通股)
    "GEGGL", "BNJ", "BNH", "TBB",
    # 用户手动排除的应用软件
    "CRM", "INTU", "NOW",
    # 用户手动排除的机械类
    "CAT", "DE", "HON", "PH",
    # 用户手动排除的其他工业
    "UNP", "ADP",
}

# 永久排除的行业 (这些行业的股票永远不加入)
EXCLUDED_SECTORS = [
    "Consumer Defensive",   # 必需消费
    "Energy",               # 能源
    "Utilities",            # 公用事业
    "Basic Materials",      # 基础材料
    "Real Estate",          # 房地产
]

# 永久排除的细分行业
EXCLUDED_INDUSTRIES = [
    "Telecommunications Services",  # 电信
    "Agricultural - Machinery",     # 农业机械
    "Conglomerates",               # 多元工业
    "Railroads",                   # 铁路
    "Industrial - Machinery",      # 工业机械
    "Staffing & Employment Services",  # 人力资源
]

# API 调用配置 (防限流)
API_CALL_INTERVAL = 2  # 秒，每次 API 调用间隔
API_RETRY_TIMES = 3
API_TIMEOUT = 30

# 数据保留配置
PRICE_HISTORY_YEARS = 5  # 保留5年量价数据

# Dollar Volume 配置
DOLLAR_VOLUME_DB = DATA_DIR / "dollar_volume.db"
DOLLAR_VOLUME_TOP_N = 200       # 存储 Top 200
DOLLAR_VOLUME_REPORT_N = 50     # 推送 Top 50
DOLLAR_VOLUME_LOOKBACK = 30     # 新面孔回看天数

# Benchmark symbols (always included in price updates)
BENCHMARK_SYMBOLS = ["SPY", "QQQ"]

# ============ Attention Engine (Engine B) ============

# Finnhub API (free tier: 60 req/min)
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

# Paths
ATTENTION_DIR = DATA_DIR / "attention"
ATTENTION_DB_PATH = ATTENTION_DIR / "attention.db"
ATTENTION_REPORT_DIR = ATTENTION_DIR

# Google Trends
GT_ANCHOR_KEYWORD = "stock market"
GT_SLEEP_SECONDS = 60  # 安全间隔（Google 限流严格）
GT_DEFAULT_TIMEFRAME = "today 3-m"

# Reddit (PRAW read-only OAuth)
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = "attention-engine/1.0 by future-capital"
REDDIT_SUBREDDITS = ["stocks", "investing", "wallstreetbets", "options"]
REDDIT_POSTS_PER_SUB = 200
REDDIT_TICKER_BLACKLIST = {
    "I", "A", "AM", "AT", "IT", "IS", "ON", "OR", "AN", "AS",
    "BE", "BY", "DO", "GO", "IF", "IN", "ME", "MY", "NO", "OF",
    "OK", "SO", "TO", "UP", "US", "WE", "AI", "ALL", "CEO", "GDP",
    "IMO", "IPO", "LOL", "OMG", "SEC", "USD", "WSB", "YOY", "DD",
    "EPS", "ETF", "FED", "ATH", "OTC", "PE", "PS", "IV", "DTE",
    "OP", "TD", "PM", "UK", "EU", "JP", "CN", "HK", "RIP", "FYI",
    "TL", "DR", "TA", "FA", "IMF", "GDP", "CPI", "PPI", "NFP",
}

# 初始主题关键词（手动维护）
THEME_KEYWORDS_SEED = {
    "memory": {
        "keywords": ["DRAM price", "HBM memory", "memory shortage", "NAND flash"],
        "tickers": ["MU", "WDC", "SNDK"],
    },
    "ai_chip": {
        "keywords": ["AI chip", "GPU shortage", "AI accelerator"],
        "tickers": ["NVDA", "AMD", "AVGO", "MRVL"],
    },
    "quantum": {
        "keywords": ["quantum computing", "quantum chip"],
        "tickers": ["GOOG", "IBM", "IONQ"],
    },
    "cybersecurity": {
        "keywords": ["cybersecurity", "zero trust", "ransomware"],
        "tickers": ["CRWD", "PANW", "ZS", "FTNT"],
    },
}

# 评分权重
ATTENTION_WEIGHTS = {
    "reddit": 0.35,
    "news": 0.35,
    "trends": 0.30,
}
