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

# 过滤策略：使用排除法 (EXCLUDED_SECTORS + EXCLUDED_INDUSTRIES + PERMANENTLY_EXCLUDED)
# 不维护 ALLOWED 白名单，只维护黑名单——新行业默认进入，需要排除时手动加入

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
    # 用户手动排除的医疗器械/军工
    "SYK", "NOC",
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

# 初始主题关键词（手动维护，~25 主题 120+ 关键词）
THEME_KEYWORDS_SEED = {
    # ===== AI 核心 =====
    "ai_chip": {
        "keywords": [
            "AI chip", "GPU shortage", "AI accelerator", "AI semiconductor",
            "NVIDIA GPU", "AI training chip", "inference chip",
        ],
        "tickers": ["NVDA", "AMD", "AVGO", "MRVL"],
    },
    "ai_software": {
        "keywords": [
            "generative AI", "large language model", "ChatGPT",
            "AI copilot", "AI assistant", "enterprise AI",
        ],
        "tickers": ["MSFT", "GOOG", "META", "ORCL", "PLTR"],
    },
    "ai_agent": {
        "keywords": [
            "AI agent", "autonomous AI", "agentic AI",
            "AI workflow automation", "AI coding",
        ],
        "tickers": ["MSFT", "GOOG", "AMZN", "PLTR", "CRM"],
    },
    "ai_infra": {
        "keywords": [
            "AI data center", "AI infrastructure", "hyperscaler capex",
            "GPU cluster", "AI server", "AI power consumption",
        ],
        "tickers": ["NVDA", "AMD", "AVGO", "MRVL", "DELL", "AMZN", "MSFT", "GOOG"],
    },
    # ===== 半导体 =====
    "memory": {
        "keywords": [
            "DRAM price", "HBM memory", "memory shortage", "NAND flash",
            "HBM3E", "DRAM demand", "memory cycle",
        ],
        "tickers": ["MU", "WDC"],
    },
    "semicap": {
        "keywords": [
            "semiconductor equipment", "chip manufacturing",
            "EUV lithography", "foundry expansion", "wafer fab",
        ],
        "tickers": ["ASML", "AMAT", "LRCX", "KLAC", "TSM"],
    },
    "chip_design": {
        "keywords": [
            "ARM architecture", "RISC-V", "custom silicon",
            "edge AI chip", "mobile processor",
        ],
        "tickers": ["ARM", "QCOM", "AVGO", "MRVL"],
    },
    # ===== 数据中心 & 基建 =====
    "liquid_cooling": {
        "keywords": [
            "liquid cooling", "data center cooling", "immersion cooling",
            "direct-to-chip cooling", "thermal management",
        ],
        "tickers": ["NVDA", "DELL", "AMZN", "MSFT", "GOOG"],
    },
    "cloud": {
        "keywords": [
            "cloud computing", "cloud migration", "multi-cloud",
            "AWS revenue", "Azure growth", "Google Cloud",
        ],
        "tickers": ["AMZN", "MSFT", "GOOG", "ORCL", "SNOW"],
    },
    "nuclear_power": {
        "keywords": [
            "small modular reactor", "nuclear data center",
            "nuclear energy AI", "SMR nuclear",
        ],
        "tickers": ["AMZN", "MSFT", "GOOG"],
    },
    # ===== 网络安全 =====
    "cybersecurity": {
        "keywords": [
            "cybersecurity", "zero trust", "ransomware",
            "cloud security", "SASE", "XDR security",
            "cybersecurity spending", "data breach",
        ],
        "tickers": ["CRWD", "PANW", "ZS", "FTNT"],
    },
    # ===== 自动驾驶 & 机器人 =====
    "autonomous_driving": {
        "keywords": [
            "self driving car", "autonomous vehicle", "robotaxi",
            "Tesla FSD", "Waymo", "lidar technology",
        ],
        "tickers": ["TSLA", "GOOG", "UBER"],
    },
    "humanoid_robot": {
        "keywords": [
            "humanoid robot", "Tesla Optimus", "Figure AI",
            "robot automation", "industrial robot",
        ],
        "tickers": ["TSLA", "NVDA"],
    },
    # ===== 商业航天 =====
    "space": {
        "keywords": [
            "commercial space", "SpaceX", "Starlink",
            "satellite internet", "space economy",
            "rocket launch", "space defense",
        ],
        "tickers": ["LMT", "RTX", "NOC", "BA"],
    },
    # ===== 量子计算 =====
    "quantum": {
        "keywords": [
            "quantum computing", "quantum chip", "quantum supremacy",
            "quantum error correction", "quantum advantage",
        ],
        "tickers": ["GOOG", "IBM", "IONQ"],
    },
    # ===== 消费科技 =====
    "ar_vr": {
        "keywords": [
            "augmented reality", "virtual reality", "Apple Vision Pro",
            "Meta Quest", "spatial computing", "mixed reality",
        ],
        "tickers": ["AAPL", "META"],
    },
    "streaming": {
        "keywords": [
            "streaming wars", "Netflix subscriber", "streaming revenue",
            "ad-supported streaming", "content spending",
        ],
        "tickers": ["NFLX", "DIS", "AMZN"],
    },
    "digital_ads": {
        "keywords": [
            "digital advertising", "social media ads", "programmatic ads",
            "ad revenue growth", "connected TV ads",
        ],
        "tickers": ["META", "GOOG", "TTD", "APP"],
    },
    # ===== 电动车 & 能源 =====
    "ev_battery": {
        "keywords": [
            "electric vehicle sales", "EV battery", "EV charging",
            "Tesla delivery", "EV market share",
        ],
        "tickers": ["TSLA"],
    },
    # ===== 金融科技 & 加密 =====
    "fintech": {
        "keywords": [
            "digital payments", "fintech growth", "buy now pay later",
            "payment processing", "embedded finance",
        ],
        "tickers": ["V", "MA", "PYPL", "SQ"],
    },
    "crypto": {
        "keywords": [
            "Bitcoin price", "Ethereum", "crypto regulation",
            "Bitcoin ETF", "crypto exchange",
        ],
        "tickers": ["COIN"],
    },
    # ===== 医疗 =====
    "glp1": {
        "keywords": [
            "GLP-1", "Ozempic", "weight loss drug",
            "Wegovy", "Mounjaro", "obesity drug",
        ],
        "tickers": ["LLY", "NVO"],
    },
    "biotech": {
        "keywords": [
            "gene therapy", "CRISPR", "mRNA vaccine",
            "biotech breakthrough", "FDA approval",
        ],
        "tickers": ["ABBV", "AMGN", "GILD", "REGN"],
    },
    # ===== 国防 =====
    "defense": {
        "keywords": [
            "defense spending", "military AI", "drone warfare",
            "defense budget", "defense contract",
        ],
        "tickers": ["LMT", "RTX", "NOC", "GD"],
    },
    # ===== 企业软件 =====
    "enterprise_sw": {
        "keywords": [
            "SaaS growth", "enterprise software", "software spending",
            "database market", "data analytics",
        ],
        "tickers": ["ORCL", "SNOW", "PLTR", "NOW"],
    },
    # ===== 中美科技 =====
    "china_tech": {
        "keywords": [
            "chip export ban", "China AI", "US China tech war",
            "semiconductor sanctions", "DeepSeek",
        ],
        "tickers": ["NVDA", "ASML", "AMAT", "LRCX"],
    },
}

