#!/usr/bin/env python3
"""
Attention Engine — CLI entry point.

Usage:
    # Normal weekly scan (Reddit + News + GT → score → report)
    python3 scripts/scan_attention.py

    # Historical backfill (GT + News only; Reddit has no history)
    python3 scripts/scan_attention.py --backfill --from 2025-08-01 --to 2026-02-13

    # Individual source scan
    python3 scripts/scan_attention.py --reddit-only
    python3 scripts/scan_attention.py --news-only
    python3 scripts/scan_attention.py --gt-only

    # Scoring + report only (from existing data)
    python3 scripts/scan_attention.py --score-only

    # Seed keywords from config
    python3 scripts/scan_attention.py --seed-keywords
"""
import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Add project root to path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import (
    ATTENTION_DIR,
    REDDIT_TICKER_BLACKLIST,
    THEME_KEYWORDS_SEED,
)
from terminal.attention_store import get_attention_store
from terminal.attention import (
    collect_google_trends,
    collect_news_mentions,
    collect_reddit_mentions,
    compute_attention_ranking,
    extract_hot_keywords,
)
from terminal.attention_report import generate_attention_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scan_attention")


def _get_monday(d: date) -> str:
    """Get ISO date of Monday of the week containing d."""
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def _get_known_tickers() -> set:
    """Get the set of known tickers from the stock pool + common large caps."""
    known = set()
    try:
        from src.data.stock_pool import get_stock_list
        pool = get_stock_list()
        known.update(t.upper() for t in pool)
    except Exception:
        pass
    # Add some common tickers that might not be in the pool
    known.update([
        "NVDA", "AMD", "MU", "AVGO", "MRVL", "TSM", "INTC", "QCOM",
        "MSFT", "AAPL", "GOOG", "GOOGL", "META", "AMZN", "TSLA",
        "NFLX", "CRM", "ORCL", "PLTR", "SNOW", "NET", "CRWD",
        "PANW", "ZS", "FTNT", "IONQ", "IBM", "WDC", "ARM",
    ])
    return known


def seed_keywords():
    """Seed the theme_keywords table from config."""
    store = get_attention_store()
    count = store.seed_keywords(THEME_KEYWORDS_SEED)
    logger.info("Seeded %d keywords", count)
    return count


def run_reddit_scan(known_tickers: set) -> dict:
    """Run Reddit mention scan and save to store."""
    store = get_attention_store()
    result = collect_reddit_mentions(
        known_tickers=known_tickers,
        blacklist=REDDIT_TICKER_BLACKLIST,
    )

    if not result["mentions"]:
        logger.warning("Reddit scan returned no mentions")
        return result

    # Save to SQLite
    records = []
    scan_date = result["scan_date"]
    for ticker, subs in result["mentions"].items():
        for sub_name, count in subs.items():
            records.append({
                "ticker": ticker,
                "scan_date": scan_date,
                "subreddit": sub_name,
                "mention_count": count,
                "sample_posts": result["total_posts"] // len(result["mentions"]),
            })
    store.save_reddit_batch(records)
    logger.info("Saved %d Reddit mention records", len(records))

    # Extract hot keywords for GT supplement
    hot_kws = extract_hot_keywords(result["titles"], min_freq=5, top_n=10)
    if hot_kws:
        logger.info("Hot Reddit keywords: %s", ", ".join(f"{k}({c})" for k, c in hot_kws[:5]))
        for kw, _ in hot_kws:
            store.save_keyword(kw, source="reddit_auto", category="reddit_trending")

    return result


def run_news_scan(tickers: list) -> dict:
    """Run Finnhub news scan and save to store."""
    store = get_attention_store()
    scan_date = date.today().isoformat()
    results = collect_news_mentions(tickers, days_back=7)

    if not results:
        logger.warning("News scan returned no results")
        return results

    records = []
    for ticker, data in results.items():
        records.append({
            "ticker": ticker,
            "scan_date": scan_date,
            "article_count": data["count"],
            "avg_sentiment": data.get("avg_sentiment"),
            "source": "finnhub",
        })
    store.save_news_batch(records)
    logger.info("Saved %d news mention records", len(records))
    return results


def run_gt_scan() -> dict:
    """Run Google Trends scan and save to store."""
    store = get_attention_store()
    keywords_data = store.get_keywords(active_only=True)

    if not keywords_data:
        logger.warning("No active keywords. Run --seed-keywords first.")
        return {}

    keywords = [kw["keyword"] for kw in keywords_data]
    logger.info("Scanning GT for %d keywords...", len(keywords))

    results = collect_google_trends(keywords)

    if not results:
        logger.warning("GT scan returned no results")
        return results

    # Save to SQLite
    total = 0
    for keyword, data_points in results.items():
        records = [
            {
                "keyword": keyword,
                "week_start": pt["week_start"],
                "interest_score": pt["interest_score"],
                "anchor_ratio": pt["anchor_ratio"],
            }
            for pt in data_points
        ]
        total += store.save_trends_batch(records)
    logger.info("Saved %d GT data points", total)
    return results


