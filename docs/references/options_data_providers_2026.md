# US Equity Options Data Providers Comparison - 2026

**Research Date**: 2026-02-11
**Use Case**: Personal trading/analysis system for US equity options

---

## Executive Summary

**Best Overall Value**: **Tradier** (free with brokerage account)
**Best Budget API**: **marketdata.app** ($30/month annual)
**Best Free Library**: **yfinance** + **blackscholes** Python library (compute your own Greeks)
**Best Historical Data**: **DiscountOptionData** (one-time purchase, 2005-present)

---

## Detailed Comparison Table

| Provider | Cost | Chain Data | Greeks | IV | Historical | Real-time/Delay | Rate Limits | Python SDK | Key Gotchas |
|----------|------|------------|--------|----|-----------:|-----------------|-------------|------------|-------------|
| **Tradier** | FREE (with brokerage account) | ✅ | ✅ | ✅ | ✅ | Real-time (account holders only) | Unknown | ✅ | Requires funded brokerage account; $50 inactivity fee if <$2K balance + <2 trades/year |
| **Polygon.io** | Varies (no specific 2026 pricing) | ✅ | ✅ | ✅ | Years of tick data | Real-time + historical | Unknown | ✅ | Premium pricing not disclosed; likely >$50/month for options |
| **marketdata.app** | $30/month (annual) or $75/month | ✅ | ✅ (real-time) | ✅ (real-time) | ✅ (quotes back to 2005) | Real-time | Unknown | ✅ | Greeks/IV NOT available for historical EOD data (coming soon) |
| **yfinance** | FREE | ✅ | ❌ | ✅ | Limited | 15-min delayed | No official limits (scraping) | ✅ | NO Greeks; must compute yourself; scraping Yahoo (fragile) |
| **Schwab API** | FREE (with brokerage account) | ✅ | Unknown | Unknown | Unknown | Real-time (account holders) | Unknown | Limited docs | API still under development as of early 2026; TD Ameritrade API shut down May 2024 |
| **IBKR API** | FREE (with brokerage account) | ✅ | ✅ | ✅ | ✅ | Real-time (account holders) | Unknown | ✅ (ib_insync) | Most market data is NOT free via API (considered "off-platform"); requires separate data subscriptions |
| **Unusual Whales** | $50/month (basic) | ✅ | Unknown | Unknown | ✅ | Real-time flow | API pricing updated May 2025 | ✅ | Focus on flow/unusual activity, not comprehensive chain data; API tiers higher than $50 |
| **CBOE DataShop** | Unknown (likely expensive) | ✅ | ✅ | ✅ | ✅ (from 2004) | EOD + historical | Unknown | Unknown | Institutional focus; pricing not public; likely $$$$ |
| **Nasdaq Data Link** | Free tier + premium datasets | Limited | Unknown | Unknown | ✅ | EOD | Free: 300/10s, 50K/day | ✅ (quandl) | Most datasets are premium (a la carte); options coverage unclear |
| **ORATS** | $199/month (intraday) | ✅ | ✅ | ✅ (comprehensive) | ✅ ($1500 one-time historical) | 15-min delayed (standard) or <10s (live API) | Unknown | ✅ | Expensive; focus on analytics/IV; historical data separate purchase |
| **Alpha Vantage** | FREE (25 req/day) or $249.99/month (1200 req/min) | ✅ | ❌ | Unknown | Limited (100 points/request on free) | Varies by tier | Free: 25/day | ✅ | Very limited free tier; must compute Greeks yourself |
| **Theta Data** | Free (30 days EOD) + paid tiers | ✅ | ✅ | ✅ | ✅ (up to 10 years) | Real-time + historical tick | Unknown | ✅ (thetadata-python) | Pricing not disclosed; likely premium for real-time |
| **DiscountOptionData** | ~$200-300 one-time (2005-present) | ✅ | ✅ | Unknown | ✅ (2005-present, ~4000 symbols) | Historical only (no real-time) | N/A (bulk download) | Unknown | One-time purchase; NO real-time; good for backtesting only |

---

## Free Python Libraries for Computing Greeks

If you have options chain data (strikes, prices, expiry, underlying price), you can compute Greeks yourself using:

| Library | Features | Installation |
|---------|----------|--------------|
| **blackscholes** | Delta, Gamma, Theta, Vega, Rho (up to 3rd order Greeks); supports Black-Scholes-Merton + Black-76 | `pip install blackscholes` |
| **py_vollib** | Option prices, IV, Greeks; analytical + numerical Greeks | `pip install py_vollib` |
| **pygreeks** | Black-Scholes + IV using PyTorch autograd or numerical approx | `pip install pygreeks` |
| **pyBlackScholesAnalytics** | European options pricing, P&L, Greeks | `pip install pyBlackScholesAnalytics` |

