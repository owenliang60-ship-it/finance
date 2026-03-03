"""
全链数据健康检查 — 新鲜度 + 完整性 + 一致性

整合 data_validator.py 的能力，新增池完整性、覆盖率、一致性等检查维度。
输出统一的 PASS/WARN/FAIL 报告。

用法:
    python -m src.data.data_health          # 直接运行
    python scripts/update_data.py --check   # 通过 update_data.py
"""
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import DATA_DIR, POOL_DIR, PRICE_DIR, FUNDAMENTAL_DIR, MARKET_DB_PATH

logger = logging.getLogger(__name__)

UNIVERSE_FILE = POOL_DIR / "universe.json"
COMPANY_DB = DATA_DIR / "company.db"
MARKET_DB = MARKET_DB_PATH

# market.db 的 8 个数据表 (来自 market_store._SCHEMA)
MARKET_DB_EXPECTED_TABLES = {
    "daily_price",
    "income_quarterly",
    "balance_sheet_quarterly",
    "cash_flow_quarterly",
    "metrics_quarterly",
    "ratios_annual",
    "iv_daily",
    "options_snapshots",
}


@dataclass
class CheckResult:
    name: str
    status: str  # "PASS", "WARN", "FAIL"
    detail: str

    def __str__(self):
        icon = {"PASS": "OK", "WARN": "!!", "FAIL": "XX"}[self.status]
        return f"[{icon}] {self.name}: {self.detail}"


@dataclass
class HealthReport:
    level: str = "PASS"  # overall: "PASS", "WARN", "FAIL"
    checks: List[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult):
        self.checks.append(result)
        # Escalate overall level
        if result.status == "FAIL":
            self.level = "FAIL"
        elif result.status == "WARN" and self.level != "FAIL":
            self.level = "WARN"

    def summary(self) -> str:
        icon = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}[self.level]
        lines = [f"=== 数据健康检查: {icon} ==="]
        for c in self.checks:
            lines.append(f"  {c}")
        return "\n".join(lines)


def _load_universe_symbols() -> List[str]:
    """加载股票池 symbols，不依赖 pool_manager 避免循环导入。"""
    if not UNIVERSE_FILE.exists():
        return []
    try:
        with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [s.get("symbol") for s in data if s.get("symbol")]
    except (json.JSONDecodeError, IOError):
        return []


def _business_days_ago(n: int) -> datetime:
    """返回 n 个交易日前的日期 (简易版: 跳过周末)。"""
    dt = datetime.now()
    count = 0
    while count < n:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:  # Mon-Fri
            count += 1
    return dt


def _check_pool_integrity() -> CheckResult:
    """检查池完整性: universe.json 中股票数量。"""
    symbols = _load_universe_symbols()
    count = len(symbols)

    if count == 0:
        return CheckResult("池完整性", "FAIL", "股票池为空")
    elif count < 70:
        return CheckResult("池完整性", "FAIL", f"股票池仅 {count} 只 (<70)")
    elif count > 200:
        return CheckResult("池完整性", "FAIL", f"股票池 {count} 只 (>200, 异常)")
    elif count < 90 or count > 150:
        return CheckResult("池完整性", "WARN", f"股票池 {count} 只 (偏离正常范围 90-150)")
    else:
        return CheckResult("池完整性", "PASS", f"股票池 {count} 只")