# 评分权重
ATTENTION_WEIGHTS = {
    "reddit": 0.35,
    "news": 0.35,
    "trends": 0.30,
}

# ============ Momentum Engine (Engine A) ============

# 聚类数据目录
CLUSTERING_DIR = DATA_DIR / "clustering"

# 晨报输出目录
SCANS_DIR = DATA_DIR / "scans"

# RS Rating 配置
RS_RATING_TOP_N = 10      # 晨报显示 Top N
RS_RATING_BOTTOM_N = 5    # 晨报显示 Bottom N

# DV 加速阈值
DV_ACCELERATION_THRESHOLD = 1.5  # 5d/20d ratio 阈值

# RVOL 持续放量阈值
RVOL_SUSTAINED_THRESHOLD = 2.0   # σ 阈值

# ============ Theme Engine P2 ============

THEME_TOP_N = 20                           # Engine B 注意力榜取 Top N
THEME_MAX_NEW_TICKERS = 10                 # 每次池扩展最多新增几只
THEME_RS_THRESHOLD = 80                    # RS 动量信号阈值 (百分位)
POOL_SOURCE_ATTENTION = "attention"        # 注意力引擎来源标记

# ============ MarketData.app (Options) ============

MARKETDATA_API_KEY = os.environ.get("MARKETDATA_API_KEY", "")
MARKETDATA_BASE_URL = "https://api.marketdata.app/v1"
MARKETDATA_CALL_INTERVAL = 2  # 秒，防限流

# Options 配置
OPTIONS_IV_LOOKBACK_DAYS = 252    # IV rank/percentile 回看天数
OPTIONS_CHAIN_DTE_MIN = 7        # Chain 最小 DTE
OPTIONS_CHAIN_DTE_MAX = 120      # Chain 最大 DTE
OPTIONS_SNAPSHOT_RETAIN_DAYS = 7  # Chain 快照保留天数
OPTIONS_LIQUIDITY_MIN_OI = 200   # 最低 OI 标准
OPTIONS_LIQUIDITY_MIN_VOLUME = 100  # 最低日成交量
OPTIONS_LIQUIDITY_MAX_SPREAD_PCT = 0.10  # 最大 bid-ask spread (占 mid %)

# Telegram 配置 (从环境变量读取)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
