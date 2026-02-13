"""
Attention Engine — SQLite backend.

Stores Google Trends, Reddit mentions, news mentions, theme keywords,
and composite attention snapshots. Lives at data/attention/attention.db.

Usage:
    from terminal.attention_store import get_attention_store
    store = get_attention_store()
    store.save_reddit_mention("NVDA", "2026-02-13", "wallstreetbets", 42, 200)
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "attention" / "attention.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS theme_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    related_tickers TEXT,
    category TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS google_trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    week_start TEXT NOT NULL,
    interest_score INTEGER,
    anchor_ratio REAL,
    collected_at TEXT NOT NULL,
    UNIQUE(keyword, week_start)
);

CREATE INDEX IF NOT EXISTS idx_gt_keyword ON google_trends(keyword);

CREATE TABLE IF NOT EXISTS reddit_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    mention_count INTEGER DEFAULT 0,
    sample_posts INTEGER,
    collected_at TEXT NOT NULL,
    UNIQUE(ticker, scan_date, subreddit)
);

CREATE INDEX IF NOT EXISTS idx_reddit_ticker ON reddit_mentions(ticker);
CREATE INDEX IF NOT EXISTS idx_reddit_date ON reddit_mentions(scan_date);

CREATE TABLE IF NOT EXISTS news_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    article_count INTEGER DEFAULT 0,
    avg_sentiment REAL,
    source TEXT DEFAULT 'finnhub',
    collected_at TEXT NOT NULL,
    UNIQUE(ticker, scan_date, source)
);

CREATE INDEX IF NOT EXISTS idx_news_ticker ON news_mentions(ticker);
CREATE INDEX IF NOT EXISTS idx_news_date ON news_mentions(scan_date);

CREATE TABLE IF NOT EXISTS attention_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    week_start TEXT NOT NULL,
    reddit_zscore REAL,
    news_zscore REAL,
    trends_zscore REAL,
    composite_score REAL,
    rank INTEGER,
    collected_at TEXT NOT NULL,
    UNIQUE(ticker, week_start)
);

CREATE INDEX IF NOT EXISTS idx_snap_ticker ON attention_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_snap_week ON attention_snapshots(week_start);
"""


# ---------------------------------------------------------------------------
# AttentionStore class
# ---------------------------------------------------------------------------