def _check_price_coverage(symbols: List[str]) -> CheckResult:
    """检查价格覆盖率: CSV 数量 vs 池数量。"""
    if not symbols:
        return CheckResult("价格覆盖率", "FAIL", "无股票池数据")

    if not PRICE_DIR.exists():
        return CheckResult("价格覆盖率", "FAIL", "价格目录不存在")

    csv_symbols = {f.stem for f in PRICE_DIR.glob("*.csv")}
    covered = sum(1 for s in symbols if s in csv_symbols)
    ratio = covered / len(symbols)

    if ratio >= 0.95:
        return CheckResult("价格覆盖率", "PASS", f"{covered}/{len(symbols)} ({ratio:.0%})")
    elif ratio >= 0.80:
        missing = [s for s in symbols if s not in csv_symbols][:10]
        return CheckResult("价格覆盖率", "WARN",
                           f"{covered}/{len(symbols)} ({ratio:.0%}), 缺失: {missing}")
    else:
        return CheckResult("价格覆盖率", "FAIL",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")


def _check_fundamental_coverage(symbols: List[str]) -> CheckResult:
    """检查基本面覆盖率: JSON 条目数 vs 池数量。"""
    if not symbols:
        return CheckResult("基本面覆盖率", "FAIL", "无股票池数据")

    if not FUNDAMENTAL_DIR.exists():
        return CheckResult("基本面覆盖率", "FAIL", "基本面目录不存在")

    # 以 profiles.json 为准
    profiles_file = FUNDAMENTAL_DIR / "profiles.json"
    if not profiles_file.exists():
        return CheckResult("基本面覆盖率", "FAIL", "profiles.json 不存在")

    try:
        with open(profiles_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile_symbols = {k for k in data if k != "_meta"}
    except (json.JSONDecodeError, IOError):
        return CheckResult("基本面覆盖率", "FAIL", "profiles.json 读取失败")

    covered = sum(1 for s in symbols if s in profile_symbols)
    ratio = covered / len(symbols)

    if ratio >= 0.95:
        return CheckResult("基本面覆盖率", "PASS", f"{covered}/{len(symbols)} ({ratio:.0%})")
    elif ratio >= 0.80:
        return CheckResult("基本面覆盖率", "WARN", f"{covered}/{len(symbols)} ({ratio:.0%})")
    else:
        return CheckResult("基本面覆盖率", "FAIL", f"{covered}/{len(symbols)} ({ratio:.0%})")


def _check_price_freshness() -> CheckResult:
    """检查价格新鲜度: 最新 CSV 的最后日期。"""
    if not PRICE_DIR.exists():
        return CheckResult("价格新鲜度", "FAIL", "价格目录不存在")

    csv_files = list(PRICE_DIR.glob("*.csv"))
    if not csv_files:
        return CheckResult("价格新鲜度", "FAIL", "无价格 CSV 文件")

    # 取 mtime 最新的 CSV，读最后一行的日期
    latest_csv = max(csv_files, key=lambda f: f.stat().st_mtime)
    try:
        # 读最后几行找日期
        with open(latest_csv, "r") as f:
            lines = f.readlines()
        if len(lines) < 2:
            return CheckResult("价格新鲜度", "FAIL", f"{latest_csv.stem}: CSV 无数据行")

        last_line = lines[-1].strip()
        date_str = last_line.split(",")[0]
        latest_date = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, IndexError):
        return CheckResult("价格新鲜度", "WARN", f"{latest_csv.stem}: 无法解析日期")

    # 计算交易日距离
    now = datetime.now()
    calendar_days = (now - latest_date).days
    # 粗略估计交易日 (减去周末)
    weeks = calendar_days // 7
    trading_days = calendar_days - (weeks * 2)

    if trading_days <= 3:
        return CheckResult("价格新鲜度", "PASS",
                           f"最新: {date_str} ({latest_csv.stem}), ~{trading_days} 交易日前")
    elif trading_days <= 7:
        return CheckResult("价格新鲜度", "WARN",
                           f"最新: {date_str} ({latest_csv.stem}), ~{trading_days} 交易日前")
    else:
        return CheckResult("价格新鲜度", "FAIL",
                           f"最新: {date_str} ({latest_csv.stem}), ~{trading_days} 交易日前")


def _check_fundamental_freshness() -> CheckResult:
    """检查基本面新鲜度: _meta.updated_at。"""
    profiles_file = FUNDAMENTAL_DIR / "profiles.json"
    if not profiles_file.exists():
        return CheckResult("基本面新鲜度", "FAIL", "profiles.json 不存在")

    try:
        with open(profiles_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        meta = data.get("_meta", {})
        updated_at = meta.get("updated_at")
    except (json.JSONDecodeError, IOError):
        return CheckResult("基本面新鲜度", "FAIL", "profiles.json 读取失败")

    if not updated_at:
        return CheckResult("基本面新鲜度", "WARN", "无 _meta.updated_at 时间戳")

    try:
        update_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
        age_days = (datetime.now() - update_time).days
    except ValueError:
        return CheckResult("基本面新鲜度", "WARN", f"无法解析时间: {updated_at}")

    if age_days <= 14:
        return CheckResult("基本面新鲜度", "PASS", f"更新于 {updated_at} ({age_days} 天前)")
    elif age_days <= 30:
        return CheckResult("基本面新鲜度", "WARN", f"更新于 {updated_at} ({age_days} 天前)")
    else:
        return CheckResult("基本面新鲜度", "FAIL", f"更新于 {updated_at} ({age_days} 天前)")


def _check_pool_freshness() -> CheckResult:
    """检查池新鲜度: universe.json 的 mtime。"""
    if not UNIVERSE_FILE.exists():
        return CheckResult("池新鲜度", "FAIL", "universe.json 不存在")

    mtime = datetime.fromtimestamp(UNIVERSE_FILE.stat().st_mtime)
    age_days = (datetime.now() - mtime).days

    if age_days <= 10:
        return CheckResult("池新鲜度", "PASS",
                           f"更新于 {mtime.strftime('%Y-%m-%d')} ({age_days} 天前)")
    elif age_days <= 20:
        return CheckResult("池新鲜度", "WARN",
                           f"更新于 {mtime.strftime('%Y-%m-%d')} ({age_days} 天前)")
    else:
        return CheckResult("池新鲜度", "FAIL",
                           f"更新于 {mtime.strftime('%Y-%m-%d')} ({age_days} 天前)")


def _check_company_db() -> CheckResult:
    """检查 company.db 存在 + 可读 + 表结构完整。"""
    if not COMPANY_DB.exists():
        return CheckResult("company.db", "FAIL", "数据库文件不存在")

    try:
        conn = sqlite3.connect(str(COMPANY_DB))
        cursor = conn.cursor()

        # 检查关键表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected = {"companies"}  # 至少有 companies 表
        missing = expected - tables
        if missing:
            return CheckResult("company.db", "FAIL", f"缺少表: {missing}")

        return CheckResult("company.db", "PASS", f"正常, {len(tables)} 个表")

    except sqlite3.DatabaseError as e:
        return CheckResult("company.db", "FAIL", f"数据库损坏: {e}")


def _check_price_pool_consistency(symbols: List[str]) -> CheckResult:
    """检查价格-池一致性: 池内每只股票都有对应 CSV。"""
    if not symbols:
        return CheckResult("价格-池一致性", "FAIL", "无股票池数据")

    if not PRICE_DIR.exists():
        return CheckResult("价格-池一致性", "FAIL", "价格目录不存在")

    csv_symbols = {f.stem for f in PRICE_DIR.glob("*.csv")}
    missing = [s for s in symbols if s not in csv_symbols]
    ratio = (len(symbols) - len(missing)) / len(symbols)

    if ratio >= 1.0:
        return CheckResult("价格-池一致性", "PASS", "100% 一致")
    elif ratio >= 0.90:
        return CheckResult("价格-池一致性", "WARN",
                           f"{ratio:.0%} 一致, 缺少: {missing[:10]}")
    else:
        return CheckResult("价格-池一致性", "FAIL",
                           f"{ratio:.0%} 一致, 缺少 {len(missing)} 只")


def _check_market_db() -> CheckResult:
    """检查 market.db 存在 + 8 个数据表齐全。"""
    if not MARKET_DB.exists():
        return CheckResult("market.db", "FAIL", "数据库文件不存在")

    try:
        conn = sqlite3.connect(str(MARKET_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        missing = MARKET_DB_EXPECTED_TABLES - tables
        if missing:
            return CheckResult("market.db", "FAIL",
                               f"缺少 {len(missing)} 个表: {sorted(missing)}")

        return CheckResult("market.db", "PASS",
                           f"正常, {len(tables)} 个表")

    except sqlite3.DatabaseError as e:
        return CheckResult("market.db", "FAIL", f"数据库损坏: {e}")


def _check_mdb_price_coverage(symbols: List[str]) -> CheckResult:
    """检查 market.db daily_price 覆盖率 vs 股票池。"""
    if not symbols:
        return CheckResult("mdb价格覆盖率", "FAIL", "无股票池数据")

    if not MARKET_DB.exists():
        return CheckResult("mdb价格覆盖率", "FAIL", "market.db 不存在")

    try:
        conn = sqlite3.connect(str(MARKET_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM daily_price")
        db_symbols = {row[0] for row in cursor.fetchall()}
        conn.close()
    except sqlite3.DatabaseError as e:
        return CheckResult("mdb价格覆盖率", "FAIL", f"查询失败: {e}")

    covered = sum(1 for s in symbols if s in db_symbols)
    ratio = covered / len(symbols)

    if ratio >= 0.95:
        return CheckResult("mdb价格覆盖率", "PASS",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")
    elif ratio >= 0.80:
        missing = [s for s in symbols if s not in db_symbols][:10]
        return CheckResult("mdb价格覆盖率", "WARN",
                           f"{covered}/{len(symbols)} ({ratio:.0%}), 缺失: {missing}")
    else:
        return CheckResult("mdb价格覆盖率", "FAIL",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")


def _check_mdb_price_freshness() -> CheckResult:
    """检查 market.db daily_price 新鲜度: 最新日期距今交易日。"""
    if not MARKET_DB.exists():
        return CheckResult("mdb价格新鲜度", "FAIL", "market.db 不存在")

    try:
        conn = sqlite3.connect(str(MARKET_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) FROM daily_price")
        row = cursor.fetchone()
        conn.close()
    except sqlite3.DatabaseError as e:
        return CheckResult("mdb价格新鲜度", "FAIL", f"查询失败: {e}")

    if not row or not row[0]:
        return CheckResult("mdb价格新鲜度", "FAIL", "daily_price 无数据")

    date_str = row[0]
    try:
        latest_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return CheckResult("mdb价格新鲜度", "WARN", f"无法解析日期: {date_str}")

    now = datetime.now()
    calendar_days = (now - latest_date).days
    weeks = calendar_days // 7
    trading_days = calendar_days - (weeks * 2)

    if trading_days <= 3:
        return CheckResult("mdb价格新鲜度", "PASS",
                           f"最新: {date_str}, ~{trading_days} 交易日前")
    elif trading_days <= 7:
        return CheckResult("mdb价格新鲜度", "WARN",
                           f"最新: {date_str}, ~{trading_days} 交易日前")
    else:
        return CheckResult("mdb价格新鲜度", "FAIL",
                           f"最新: {date_str}, ~{trading_days} 交易日前")


def _check_mdb_fundamental_coverage(symbols: List[str]) -> CheckResult:
    """检查 market.db income_quarterly 覆盖率 vs 股票池。"""
    if not symbols:
        return CheckResult("mdb基本面覆盖率", "FAIL", "无股票池数据")

    if not MARKET_DB.exists():
        return CheckResult("mdb基本面覆盖率", "FAIL", "market.db 不存在")

    try:
        conn = sqlite3.connect(str(MARKET_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM income_quarterly")
        db_symbols = {row[0] for row in cursor.fetchall()}
        conn.close()
    except sqlite3.DatabaseError as e:
        return CheckResult("mdb基本面覆盖率", "FAIL", f"查询失败: {e}")

    covered = sum(1 for s in symbols if s in db_symbols)
    ratio = covered / len(symbols)

    if ratio >= 0.95:
        return CheckResult("mdb基本面覆盖率", "PASS",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")
    elif ratio >= 0.80:
        return CheckResult("mdb基本面覆盖率", "WARN",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")
    else:
        return CheckResult("mdb基本面覆盖率", "FAIL",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")


def _check_mdb_iv_coverage(symbols: List[str]) -> CheckResult:
    """检查 market.db iv_daily 覆盖率 vs 股票池。"""
    if not symbols:
        return CheckResult("mdb IV覆盖率", "FAIL", "无股票池数据")

    if not MARKET_DB.exists():
        return CheckResult("mdb IV覆盖率", "FAIL", "market.db 不存在")

    try:
        conn = sqlite3.connect(str(MARKET_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM iv_daily")
        db_symbols = {row[0] for row in cursor.fetchall()}
        conn.close()
    except sqlite3.DatabaseError as e:
        return CheckResult("mdb IV覆盖率", "FAIL", f"查询失败: {e}")

    covered = sum(1 for s in symbols if s in db_symbols)
    ratio = covered / len(symbols)

    if ratio >= 0.50:
        return CheckResult("mdb IV覆盖率", "PASS",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")
    elif ratio >= 0.30:
        return CheckResult("mdb IV覆盖率", "WARN",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")
    else:
        return CheckResult("mdb IV覆盖率", "FAIL",
                           f"{covered}/{len(symbols)} ({ratio:.0%})")


def _check_mdb_iv_freshness() -> CheckResult:
    """检查 market.db iv_daily 新鲜度: 最新日期距今交易日。"""
    if not MARKET_DB.exists():
        return CheckResult("mdb IV新鲜度", "FAIL", "market.db 不存在")

    try:
        conn = sqlite3.connect(str(MARKET_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) FROM iv_daily")
        row = cursor.fetchone()
        conn.close()
    except sqlite3.DatabaseError as e:
        return CheckResult("mdb IV新鲜度", "FAIL", f"查询失败: {e}")

    if not row or not row[0]:
        return CheckResult("mdb IV新鲜度", "FAIL", "iv_daily 无数据")

    date_str = row[0]
    try:
        latest_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return CheckResult("mdb IV新鲜度", "WARN", f"无法解析日期: {date_str}")

    now = datetime.now()
    calendar_days = (now - latest_date).days
    weeks = calendar_days // 7
    trading_days = calendar_days - (weeks * 2)

    if trading_days <= 3:
        return CheckResult("mdb IV新鲜度", "PASS",
                           f"最新: {date_str}, ~{trading_days} 交易日前")
    elif trading_days <= 7:
        return CheckResult("mdb IV新鲜度", "WARN",
                           f"最新: {date_str}, ~{trading_days} 交易日前")
    else:
        return CheckResult("mdb IV新鲜度", "FAIL",
                           f"最新: {date_str}, ~{trading_days} 交易日前")


def health_check(verbose: bool = False) -> HealthReport:
    """
    一站式数据健康检查。

    Returns:
        HealthReport with level (PASS/WARN/FAIL) and list of CheckResult
    """
    report = HealthReport()
    symbols = _load_universe_symbols()

    report.add(_check_pool_integrity())
    report.add(_check_price_coverage(symbols))
    report.add(_check_fundamental_coverage(symbols))
    report.add(_check_price_freshness())
    report.add(_check_fundamental_freshness())
    report.add(_check_pool_freshness())
    report.add(_check_company_db())
    report.add(_check_price_pool_consistency(symbols))

    # market.db 检查
    report.add(_check_market_db())
    report.add(_check_mdb_price_coverage(symbols))
    report.add(_check_mdb_price_freshness())
    report.add(_check_mdb_fundamental_coverage(symbols))
    report.add(_check_mdb_iv_coverage(symbols))
    report.add(_check_mdb_iv_freshness())

    if verbose:
        print(report.summary())

    return report


if __name__ == "__main__":
    r = health_check(verbose=True)
    sys.exit(0 if r.level != "FAIL" else 1)
