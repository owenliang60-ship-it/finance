"""
Attention Engine — Data collectors and composite scoring.

Three data sources:
  1. Reddit (PRAW) — ticker mention counts from hot subreddits
  2. Finnhub — news article counts + sentiment per ticker
  3. Google Trends (pytrends) — keyword interest over time

Plus composite Z-score ranking.
"""
import logging
import math
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticker extraction helpers
# ---------------------------------------------------------------------------

# Matches $TICKER or standalone 1-5 uppercase letters
_DOLLAR_TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b")
_BARE_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")


def extract_tickers(
    text: str,
    known_tickers: Optional[Set[str]] = None,
    blacklist: Optional[Set[str]] = None,
) -> List[str]:
    """Extract stock tickers from text.

    Strategy:
    1. $TICKER patterns (most reliable) — always accepted
    2. Bare uppercase words — only accepted if in known_tickers set

    Returns deduplicated list of tickers found.
    """
    if not text:
        return []

    blacklist = blacklist or set()
    found: Set[str] = set()

    # Pass 1: $TICKER patterns (always trusted)
    for match in _DOLLAR_TICKER_RE.finditer(text):
        sym = match.group(1)
        if sym not in blacklist:
            found.add(sym)

    # Pass 2: bare uppercase — only if we have a known set to validate against
    if known_tickers:
        for match in _BARE_TICKER_RE.finditer(text):
            sym = match.group(1)
            if sym in known_tickers and sym not in blacklist:
                found.add(sym)

    return sorted(found)


def extract_hot_keywords(
    titles: List[str],
    min_freq: int = 5,
    top_n: int = 20,
    stopwords: Optional[Set[str]] = None,
) -> List[Tuple[str, int]]:
    """Extract high-frequency non-ticker keywords from post titles.

    Returns [(keyword, count)] sorted by count descending.
    Used to auto-supplement Google Trends keyword list.
    """
    _default_stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above",
        "below", "between", "out", "off", "over", "under", "again",
        "further", "then", "once", "here", "there", "when", "where",
        "why", "how", "all", "each", "every", "both", "few", "more",
        "most", "other", "some", "such", "no", "nor", "not", "only",
        "own", "same", "so", "than", "too", "very", "just", "because",
        "but", "and", "or", "if", "while", "about", "up", "down",
        "this", "that", "these", "those", "what", "which", "who",
        "whom", "its", "it", "he", "she", "they", "them", "we",
        "you", "my", "your", "his", "her", "our", "their", "i", "me",
        "him", "us", "am", "don", "t", "s", "re", "ve", "ll", "d",
    }
    stops = stopwords or _default_stopwords

    word_counts: Counter = Counter()
    word_re = re.compile(r"[a-z]{3,}")  # lowercase words, 3+ chars
    for title in titles:
        words = word_re.findall(title.lower())
        for w in words:
            if w not in stops:
                word_counts[w] += 1

    results = [(w, c) for w, c in word_counts.most_common(top_n * 2) if c >= min_freq]
    return results[:top_n]


# ---------------------------------------------------------------------------
# Reddit collector
# ---------------------------------------------------------------------------

