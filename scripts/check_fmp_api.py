"""
FMP API 测试脚本 (使用新的 stable 端点)
测试内容：
1. API 连通性
2. 获取市值 > 1000 亿美元的股票列表
3. 日线量价数据
4. 基本面数据
"""

import os
import requests
import json
from pathlib import Path
from datetime import datetime, timedelta

API_KEY = os.environ.get("FMP_API_KEY", "")
BASE_URL = "https://financialmodelingprep.com/stable"


def test_api_connection():
    """测试 API 连通性 - 用简单的股票查询"""
    print("=" * 60)
    print("1. 测试 API 连通性")
    print("=" * 60)

    # 用一个简单的 profile 查询测试
    url = f"{BASE_URL}/profile"
    params = {"symbol": "AAPL", "apikey": API_KEY}
    resp = requests.get(url, params=params)

    if resp.status_code == 200:
        data = resp.json()
        if data:
            print(f"✓ API 连接成功")
            print(f"  测试查询 AAPL: {data[0].get('companyName', 'N/A')}")
            return True
        else:
            print(f"✗ API 返回空数据")
            return False
    else:
        print(f"✗ API 连接失败: {resp.status_code}")
        print(f"  响应: {resp.text[:500]}")
        return False


def get_large_cap_stocks():
    """获取市值 > 1000 亿美元的股票"""
    print("\n" + "=" * 60)
    print("2. 获取市值 > 1000 亿美元的股票")
    print("=" * 60)

    # 使用新的 company-screener 端点
    url = f"{BASE_URL}/company-screener"
    params = {
        "marketCapMoreThan": 100_000_000_000,  # 1000 亿美元
        "exchange": "NYSE,NASDAQ",
        "apikey": API_KEY
    }

    resp = requests.get(url, params=params)

    if resp.status_code == 200:
        stocks = resp.json()
        if isinstance(stocks, list) and len(stocks) > 0:
            print(f"✓ 获取成功，共 {len(stocks)} 家公司")

            # 按市值排序
            stocks_sorted = sorted(stocks, key=lambda x: x.get('marketCap', 0), reverse=True)

            print("\n  前 20 大市值公司:")
            print(f"  {'排名':<4} {'代码':<8} {'公司名称':<30} {'市值(亿美元)':<15}")
            print("  " + "-" * 60)

            for i, stock in enumerate(stocks_sorted[:20], 1):
                symbol = stock.get('symbol', 'N/A')
                name = stock.get('companyName', 'N/A')[:28]
                market_cap = stock.get('marketCap', 0) / 1e9
                print(f"  {i:<4} {symbol:<8} {name:<30} ${market_cap:,.0f}B")

            return stocks_sorted
        else:
            print(f"✗ 返回数据格式异常或为空")
            print(f"  响应: {str(stocks)[:500]}")
            return []
    else:
        print(f"✗ 获取失败: {resp.status_code}")
        print(f"  响应: {resp.text[:500]}")
        return []


def test_historical_price(symbol="AAPL"):
    """测试日线量价数据"""
    print("\n" + "=" * 60)
    print(f"3. 测试日线量价数据 ({symbol})")
    print("=" * 60)

    # 使用新的 historical-price-eod 端点
    url = f"{BASE_URL}/historical-price-eod/full"
    params = {
        "symbol": symbol,
        "apikey": API_KEY
    }

    resp = requests.get(url, params=params)

    if resp.status_code == 200:
        data = resp.json()

        # 数据可能直接是列表，也可能在 historical 字段里
        if isinstance(data, list):
            historical = data
        elif isinstance(data, dict):
            historical = data.get('historical', data.get('data', []))
        else:
            historical = []

        if historical:
            print(f"✓ 获取成功，共 {len(historical)} 条日线数据")
            print(f"\n  最近 5 个交易日:")
            print(f"  {'日期':<12} {'开盘':<10} {'最高':<10} {'最低':<10} {'收盘':<10} {'成交量':<15}")
            print("  " + "-" * 70)

            for day in historical[:5]:
                date = day.get('date', 'N/A')
                open_p = day.get('open', 0)
                high = day.get('high', 0)
                low = day.get('low', 0)
                close = day.get('close', 0)
                volume = day.get('volume', 0)
                print(f"  {date:<12} {open_p:<10.2f} {high:<10.2f} {low:<10.2f} {close:<10.2f} {volume:,}")

            return historical
        else:
            print("✗ 返回数据为空")
            print(f"  原始响应: {str(data)[:300]}")
            return []
    else:
        print(f"✗ 获取失败: {resp.status_code}")
        print(f"  响应: {resp.text[:500]}")
        return []