**Recommended**: `blackscholes` (most comprehensive, actively maintained)

---

## Free Historical IV Data Sources

| Source | Coverage | Cost | Access |
|--------|----------|------|--------|
| **Option Strategist** | Weekly IV, HV, IV percentile for all stock/index/futures options | FREE | [optionstrategist.com/calculators/free-volatility-data](https://www.optionstrategist.com/calculators/free-volatility-data) |
| **IVolatility** | 20-min delayed quotes + historical | FREE (delayed) | [ivolatility.com](https://www.ivolatility.com/) |
| **Market Chameleon** | IV charts for individual stocks | FREE (web interface) | [marketchameleon.com](https://marketchameleon.com/) |

**For API access to historical IV**: Most providers charge premium fees (ORATS, OptionMetrics, CBOE DataShop).

---

## Minimum Viable Data for Options Strategy Recommender

To build a basic options strategy recommender/calculator, you need:

1. **Options Chain**: Strikes, expiries, bid/ask, volume, open interest
2. **Underlying Price**: Current stock price
3. **Risk-free Rate**: Use current 3-month T-Bill rate (free from FRED)
4. **Implied Volatility**: Either from API OR compute from option prices

**You can skip**:
- Pre-computed Greeks (calculate them yourself)
- Real-time data (15-min delay or EOD is fine for analysis)
- Historical tick data (unless doing HFT)

**Recommended minimal stack**:
- **yfinance** (free options chain + IV)
- **blackscholes** (compute Greeks)
- **FRED API** (free risk-free rate)
- Cost: **$0/month**

---

## Detailed Provider Analysis

### 1. Tradier ⭐ **BEST FREE OPTION**

**Pricing**: FREE with brokerage account ($0 equity trades, $0.35/contract options)

**Pros**:
- Real-time options chain, Greeks, IV for account holders
- Well-documented REST API
- No monthly data fees if you have an account
- Python SDK available

**Cons**:
- Requires opening brokerage account
- $50 inactivity fee if balance <$2K AND <2 trades/year
- Must maintain account to keep API access

**Verdict**: Best option if you're willing to open an account and keep small balance.

---

### 2. marketdata.app ⭐ **BEST BUDGET API**

**Pricing**: $30/month (annual commitment) or $75/month

**Pros**:
- Real-time Greeks, IV with every quote
- Historical quotes back to 2005
- Clean REST API
- No brokerage account required

**Cons**:
- Greeks/IV NOT available for historical EOD data (coming soon as of 2026)
- Higher monthly cost than yfinance + DIY Greeks

**Verdict**: Best paid option for real-time analysis without brokerage account.

---

### 3. yfinance + blackscholes ⭐ **BEST FREE (NO ACCOUNT)**

**Pricing**: FREE

**Pros**:
- No account required
- Covers all US options
- Python library easy to use
- Can compute Greeks yourself

**Cons**:
- No official API (scraping Yahoo Finance)
- Fragile (Yahoo can change format anytime)
- Must compute Greeks yourself (adds complexity)
- 15-min delayed data

**Verdict**: Best for hobbyists/learning; not suitable for production systems.

---

### 4. DiscountOptionData ⭐ **BEST HISTORICAL DATA**

**Pricing**: ~$200-300 one-time (2005-present, all ~4000 symbols)

**Pros**:
- 20+ years of historical data
- One-time purchase (no subscription)
- Includes Greeks
- Great for backtesting

**Cons**:
- NO real-time data
- NO API (bulk CSV download)
- Historical only

**Verdict**: Excellent for backtesting strategies; must combine with real-time source.

---

### 5. IBKR API

**Pricing**: FREE with brokerage account (but market data subscriptions required)

**Pros**:
- Institutional-grade data
- Global coverage
- Python SDK (ib_insync)
- Real-time if subscribed

**Cons**:
- API data considered "off-platform" → most data requires separate subscriptions
- Complex setup
- Subscriptions can add $10-50/month per exchange

**Verdict**: Good for active traders already on IBKR; not cost-effective for data-only use.

---

### 6. ORATS

**Pricing**: $199/month (1-min intraday), $1500 one-time (historical from Aug 2020)

**Pros**:
- Best-in-class IV analytics
- Comprehensive Greeks
- Multiple IV metrics (ATM, term structure, constant maturity)
- Professional-grade

**Cons**:
- Expensive
- Historical data separate $1500 purchase
- Overkill for personal use

**Verdict**: Only if you need professional IV analytics; too expensive for most retail.

---

## Recommendations by Use Case

### Use Case 1: "I just want to analyze options strategies for free"
**Solution**: yfinance + blackscholes
**Cost**: $0/month
**Limitations**: 15-min delay, fragile, must compute Greeks

### Use Case 2: "I need real-time data and willing to open brokerage account"
**Solution**: Tradier brokerage account
**Cost**: $0/month (maintain >$2K balance OR execute 2+ trades/year)
**Limitations**: Requires brokerage account

### Use Case 3: "I want reliable API without brokerage account"
**Solution**: marketdata.app
**Cost**: $30/month (annual)
**Limitations**: Historical Greeks/IV not available yet

### Use Case 4: "I need historical data for backtesting"
**Solution**: DiscountOptionData (historical) + yfinance (real-time)
**Cost**: ~$250 one-time + $0/month
**Limitations**: Two separate data sources

### Use Case 5: "I'm a professional trader needing institutional data"
**Solution**: ORATS or OptionMetrics
**Cost**: $200-500/month
**Limitations**: Expensive

---

## Technical Considerations

### Can You Compute Greeks Yourself?

**YES** - Black-Scholes Greeks calculation is straightforward:

```python
from blackscholes import BlackScholes

# Example: Compute Greeks for a call option
bs = BlackScholes(
    spot=100,        # Underlying price
    strike=105,      # Strike price
    time=30/365,     # Days to expiry (in years)
    rate=0.05,       # Risk-free rate
    vol=0.25         # Implied volatility
)

delta = bs.delta()
gamma = bs.gamma()
theta = bs.theta()
vega = bs.vega()
rho = bs.rho()
```

**Requirements**:
- Underlying price (from yfinance or any stock API)
- Strike price (from options chain)
- Time to expiration (from options chain)
- Risk-free rate (from FRED API - free)
- Implied volatility (from options chain OR compute using py_vollib)

**Accuracy**: Black-Scholes is industry standard for European options. American options (most equity options) require more complex models, but Black-Scholes Greeks are close enough for analysis.

---

## Final Recommendation

**For your requirements** (personal trading/analysis, budget <$50/month):

### Option A: **Tradier** (BEST if willing to open account)
- Open Tradier brokerage account
- Maintain $2000 balance OR execute 2 trades/year (avoid inactivity fee)
- Get real-time options chain + Greeks + IV for FREE
- Cost: **$0/month**

### Option B: **marketdata.app** (BEST if no brokerage account)
- $30/month annual commitment
- Real-time Greeks, IV, options chain
- Historical data back to 2005
- Cost: **$30/month**

### Option C: **yfinance + blackscholes** (BEST free, no account)
- 100% free
- Compute Greeks yourself
- 15-min delayed data (acceptable for analysis)
- Cost: **$0/month**
- Caveat: Fragile (Yahoo can break anytime)

### For Historical IV (add-on):
- **Option Strategist** (free weekly IV data)
- **DiscountOptionData** ($250 one-time for 2005-present)

---

## Sources

- [Tradier Pricing & API](https://tradier.com/individuals/pricing)
- [Polygon.io Options Data](https://polygon.io/options)
- [CBOE DataShop](https://www.livevol.com/stock-options-analysis-data/)
- [yfinance GitHub Issue #1465](https://github.com/ranaroussi/yfinance/issues/1465)
- [Schwab Developer Portal](https://developer.schwab.com/)
- [IBKR API Documentation](https://www.interactivebrokers.com/en/trading/ib-api.php)
- [Unusual Whales Pricing](https://unusualwhales.com/pricing)
- [marketdata.app Pricing](https://www.marketdata.app/pricing/)
- [Nasdaq Data Link](https://data.nasdaq.com/)
- [ORATS API Documentation](https://docs.orats.io/)
- [Alpha Vantage API](https://www.alphavantage.co/)
- [Theta Data](https://www.thetadata.net/)
- [DiscountOptionData](https://discountoptiondata.com/)
- [blackscholes Python Library](https://github.com/CarloLepelaars/blackscholes)
- [Option Strategist Free Volatility Data](https://www.optionstrategist.com/calculators/free-volatility-data)
- [Best Stock Data APIs 2026](https://brightdata.com/blog/web-data/best-stock-data-providers)