def collect_reddit_mentions(
    subreddits: Optional[List[str]] = None,
    posts_per_sub: int = 200,
    sleep_between: float = 0.5,
    known_tickers: Optional[Set[str]] = None,
    blacklist: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Scan Reddit subreddits for ticker mentions.

    Returns:
        {
            "mentions": {ticker: {subreddit: count}},
            "titles": [list of post titles for keyword extraction],
            "total_posts": int,
            "scan_date": str,
        }
    """
    try:
        import praw
    except ImportError:
        logger.error("praw not installed. Run: pip install praw")
        return {"mentions": {}, "titles": [], "total_posts": 0, "scan_date": ""}

    from config.settings import (
        REDDIT_CLIENT_ID,
        REDDIT_CLIENT_SECRET,
        REDDIT_SUBREDDITS,
        REDDIT_TICKER_BLACKLIST,
        REDDIT_USER_AGENT,
    )

    subreddits = subreddits or REDDIT_SUBREDDITS
    blacklist = blacklist or REDDIT_TICKER_BLACKLIST
    scan_date = date.today().isoformat()

    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        logger.warning("Reddit credentials not configured. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env")
        return {"mentions": {}, "titles": [], "total_posts": 0, "scan_date": scan_date}

    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )

    mentions: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    all_titles: List[str] = []
    total_posts = 0

    for sub_name in subreddits:
        try:
            subreddit = reddit.subreddit(sub_name)
            posts_seen = 0

            # Fetch hot + new posts
            for listing_fn in [subreddit.hot, subreddit.new]:
                limit = posts_per_sub // 2
                for post in listing_fn(limit=limit):
                    text = f"{post.title} {post.selftext or ''}"
                    tickers = extract_tickers(text, known_tickers=known_tickers, blacklist=blacklist)
                    for t in tickers:
                        mentions[t][sub_name] += 1
                    all_titles.append(post.title)
                    posts_seen += 1

            total_posts += posts_seen
            logger.info("Reddit r/%s: scanned %d posts", sub_name, posts_seen)

        except Exception as e:
            logger.error("Reddit r/%s failed: %s", sub_name, e)

        if sleep_between > 0:
            time.sleep(sleep_between)

    # Convert defaultdict to regular dict
    result_mentions = {t: dict(subs) for t, subs in mentions.items()}

    logger.info(
        "Reddit scan complete: %d unique tickers from %d posts across %d subs",
        len(result_mentions), total_posts, len(subreddits),
    )

    return {
        "mentions": result_mentions,
        "titles": all_titles,
        "total_posts": total_posts,
        "scan_date": scan_date,
    }


# ---------------------------------------------------------------------------
# Finnhub news collector
# ---------------------------------------------------------------------------

def collect_news_mentions(
    tickers: List[str],
    days_back: int = 7,
    sleep_between: float = 1.0,
) -> Dict[str, Dict[str, Any]]:
    """Collect news article counts + sentiment from Finnhub.

    Returns: {ticker: {"count": int, "avg_sentiment": float|None}}
    """
    try:
        import finnhub
    except ImportError:
        logger.error("finnhub-python not installed. Run: pip install finnhub-python")
        return {}

    from config.settings import FINNHUB_API_KEY

    if not FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not configured in .env")
        return {}

    client = finnhub.Client(api_key=FINNHUB_API_KEY)
    today = date.today()
    from_date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    results: Dict[str, Dict[str, Any]] = {}

    for i, ticker in enumerate(tickers):
        try:
            news = client.company_news(ticker, _from=from_date, to=to_date)
            article_count = len(news) if news else 0

            # Try to get sentiment
            avg_sentiment = None
            try:
                sentiment_data = client.news_sentiment(ticker)
                if sentiment_data and "sentiment" in sentiment_data:
                    s = sentiment_data["sentiment"]
                    avg_sentiment = s.get("bullishPercent", 0.5) - 0.5  # normalize to -0.5..0.5
            except Exception:
                pass  # sentiment endpoint may not always work

            results[ticker] = {
                "count": article_count,
                "avg_sentiment": avg_sentiment,
            }

            if (i + 1) % 20 == 0:
                logger.info("Finnhub news: processed %d/%d tickers", i + 1, len(tickers))

        except Exception as e:
            logger.error("Finnhub news for %s failed: %s", ticker, e)
            results[ticker] = {"count": 0, "avg_sentiment": None}

        if sleep_between > 0 and i < len(tickers) - 1:
            time.sleep(sleep_between)

    logger.info("Finnhub news scan complete: %d tickers", len(results))
    return results


def backfill_news(
    tickers: List[str],
    start_date: str,
    end_date: str,
    sleep_between: float = 1.0,
) -> Dict[str, List[Dict[str, Any]]]:
    """Backfill news data week by week for historical analysis.

    Returns: {ticker: [{scan_date, article_count, avg_sentiment}]}
    """
    try:
        import finnhub
    except ImportError:
        logger.error("finnhub-python not installed")
        return {}

    from config.settings import FINNHUB_API_KEY

    if not FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not configured")
        return {}

    client = finnhub.Client(api_key=FINNHUB_API_KEY)

    # Generate week boundaries
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    weeks: List[Tuple[str, str]] = []
    current = start
    while current < end:
        week_end = min(current + timedelta(days=6), end)
        weeks.append((current.isoformat(), week_end.isoformat()))
        current = week_end + timedelta(days=1)

    results: Dict[str, List[Dict]] = defaultdict(list)

    total_calls = len(tickers) * len(weeks)
    call_count = 0

    for ticker in tickers:
        for week_start, week_end in weeks:
            try:
                news = client.company_news(ticker, _from=week_start, to=week_end)
                article_count = len(news) if news else 0
                results[ticker].append({
                    "scan_date": week_start,
                    "article_count": article_count,
                    "avg_sentiment": None,
                })
            except Exception as e:
                logger.error("Backfill news %s %s: %s", ticker, week_start, e)

            call_count += 1
            if call_count % 50 == 0:
                logger.info("News backfill: %d/%d calls", call_count, total_calls)

            if sleep_between > 0:
                time.sleep(sleep_between)

    return dict(results)


# ---------------------------------------------------------------------------
# Google Trends collector
# ---------------------------------------------------------------------------

def collect_google_trends(
    keywords: List[str],
    anchor_keyword: Optional[str] = None,
    timeframe: Optional[str] = None,
    sleep_between: Optional[float] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch Google Trends data for keywords with anchor normalization.

    Each batch: up to 4 keywords + 1 anchor (pytrends limit = 5/batch).
    Returns: {keyword: [{week_start, interest_score, anchor_ratio}]}
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.error("pytrends not installed. Run: pip install pytrends")
        return {}

    from config.settings import GT_ANCHOR_KEYWORD, GT_DEFAULT_TIMEFRAME, GT_SLEEP_SECONDS

    anchor = anchor_keyword or GT_ANCHOR_KEYWORD
    tf = timeframe or GT_DEFAULT_TIMEFRAME
    sleep_sec = sleep_between if sleep_between is not None else GT_SLEEP_SECONDS

    pytrends = TrendReq(hl="en-US", tz=360)

    # Batch keywords: 4 per batch + anchor
    batches: List[List[str]] = []
    for i in range(0, len(keywords), 4):
        batch = keywords[i:i + 4]
        batches.append(batch)

    results: Dict[str, List[Dict]] = defaultdict(list)

    for batch_idx, batch in enumerate(batches):
        kw_list = batch + [anchor]
        try:
            pytrends.build_payload(kw_list, timeframe=tf)
            df = pytrends.interest_over_time()

            if df.empty:
                logger.warning("GT batch %d returned empty data", batch_idx)
                continue

            # Drop the isPartial column if present
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])

            for kw in batch:
                if kw not in df.columns:
                    continue
                for idx, row in df.iterrows():
                    week_start = idx.strftime("%Y-%m-%d")
                    interest = int(row[kw])
                    anchor_val = int(row[anchor]) if anchor in df.columns else 1
                    anchor_ratio = interest / max(anchor_val, 1)
                    results[kw].append({
                        "week_start": week_start,
                        "interest_score": interest,
                        "anchor_ratio": round(anchor_ratio, 4),
                    })

            logger.info(
                "GT batch %d/%d: %d keywords, %d data points each",
                batch_idx + 1, len(batches), len(batch),
                len(df) if not df.empty else 0,
            )

        except Exception as e:
            logger.error("GT batch %d failed: %s", batch_idx, e)

        if sleep_sec > 0 and batch_idx < len(batches) - 1:
            logger.info("GT: sleeping %ds before next batch...", sleep_sec)
            time.sleep(sleep_sec)

    return dict(results)


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def _zscore(values: List[float], current: float) -> float:
    """Compute Z-score of current value against historical values."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance)
    if std < 1e-10:
        return 0.0
    return (current - mean) / std


def _get_monday(d: date) -> str:
    """Get the ISO date of the Monday of the week containing d."""
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def compute_attention_ranking(
    store: Any,
    week_start: Optional[str] = None,
    baseline_days: int = 90,
    weights: Optional[Dict[str, float]] = None,
    top_n: int = 30,
    keyword_ticker_map: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """Compute composite attention ranking from stored raw data.

    Reads reddit_mentions, news_mentions, google_trends from the store.
    Computes Z-scores per ticker per signal, weights them, ranks.

    Args:
        store: AttentionStore instance
        week_start: ISO date of the Monday to score. Defaults to current week.
        baseline_days: Number of days for Z-score baseline window.
        weights: {reddit, news, trends} weights. Defaults from settings.
        top_n: Max tickers to return.
        keyword_ticker_map: {keyword: [tickers]} for GT→ticker mapping.

    Returns:
        List of ranked dicts: [{ticker, composite_score, rank,
                                reddit_zscore, news_zscore, trends_zscore}]
    """
    from config.settings import ATTENTION_WEIGHTS

    weights = weights or ATTENTION_WEIGHTS

    if week_start is None:
        week_start = _get_monday(date.today())

    # Convert week_start to date for range calculation
    ws_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    baseline_start = (ws_date - timedelta(days=baseline_days)).isoformat()

    conn = store._get_conn()

    # --- Reddit Z-scores ---
    # Current week: sum of mentions from the 7 days starting at week_start
    ws_end = (ws_date + timedelta(days=6)).isoformat()

    reddit_current: Dict[str, float] = {}
    rows = conn.execute(
        """
        SELECT ticker, SUM(mention_count) as total
        FROM reddit_mentions
        WHERE scan_date >= ? AND scan_date <= ?
        GROUP BY ticker
        """,
        (week_start, ws_end),
    ).fetchall()
    for r in rows:
        reddit_current[r["ticker"]] = float(r["total"])

    # Reddit historical weekly averages per ticker
    reddit_history: Dict[str, List[float]] = defaultdict(list)
    hist_rows = conn.execute(
        """
        SELECT ticker,
               strftime('%Y-%W', scan_date) as yw,
               SUM(mention_count) as weekly_total
        FROM reddit_mentions
        WHERE scan_date >= ? AND scan_date < ?
        GROUP BY ticker, yw
        """,
        (baseline_start, week_start),
    ).fetchall()
    for r in hist_rows:
        reddit_history[r["ticker"]].append(float(r["weekly_total"]))

    # --- News Z-scores ---
    news_current: Dict[str, float] = {}
    rows = conn.execute(
        """
        SELECT ticker, SUM(article_count) as total
        FROM news_mentions
        WHERE scan_date >= ? AND scan_date <= ?
        GROUP BY ticker
        """,
        (week_start, ws_end),
    ).fetchall()
    for r in rows:
        news_current[r["ticker"]] = float(r["total"])

    news_history: Dict[str, List[float]] = defaultdict(list)
    hist_rows = conn.execute(
        """
        SELECT ticker,
               strftime('%Y-%W', scan_date) as yw,
               SUM(article_count) as weekly_total
        FROM news_mentions
        WHERE scan_date >= ? AND scan_date < ?
        GROUP BY ticker, yw
        """,
        (baseline_start, week_start),
    ).fetchall()
    for r in hist_rows:
        news_history[r["ticker"]].append(float(r["weekly_total"]))

    # --- GT Z-scores → ticker mapping ---
    trends_current_kw: Dict[str, float] = {}
    rows = conn.execute(
        """
        SELECT keyword, anchor_ratio
        FROM google_trends
        WHERE week_start = ?
        """,
        (week_start,),
    ).fetchall()
    for r in rows:
        trends_current_kw[r["keyword"]] = float(r["anchor_ratio"])

    trends_history_kw: Dict[str, List[float]] = defaultdict(list)
    hist_rows = conn.execute(
        """
        SELECT keyword, anchor_ratio
        FROM google_trends
        WHERE week_start >= ? AND week_start < ?
        ORDER BY week_start
        """,
        (baseline_start, week_start),
    ).fetchall()
    for r in hist_rows:
        trends_history_kw[r["keyword"]].append(float(r["anchor_ratio"]))

    # Build keyword→ticker mapping if not provided
    if keyword_ticker_map is None:
        keyword_ticker_map = {}
        kw_rows = store.get_keywords(active_only=True)
        for kw_row in kw_rows:
            keyword_ticker_map[kw_row["keyword"]] = kw_row["related_tickers"]

    # Map GT keyword Z-scores to tickers (take max across related keywords)
    trends_zscore_by_ticker: Dict[str, float] = defaultdict(float)
    for kw, tickers_list in keyword_ticker_map.items():
        if kw in trends_current_kw:
            kw_z = _zscore(
                trends_history_kw.get(kw, []),
                trends_current_kw[kw],
            )
            for t in tickers_list:
                trends_zscore_by_ticker[t] = max(trends_zscore_by_ticker[t], kw_z)

    # --- Merge all tickers ---
    all_tickers = set(reddit_current.keys()) | set(news_current.keys()) | set(trends_zscore_by_ticker.keys())

    rankings: List[Dict[str, Any]] = []
    for ticker in all_tickers:
        r_z = _zscore(
            reddit_history.get(ticker, []),
            reddit_current.get(ticker, 0.0),
        )
        n_z = _zscore(
            news_history.get(ticker, []),
            news_current.get(ticker, 0.0),
        )
        t_z = trends_zscore_by_ticker.get(ticker, 0.0)

        composite = (
            r_z * weights.get("reddit", 0.35)
            + n_z * weights.get("news", 0.35)
            + t_z * weights.get("trends", 0.30)
        )

        rankings.append({
            "ticker": ticker,
            "reddit_zscore": round(r_z, 3),
            "news_zscore": round(n_z, 3),
            "trends_zscore": round(t_z, 3),
            "composite_score": round(composite, 3),
        })

    # Sort and assign ranks
    rankings.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(rankings):
        r["rank"] = i + 1
        r["week_start"] = week_start

    return rankings[:top_n]