def test_fundamentals(symbol="AAPL"):
    """测试基本面数据"""
    print("\n" + "=" * 60)
    print(f"4. 测试基本面数据 ({symbol})")
    print("=" * 60)

    results = {}

    # 4.1 公司概况
    print("\n  4.1 公司概况:")
    url = f"{BASE_URL}/profile"
    params = {"symbol": symbol, "apikey": API_KEY}
    resp = requests.get(url, params=params)
    if resp.status_code == 200 and resp.json():
        data = resp.json()
        profile = data[0] if isinstance(data, list) else data
        print(f"      公司: {profile.get('companyName')}")
        print(f"      行业: {profile.get('industry')}")
        print(f"      市值: ${profile.get('mktCap', 0)/1e9:,.0f}B")
        print(f"      P/E: {profile.get('pe', 'N/A')}")
        print(f"      Beta: {profile.get('beta', 'N/A')}")
        results['profile'] = profile
    else:
        print(f"      ✗ 获取失败: {resp.status_code}")

    # 4.2 财务比率
    print("\n  4.2 关键财务比率:")
    url = f"{BASE_URL}/ratios"
    params = {"symbol": symbol, "limit": 4, "apikey": API_KEY}
    resp = requests.get(url, params=params)
    if resp.status_code == 200 and resp.json():
        ratios = resp.json()
        latest = ratios[0] if isinstance(ratios, list) and ratios else ratios
        roe = latest.get('returnOnEquity', 'N/A')
        roa = latest.get('returnOnAssets', 'N/A')
        gm = latest.get('grossProfitMargin', 'N/A')
        nm = latest.get('netProfitMargin', 'N/A')
        de = latest.get('debtEquityRatio', 'N/A')

        print(f"      ROE: {roe if roe == 'N/A' else f'{roe:.2%}'}")
        print(f"      ROA: {roa if roa == 'N/A' else f'{roa:.2%}'}")
        print(f"      Gross Margin: {gm if gm == 'N/A' else f'{gm:.2%}'}")
        print(f"      Net Margin: {nm if nm == 'N/A' else f'{nm:.2%}'}")
        print(f"      Debt/Equity: {de if de == 'N/A' else f'{de:.2f}'}")
        results['ratios'] = ratios
    else:
        print(f"      ✗ 获取失败: {resp.status_code}")
        if resp.text:
            print(f"      响应: {resp.text[:200]}")

    # 4.3 收入报表 (季度)
    print("\n  4.3 季度收入 (最近4季):")
    url = f"{BASE_URL}/income-statement"
    params = {"symbol": symbol, "period": "quarter", "limit": 4, "apikey": API_KEY}
    resp = requests.get(url, params=params)
    if resp.status_code == 200 and resp.json():
        income = resp.json()
        if isinstance(income, list) and income:
            print(f"      {'季度':<12} {'营收(B)':<12} {'净利润(B)':<12} {'EPS':<10}")
            print("      " + "-" * 45)
            for q in income:
                date = q.get('date', 'N/A')
                revenue = q.get('revenue', 0) / 1e9
                net_income = q.get('netIncome', 0) / 1e9
                eps = q.get('eps', 'N/A')
                print(f"      {date:<12} ${revenue:<10.1f} ${net_income:<10.1f} {eps}")
            results['income'] = income
    else:
        print(f"      ✗ 获取失败: {resp.status_code}")
        if resp.text:
            print(f"      响应: {resp.text[:200]}")

    return results


def check_api_limits():
    """检查 API 使用限制"""
    print("\n" + "=" * 60)
    print("5. API 使用情况")
    print("=" * 60)
    print("  注意: FMP 免费版限制 250 次/天")
    print("  本次测试大约使用 5-6 次请求")


def main():
    print("\n" + "🔍 FMP API 测试开始 ".center(60, "="))
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. 测试连接
    if not test_api_connection():
        print("\n❌ API 连接失败，终止测试")
        return

    # 2. 获取大市值股票
    large_caps = get_large_cap_stocks()

    # 3. 测试日线数据
    test_historical_price("AAPL")

    # 4. 测试基本面数据
    test_fundamentals("AAPL")

    # 5. API 限制说明
    check_api_limits()

    print("\n" + " 测试完成 ".center(60, "="))

    # 保存大市值股票列表
    if large_caps:
        output_path = str(Path(__file__).parent.parent / "data" / "large_cap_stocks.json")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(large_caps, f, ensure_ascii=False, indent=2)
        print(f"\n📁 大市值股票列表已保存到: {output_path}")


if __name__ == "__main__":
    main()