def run_scoring(week_start: str) -> list:
    """Compute attention ranking and save snapshots."""
    store = get_attention_store()
    rankings = compute_attention_ranking(store, week_start=week_start, top_n=30)

    if not rankings:
        logger.warning("No data to rank for week %s", week_start)
        return []

    store.save_snapshots_batch(rankings)
    logger.info("Saved %d attention snapshots for %s", len(rankings), week_start)

    # Generate report
    report_path = generate_attention_report(week_start, rankings)
    logger.info("Report: %s", report_path)

    return rankings


def run_backfill(start_date: str, end_date: str, tickers: list):
    """Historical backfill for GT + News (Reddit has no history via PRAW)."""
    from terminal.attention import backfill_news

    store = get_attention_store()

    # 1. GT backfill — fetch full range in one go
    logger.info("=== GT Backfill: %s to %s ===", start_date, end_date)
    keywords_data = store.get_keywords(active_only=True)
    if keywords_data:
        keywords = [kw["keyword"] for kw in keywords_data]
        timeframe = f"{start_date} {end_date}"
        results = collect_google_trends(keywords, timeframe=timeframe)
        total = 0
        for keyword, data_points in results.items():
            records = [
                {
                    "keyword": keyword,
                    "week_start": pt["week_start"],
                    "interest_score": pt["interest_score"],
                    "anchor_ratio": pt["anchor_ratio"],
                }
                for pt in data_points
            ]
            total += store.save_trends_batch(records)
        logger.info("GT backfill: saved %d data points", total)
    else:
        logger.warning("No keywords for GT backfill. Run --seed-keywords first.")

    # 2. News backfill — week by week
    logger.info("=== News Backfill: %s to %s ===", start_date, end_date)
    news_results = backfill_news(tickers, start_date, end_date)
    total_news = 0
    for ticker, weekly_data in news_results.items():
        for wd in weekly_data:
            store.save_news_mention(
                ticker=ticker,
                scan_date=wd["scan_date"],
                article_count=wd["article_count"],
                avg_sentiment=wd.get("avg_sentiment"),
                source="finnhub",
            )
            total_news += 1
    logger.info("News backfill: saved %d records", total_news)

    # 3. Compute historical rankings week by week
    logger.info("=== Computing historical rankings ===")
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    current = start
    while current <= end:
        ws = _get_monday(current)
        rankings = compute_attention_ranking(store, week_start=ws, top_n=30)
        if rankings:
            store.save_snapshots_batch(rankings)
        current += timedelta(days=7)
    logger.info("Historical rankings computed")


def main():
    parser = argparse.ArgumentParser(description="Attention Engine Scanner")
    parser.add_argument("--backfill", action="store_true", help="Historical backfill mode")
    parser.add_argument("--from", dest="from_date", help="Backfill start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="Backfill end date (YYYY-MM-DD)")
    parser.add_argument("--reddit-only", action="store_true", help="Only scan Reddit")
    parser.add_argument("--news-only", action="store_true", help="Only scan Finnhub news")
    parser.add_argument("--gt-only", action="store_true", help="Only scan Google Trends")
    parser.add_argument("--score-only", action="store_true", help="Only compute scores + report")
    parser.add_argument("--seed-keywords", action="store_true", help="Seed keywords from config")
    args = parser.parse_args()

    # Ensure data dir exists
    ATTENTION_DIR.mkdir(parents=True, exist_ok=True)

    known_tickers = _get_known_tickers()
    week_start = _get_monday(date.today())

    if args.seed_keywords:
        seed_keywords()
        return

    if args.backfill:
        if not args.from_date or not args.to_date:
            logger.error("--backfill requires --from and --to dates")
            sys.exit(1)
        seed_keywords()  # Ensure keywords exist
        run_backfill(args.from_date, args.to_date, sorted(known_tickers))
        # Generate final report for latest week
        run_scoring(week_start)
        return

    if args.score_only:
        run_scoring(week_start)
        return

    if args.reddit_only:
        run_reddit_scan(known_tickers)
        return

    if args.news_only:
        tickers = sorted(known_tickers)
        run_news_scan(tickers)
        return

    if args.gt_only:
        run_gt_scan()
        return

    # Full scan: Reddit → News → GT → Score → Report
    logger.info("=== Full Attention Scan (week: %s) ===", week_start)

    # 1. Reddit
    logger.info("--- Phase 1: Reddit ---")
    reddit_result = run_reddit_scan(known_tickers)
    reddit_tickers = set(reddit_result.get("mentions", {}).keys())

    # 2. News — pool + Reddit discoveries
    logger.info("--- Phase 2: Finnhub News ---")
    all_tickers = sorted(known_tickers | reddit_tickers)
    run_news_scan(all_tickers)

    # 3. Google Trends
    logger.info("--- Phase 3: Google Trends ---")
    run_gt_scan()

    # 4. Score + Report
    logger.info("--- Phase 4: Scoring + Report ---")
    rankings = run_scoring(week_start)

    if rankings:
        logger.info("=== Top 5 ===")
        for r in rankings[:5]:
            logger.info(
                "  #%d %s: composite=%.2f reddit_z=%.2f news_z=%.2f gt_z=%.2f",
                r["rank"], r["ticker"], r["composite_score"],
                r["reddit_zscore"], r["news_zscore"], r["trends_zscore"],
            )

    logger.info("=== Scan complete ===")


if __name__ == "__main__":
    main()