class AttentionStore:
    """SQLite-backed attention data store."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---- Theme Keywords ----

    def save_keyword(
        self,
        keyword: str,
        source: str = "manual",
        tickers: Optional[List[str]] = None,
        category: str = "",
    ) -> None:
        """Insert or update a theme keyword."""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO theme_keywords (keyword, source, related_tickers, category,
                                        is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                source = excluded.source,
                related_tickers = excluded.related_tickers,
                category = excluded.category,
                updated_at = excluded.updated_at
            """,
            (keyword, source, json.dumps(tickers or [], ensure_ascii=False),
             category, now, now),
        )
        conn.commit()

    def get_keywords(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all theme keywords."""
        conn = self._get_conn()
        query = "SELECT * FROM theme_keywords"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY category, keyword"
        rows = conn.execute(query).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["related_tickers"] = json.loads(d["related_tickers"]) if d["related_tickers"] else []
            results.append(d)
        return results

    def deactivate_keyword(self, keyword: str) -> bool:
        """Deactivate a keyword. Returns True if found."""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        result = conn.execute(
            "UPDATE theme_keywords SET is_active = 0, updated_at = ? WHERE keyword = ?",
            (now, keyword),
        )
        conn.commit()
        return result.rowcount > 0

    def seed_keywords(self, seed_config: Dict[str, Dict]) -> int:
        """Bulk-insert seed keywords from config. Returns count inserted."""
        count = 0
        for category, info in seed_config.items():
            tickers = info.get("tickers", [])
            for kw in info.get("keywords", []):
                self.save_keyword(kw, source="manual", tickers=tickers, category=category)
                count += 1
        return count

    # ---- Google Trends ----

    def save_trends_data(
        self,
        keyword: str,
        week_start: str,
        interest_score: int,
        anchor_ratio: float,
    ) -> None:
        """Save a single GT data point (upsert)."""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO google_trends (keyword, week_start, interest_score, anchor_ratio, collected_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(keyword, week_start) DO UPDATE SET
                interest_score = excluded.interest_score,
                anchor_ratio = excluded.anchor_ratio,
                collected_at = excluded.collected_at
            """,
            (keyword, week_start, interest_score, anchor_ratio, now),
        )
        conn.commit()

    def save_trends_batch(self, records: List[Dict[str, Any]]) -> int:
        """Save multiple GT data points in a single transaction.

        Each record: {keyword, week_start, interest_score, anchor_ratio}
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        count = 0
        for rec in records:
            conn.execute(
                """
                INSERT INTO google_trends (keyword, week_start, interest_score, anchor_ratio, collected_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(keyword, week_start) DO UPDATE SET
                    interest_score = excluded.interest_score,
                    anchor_ratio = excluded.anchor_ratio,
                    collected_at = excluded.collected_at
                """,
                (rec["keyword"], rec["week_start"], rec["interest_score"],
                 rec["anchor_ratio"], now),
            )
            count += 1
        conn.commit()
        return count

    def get_trends_history(
        self, keyword: str, weeks: int = 26
    ) -> List[Dict[str, Any]]:
        """Get GT history for a keyword, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM google_trends
            WHERE keyword = ?
            ORDER BY week_start DESC
            LIMIT ?
            """,
            (keyword, weeks),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Reddit Mentions ----

    def save_reddit_mention(
        self,
        ticker: str,
        scan_date: str,
        subreddit: str,
        mention_count: int,
        sample_posts: int,
    ) -> None:
        """Save a Reddit mention count (upsert)."""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO reddit_mentions (ticker, scan_date, subreddit, mention_count, sample_posts, collected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, scan_date, subreddit) DO UPDATE SET
                mention_count = excluded.mention_count,
                sample_posts = excluded.sample_posts,
                collected_at = excluded.collected_at
            """,
            (ticker.upper(), scan_date, subreddit, mention_count, sample_posts, now),
        )
        conn.commit()

    def save_reddit_batch(self, records: List[Dict[str, Any]]) -> int:
        """Save multiple Reddit mentions in one transaction.

        Each record: {ticker, scan_date, subreddit, mention_count, sample_posts}
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        count = 0
        for rec in records:
            conn.execute(
                """
                INSERT INTO reddit_mentions (ticker, scan_date, subreddit, mention_count, sample_posts, collected_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, scan_date, subreddit) DO UPDATE SET
                    mention_count = excluded.mention_count,
                    sample_posts = excluded.sample_posts,
                    collected_at = excluded.collected_at
                """,
                (rec["ticker"].upper(), rec["scan_date"], rec["subreddit"],
                 rec["mention_count"], rec["sample_posts"], now),
            )
            count += 1
        conn.commit()
        return count

    def get_reddit_history(
        self, ticker: str, days: int = 90
    ) -> List[Dict[str, Any]]:
        """Get Reddit mention history for a ticker, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT scan_date, SUM(mention_count) as total_mentions,
                   SUM(sample_posts) as total_posts
            FROM reddit_mentions
            WHERE ticker = ?
            GROUP BY scan_date
            ORDER BY scan_date DESC
            LIMIT ?
            """,
            (ticker.upper(), days),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_reddit_daily_totals(self, scan_date: str) -> List[Dict[str, Any]]:
        """Get all ticker totals for a specific date."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT ticker, SUM(mention_count) as total_mentions
            FROM reddit_mentions
            WHERE scan_date = ?
            GROUP BY ticker
            ORDER BY total_mentions DESC
            """,
            (scan_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- News Mentions ----

    def save_news_mention(
        self,
        ticker: str,
        scan_date: str,
        article_count: int,
        avg_sentiment: Optional[float] = None,
        source: str = "finnhub",
    ) -> None:
        """Save a news mention count (upsert)."""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO news_mentions (ticker, scan_date, article_count, avg_sentiment, source, collected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, scan_date, source) DO UPDATE SET
                article_count = excluded.article_count,
                avg_sentiment = excluded.avg_sentiment,
                collected_at = excluded.collected_at
            """,
            (ticker.upper(), scan_date, article_count, avg_sentiment, source, now),
        )
        conn.commit()

    def save_news_batch(self, records: List[Dict[str, Any]]) -> int:
        """Save multiple news mentions in one transaction.

        Each record: {ticker, scan_date, article_count, avg_sentiment, source}
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        count = 0
        for rec in records:
            conn.execute(
                """
                INSERT INTO news_mentions (ticker, scan_date, article_count, avg_sentiment, source, collected_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, scan_date, source) DO UPDATE SET
                    article_count = excluded.article_count,
                    avg_sentiment = excluded.avg_sentiment,
                    collected_at = excluded.collected_at
                """,
                (rec["ticker"].upper(), rec["scan_date"], rec["article_count"],
                 rec.get("avg_sentiment"), rec.get("source", "finnhub"), now),
            )
            count += 1
        conn.commit()
        return count

    def get_news_history(
        self, ticker: str, days: int = 90
    ) -> List[Dict[str, Any]]:
        """Get news mention history for a ticker, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM news_mentions
            WHERE ticker = ?
            ORDER BY scan_date DESC
            LIMIT ?
            """,
            (ticker.upper(), days),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_news_daily_totals(self, scan_date: str) -> List[Dict[str, Any]]:
        """Get all ticker totals for a specific date."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT ticker, SUM(article_count) as total_articles,
                   AVG(avg_sentiment) as avg_sentiment
            FROM news_mentions
            WHERE scan_date = ?
            GROUP BY ticker
            ORDER BY total_articles DESC
            """,
            (scan_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Attention Snapshots ----

    def save_attention_snapshot(
        self,
        ticker: str,
        week_start: str,
        scores: Dict[str, float],
    ) -> None:
        """Save a composite attention snapshot (upsert).

        scores: {reddit_zscore, news_zscore, trends_zscore, composite_score, rank}
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO attention_snapshots
                (ticker, week_start, reddit_zscore, news_zscore, trends_zscore,
                 composite_score, rank, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, week_start) DO UPDATE SET
                reddit_zscore = excluded.reddit_zscore,
                news_zscore = excluded.news_zscore,
                trends_zscore = excluded.trends_zscore,
                composite_score = excluded.composite_score,
                rank = excluded.rank,
                collected_at = excluded.collected_at
            """,
            (ticker.upper(), week_start,
             scores.get("reddit_zscore", 0.0),
             scores.get("news_zscore", 0.0),
             scores.get("trends_zscore", 0.0),
             scores.get("composite_score", 0.0),
             scores.get("rank"),
             now),
        )
        conn.commit()

    def save_snapshots_batch(self, records: List[Dict[str, Any]]) -> int:
        """Save multiple snapshots in one transaction.

        Each record: {ticker, week_start, reddit_zscore, news_zscore,
                      trends_zscore, composite_score, rank}
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        count = 0
        for rec in records:
            conn.execute(
                """
                INSERT INTO attention_snapshots
                    (ticker, week_start, reddit_zscore, news_zscore, trends_zscore,
                     composite_score, rank, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, week_start) DO UPDATE SET
                    reddit_zscore = excluded.reddit_zscore,
                    news_zscore = excluded.news_zscore,
                    trends_zscore = excluded.trends_zscore,
                    composite_score = excluded.composite_score,
                    rank = excluded.rank,
                    collected_at = excluded.collected_at
                """,
                (rec["ticker"].upper(), rec["week_start"],
                 rec.get("reddit_zscore", 0.0),
                 rec.get("news_zscore", 0.0),
                 rec.get("trends_zscore", 0.0),
                 rec.get("composite_score", 0.0),
                 rec.get("rank"),
                 now),
            )
            count += 1
        conn.commit()
        return count

    def get_weekly_ranking(
        self, week_start: str, top_n: int = 20
    ) -> List[Dict[str, Any]]:
        """Get the attention ranking for a given week."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM attention_snapshots
            WHERE week_start = ?
            ORDER BY composite_score DESC
            LIMIT ?
            """,
            (week_start, top_n),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_ticker_history(
        self, ticker: str, weeks: int = 26
    ) -> List[Dict[str, Any]]:
        """Get attention snapshot history for a ticker."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM attention_snapshots
            WHERE ticker = ?
            ORDER BY week_start DESC
            LIMIT ?
            """,
            (ticker.upper(), weeks),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_weeks(self) -> List[str]:
        """Get all distinct week_start values, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT week_start FROM attention_snapshots ORDER BY week_start DESC"
        ).fetchall()
        return [r["week_start"] for r in rows]

    def get_new_discoveries(self, week_start: str) -> List[str]:
        """Find tickers that appear for the first time in a given week."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT ticker FROM attention_snapshots
            WHERE week_start = ?
            AND ticker NOT IN (
                SELECT DISTINCT ticker FROM attention_snapshots
                WHERE week_start < ?
            )
            ORDER BY composite_score DESC
            """,
            (week_start, week_start),
        ).fetchall()
        return [r["ticker"] for r in rows]

    # ---- Stats ----

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        conn = self._get_conn()
        keywords = conn.execute(
            "SELECT COUNT(*) FROM theme_keywords WHERE is_active = 1"
        ).fetchone()[0]
        gt_points = conn.execute("SELECT COUNT(*) FROM google_trends").fetchone()[0]
        reddit_days = conn.execute(
            "SELECT COUNT(DISTINCT scan_date) FROM reddit_mentions"
        ).fetchone()[0]
        news_days = conn.execute(
            "SELECT COUNT(DISTINCT scan_date) FROM news_mentions"
        ).fetchone()[0]
        snapshots = conn.execute(
            "SELECT COUNT(DISTINCT week_start) FROM attention_snapshots"
        ).fetchone()[0]
        tickers_tracked = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM attention_snapshots"
        ).fetchone()[0]
        return {
            "active_keywords": keywords,
            "gt_data_points": gt_points,
            "reddit_scan_days": reddit_days,
            "news_scan_days": news_days,
            "snapshot_weeks": snapshots,
            "tickers_tracked": tickers_tracked,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[AttentionStore] = None


def get_attention_store(db_path: Optional[Path] = None) -> AttentionStore:
    """Get or create the singleton AttentionStore instance."""
    global _store
    if _store is None or (db_path and _store.db_path != db_path):
        _store = AttentionStore(db_path)
    return _store
