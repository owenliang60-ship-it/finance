"""
数据验证模块
- validate_all_data() - 验证所有数据的完整性
- generate_data_report() - 生成数据质量报告
- check_data_freshness() - 检查数据是否过期
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Tuple

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])

from config.settings import POOL_DIR, FUNDAMENTAL_DIR
from src.data.pool_manager import load_universe, get_symbols
from src.data.price_fetcher import load_price_cache, validate_price_data
from src.data.fundamental_fetcher import get_profile, get_ratios, get_income

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 文件路径
PROFILES_FILE = FUNDAMENTAL_DIR / "profiles.json"
UNIVERSE_FILE = POOL_DIR / "universe.json"


def _load_json_meta(path: Path) -> Dict:
    """加载 JSON 文件的元数据"""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("_meta", {})
    except Exception as e:
        logger.error(f"加载 {path} 失败: {e}")
        return {}


def _get_market_db_freshness(table: str) -> Dict:
    """查询 market.db 中某张表的最新日期作为新鲜度指标"""
    try:
        from src.data.market_store import get_store
        store = get_store()
        conn = store._get_conn()
        row = conn.execute(
            f"SELECT MAX(date) as max_date FROM {table}"
        ).fetchone()
        if row and row["max_date"]:
            max_date = datetime.strptime(row["max_date"], "%Y-%m-%d")
            age_days = (datetime.now() - max_date).days
            return {
                "updated_at": row["max_date"],
                "age_days": age_days,
                "is_fresh": age_days <= 14  # 周频更新，14天内算新鲜
            }
    except Exception as e:
        logger.warning("Failed to check %s freshness: %s", table, e)
    return {"updated_at": None, "age_days": -1, "is_fresh": False}


def check_data_freshness(max_days: int = 5) -> Dict[str, Any]:
    """
    检查数据是否过期

    Args:
        max_days: 允许的最大数据年龄 (天)

    Returns:
    {
        "is_fresh": True/False,
        "details": {
            "pool": {"updated_at": "...", "age_days": 2, "is_fresh": True},
            "profiles": {...},
            "ratios": {...},
            "income": {...},
            "price_samples": {...},  # 抽样检查几只股票
        }
    }
    """
    now = datetime.now()
    results = {
        "is_fresh": True,
        "details": {}
    }

    # 1. 检查股票池
    if UNIVERSE_FILE.exists():
        mtime = datetime.fromtimestamp(UNIVERSE_FILE.stat().st_mtime)
        age_days = (now - mtime).days
        results["details"]["pool"] = {
            "updated_at": mtime.strftime("%Y-%m-%d %H:%M:%S"),
            "age_days": age_days,
            "is_fresh": age_days <= max_days
        }
        if age_days > max_days:
            results["is_fresh"] = False

    # 2. 检查基本面数据
    # Profiles: still JSON
    if PROFILES_FILE.exists():
        meta = _load_json_meta(PROFILES_FILE)
        updated_at = meta.get("updated_at", "Unknown")
        if updated_at != "Unknown":
            try:
                update_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                age_days = (now - update_time).days
            except ValueError:
                age_days = -1
        else:
            age_days = -1

        is_fresh = age_days <= max_days if age_days >= 0 else False
        results["details"]["profiles"] = {
            "updated_at": updated_at,
            "age_days": age_days,
            "is_fresh": is_fresh
        }
        if not is_fresh:
            results["is_fresh"] = False
    else:
        results["details"]["profiles"] = {
            "updated_at": None, "age_days": -1, "is_fresh": False, "error": "File not found"
        }
        results["is_fresh"] = False

    # Ratios & Income: market.db
    for name, table in [("ratios", "ratios_annual"), ("income", "income_quarterly")]:
        freshness_info = _get_market_db_freshness(table)
        results["details"][name] = freshness_info
        if not freshness_info.get("is_fresh", False):
            results["is_fresh"] = False

    # 3. 抽样检查量价数据
    symbols = get_symbols()[:5]  # 取前 5 只股票抽样
    price_samples = {}
    for symbol in symbols:
        validation = validate_price_data(symbol)
        price_samples[symbol] = {
            "valid": validation.get("valid", False),
            "latest_date": validation.get("latest_date"),
            "issues": validation.get("issues", [])
        }
        if not validation.get("valid", False):
            results["is_fresh"] = False

    results["details"]["price_samples"] = price_samples

    return results


def validate_all_data() -> Dict[str, Any]:
    """
    验证所有数据的完整性

    Returns:
    {
        "valid": True/False,
        "summary": {
            "pool_count": 150,
            "price_valid": 145,
            "price_invalid": 5,
            "profile_valid": 148,
            "profile_missing": 2,
        },
        "issues": [
            {"symbol": "XXX", "type": "price", "issue": "..."},
            ...
        ]
    }
    """
    results = {
        "valid": True,
        "summary": {
            "pool_count": 0,
            "price_valid": 0,
            "price_invalid": 0,
            "price_missing": 0,
            "profile_valid": 0,
            "profile_missing": 0,
            "ratios_valid": 0,
            "ratios_missing": 0,
        },
        "issues": []
    }

    # 加载股票池
    symbols = get_symbols()
    results["summary"]["pool_count"] = len(symbols)

    if not symbols:
        results["valid"] = False
        results["issues"].append({
            "symbol": None,
            "type": "pool",
            "issue": "股票池为空"
        })
        return results

    logger.info(f"验证 {len(symbols)} 只股票的数据完整性...")

    # 验证每只股票
    for symbol in symbols:
        # 1. 验证量价数据
        price_validation = validate_price_data(symbol)
        if price_validation.get("valid"):
            results["summary"]["price_valid"] += 1
        elif price_validation.get("error") == "No cache":
            results["summary"]["price_missing"] += 1
            results["issues"].append({
                "symbol": symbol,
                "type": "price",
                "issue": "量价数据缺失"
            })
        else:
            results["summary"]["price_invalid"] += 1
            for issue in price_validation.get("issues", []):
                results["issues"].append({
                    "symbol": symbol,
                    "type": "price",
                    "issue": issue
                })

        # 2. 验证公司概况
        profile = get_profile(symbol)
        if profile:
            results["summary"]["profile_valid"] += 1
        else:
            results["summary"]["profile_missing"] += 1
            results["issues"].append({
                "symbol": symbol,
                "type": "profile",
                "issue": "公司概况缺失"
            })

        # 3. 验证财务比率
        ratios = get_ratios(symbol)
        if ratios:
            results["summary"]["ratios_valid"] += 1
        else:
            results["summary"]["ratios_missing"] += 1
            results["issues"].append({
                "symbol": symbol,
                "type": "ratios",
                "issue": "财务比率缺失"
            })

    # 判断整体是否有效
    if results["issues"]:
        # 如果问题太多，标记为无效
        if len(results["issues"]) > len(symbols) * 0.1:  # 超过 10% 有问题
            results["valid"] = False

    logger.info(f"验证完成: price_valid={results['summary']['price_valid']}, "
                f"profile_valid={results['summary']['profile_valid']}")

    return results


def generate_data_report() -> str:
    """
    生成数据质量报告

    Returns:
        Markdown 格式的报告字符串
    """
    report_lines = []
    report_lines.append("# 数据质量报告")
    report_lines.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. 数据新鲜度检查
    report_lines.append("## 1. 数据新鲜度\n")
    freshness = check_data_freshness()
    report_lines.append(f"**整体状态**: {'正常' if freshness['is_fresh'] else '需要更新'}\n")

    report_lines.append("| 数据类型 | 更新时间 | 年龄 (天) | 状态 |")
    report_lines.append("|----------|----------|-----------|------|")

    for key in ["pool", "profiles", "ratios", "income"]:
        detail = freshness["details"].get(key, {})
        updated_at = detail.get("updated_at", "N/A")
        age_days = detail.get("age_days", -1)
        is_fresh = detail.get("is_fresh", False)
        status = "OK" if is_fresh else "STALE"
        report_lines.append(f"| {key} | {updated_at} | {age_days} | {status} |")

    # 量价数据抽样
    report_lines.append("\n**量价数据抽样检查**:\n")
    for symbol, info in freshness["details"].get("price_samples", {}).items():
        status = "OK" if info.get("valid") else "ISSUE"
        latest = info.get("latest_date", "N/A")
        report_lines.append(f"- {symbol}: {status} (最新: {latest})")

    # 2. 数据完整性检查
    report_lines.append("\n## 2. 数据完整性\n")
    validation = validate_all_data()

    report_lines.append(f"**整体状态**: {'正常' if validation['valid'] else '存在问题'}\n")

    summary = validation["summary"]
    report_lines.append(f"- 股票池总数: {summary['pool_count']}")
    report_lines.append(f"- 量价数据: {summary['price_valid']} 有效, "
                       f"{summary['price_invalid']} 无效, {summary['price_missing']} 缺失")
    report_lines.append(f"- 公司概况: {summary['profile_valid']} 有效, {summary['profile_missing']} 缺失")
    report_lines.append(f"- 财务比率: {summary['ratios_valid']} 有效, {summary['ratios_missing']} 缺失")

    # 3. 问题列表
    if validation["issues"]:
        report_lines.append("\n## 3. 问题列表\n")
        report_lines.append("| 股票代码 | 类型 | 问题 |")
        report_lines.append("|----------|------|------|")

        # 最多显示 50 个问题
        for issue in validation["issues"][:50]:
            symbol = issue.get("symbol") or "N/A"
            issue_type = issue.get("type", "unknown")
            issue_desc = issue.get("issue", "")
            report_lines.append(f"| {symbol} | {issue_type} | {issue_desc} |")

        if len(validation["issues"]) > 50:
            report_lines.append(f"\n*... 还有 {len(validation['issues']) - 50} 个问题未显示*")

    # 4. 建议
    report_lines.append("\n## 4. 建议\n")
    if not freshness["is_fresh"]:
        report_lines.append("- **更新数据**: 部分数据已过期，建议运行 `scripts/update_data.py` 更新")
    if summary["price_missing"] > 0:
        report_lines.append(f"- **补充量价数据**: {summary['price_missing']} 只股票缺少量价数据")
    if summary["profile_missing"] > 0:
        report_lines.append(f"- **补充基本面数据**: {summary['profile_missing']} 只股票缺少公司概况")
    if validation["valid"] and freshness["is_fresh"]:
        report_lines.append("- 数据状态良好，无需操作")

    return "\n".join(report_lines)


def print_data_report():
    """打印数据质量报告到控制台"""
    report = generate_data_report()
    print(report)


if __name__ == "__main__":
    print_data_report()
