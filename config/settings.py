"""
Finance 工作区配置 (Data Desk)
"""
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

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
EXCHANGES = ["NYSE", "NASDAQ"]
COUNTRY = "US"

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
