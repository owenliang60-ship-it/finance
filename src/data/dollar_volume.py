"""
Dollar Volume 核心模块
- 独立数据库 (不受周六重建影响)
- 存储每日 Top 200 排名
- 检测"新面孔"动量信号
"""
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import DOLLAR_VOLUME_DB, DOLLAR_VOLUME_TOP_N, DOLLAR_VOLUME_LOOKBACK

logger = logging.getLogger(__name__)

# ============================================================
# 数据库初始化
# ============================================================

def get_connection(db_path: Path = DOLLAR_VOLUME_DB) -> sqlite3.Connection:
    """获取数据库连接"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Path = DOLLAR_VOLUME_DB):
    """创建表（如不存在）"""
    conn = get_connection(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_rankings (
                date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                company_name TEXT,
                price REAL,
                volume INTEGER,
                dollar_volume REAL NOT NULL,
                market_cap REAL,
                sector TEXT,
                UNIQUE(date, rank)
            );
            CREATE INDEX IF NOT EXISTS idx_dv_date ON daily_rankings(date);
            CREATE INDEX IF NOT EXISTS idx_dv_symbol ON daily_rankings(symbol);

            CREATE TABLE IF NOT EXISTS backfill_progress (
                symbol TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS collection_log (
                date TEXT PRIMARY KEY,
                total_scanned INTEGER,
                stored INTEGER,
                api_calls INTEGER,
                elapsed REAL,
                status TEXT
            );
        """)
        conn.commit()
        logger.info(f"Dollar Volume DB initialized: {db_path}")
    finally:
        conn.close()


# ============================================================
# 存储与查询
# ============================================================

def store_daily_rankings(date: str, rankings: List[Dict],
                         db_path: Path = DOLLAR_VOLUME_DB):
    """存储某天的 Top N 排名"""
    conn = get_connection(db_path)
    try:
        # 先清除该日旧数据（支持重跑）
        conn.execute("DELETE FROM daily_rankings WHERE date = ?", (date,))

        for item in rankings:
            conn.execute("""
                INSERT INTO daily_rankings
                    (date, rank, symbol, company_name, price, volume,
                     dollar_volume, market_cap, sector)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date,
                item["rank"],
                item["symbol"],
                item.get("company_name", ""),
                item.get("price"),
                item.get("volume"),
                item["dollar_volume"],
                item.get("market_cap"),
                item.get("sector", ""),
            ))

        conn.commit()
        logger.info(f"Stored {len(rankings)} rankings for {date}")
    finally:
        conn.close()


def get_rankings(date: str, limit: int = 50,
                 db_path: Path = DOLLAR_VOLUME_DB) -> List[Dict]:
    """查询某天的排名"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT * FROM daily_rankings
            WHERE date = ? AND rank <= ?
            ORDER BY rank
        """, (date, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_date(db_path: Path = DOLLAR_VOLUME_DB) -> Optional[str]:
    """获取最近有数据的日期"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(date) as latest FROM daily_rankings"
        ).fetchone()
        return row["latest"] if row else None
    finally:
        conn.close()


def get_all_dates(db_path: Path = DOLLAR_VOLUME_DB) -> List[str]:
    """获取所有有数据的日期"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_rankings ORDER BY date DESC"
        ).fetchall()
        return [r["date"] for r in rows]
    finally:
        conn.close()


# ============================================================
# 新面孔检测
# ============================================================

def detect_new_faces(date: str, lookback: int = DOLLAR_VOLUME_LOOKBACK,
                     top_n: int = 50,
                     db_path: Path = DOLLAR_VOLUME_DB) -> List[Dict]:
    """
    检测今天 Top N 中过去 lookback 个交易日未出现过的"新面孔"
    返回新面孔的完整排名信息
    """
    conn = get_connection(db_path)
    try:
        # 获取最近 lookback 个有数据的交易日（不含今天）
        past_dates = conn.execute("""
            SELECT DISTINCT date FROM daily_rankings
            WHERE date < ?
            ORDER BY date DESC
            LIMIT ?
        """, (date, lookback)).fetchall()
        past_dates = [r["date"] for r in past_dates]

        if not past_dates:
            # 没有历史数据，无法判断新面孔
            return []

        # 今天的 Top N symbols
        today_rows = conn.execute("""
            SELECT * FROM daily_rankings
            WHERE date = ? AND rank <= ?
            ORDER BY rank
        """, (date, top_n)).fetchall()

        if not today_rows:
            return []

        # 过去在 Top N 中出现过的 symbols
        placeholders = ",".join("?" * len(past_dates))
        past_symbols = conn.execute(f"""
            SELECT DISTINCT symbol FROM daily_rankings
            WHERE date IN ({placeholders}) AND rank <= ?
        """, past_dates + [top_n]).fetchall()
        past_set = {r["symbol"] for r in past_symbols}

        # 新面孔 = 今天有但过去没有的
        new_faces = [dict(r) for r in today_rows if r["symbol"] not in past_set]

        logger.info(
            f"New faces on {date}: {len(new_faces)} "
            f"(checked against {len(past_dates)} trading days)"
        )
        return new_faces
    finally:
        conn.close()


# ============================================================
# 采集日志
# ============================================================

def log_collection(date: str, stats: Dict,
                   db_path: Path = DOLLAR_VOLUME_DB):
    """记录采集日志"""
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO collection_log
                (date, total_scanned, stored, api_calls, elapsed, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            date,
            stats.get("total_scanned", 0),
            stats.get("stored", 0),
            stats.get("api_calls", 0),
            stats.get("elapsed", 0),
            stats.get("status", "ok"),
        ))
        conn.commit()
    finally:
        conn.close()


def get_collection_log(limit: int = 10,
                       db_path: Path = DOLLAR_VOLUME_DB) -> List[Dict]:
    """查询最近采集日志"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT * FROM collection_log
            ORDER BY date DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def is_collected(date: str, db_path: Path = DOLLAR_VOLUME_DB) -> bool:
    """检查某天是否已采集"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM daily_rankings WHERE date = ?",
            (date,)
        ).fetchone()
        return row["cnt"] > 0
    finally:
        conn.close()


def rank_change_label(today_rank: int, prev_rank: "int | None") -> str:
    """今日 vs 昨日排名变化标签。prev_rank=None → NEW（昨日不在 top-N）。"""
    if prev_rank is None:
        return "NEW"
    delta = prev_rank - today_rank  # 昨日名次大、今日名次小 = 上升
    if delta > 0:
        return "↑{}".format(delta)
    if delta < 0:
        return "↓{}".format(-delta)
    return "="


def get_previous_day_ranks(date: str, db_path: Path = DOLLAR_VOLUME_DB) -> "Dict[str, int]":
    """< date 的最近一个交易日的 {symbol: rank}。无历史 → {}。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM daily_rankings WHERE date < ?", (date,)
        ).fetchone()
        if not row or not row["d"]:
            return {}
        rows = conn.execute(
            "SELECT symbol, rank FROM daily_rankings WHERE date = ?", (row["d"],)
        ).fetchall()
        return {r["symbol"]: r["rank"] for r in rows}
    finally:
        conn.close()


def annotate_rank_changes(rankings: "List[Dict]", prev_ranks: "Dict[str, int]") -> None:
    """原地注入 rank_change_label。"""
    for item in rankings:
        item["rank_change_label"] = rank_change_label(
            item["rank"], prev_ranks.get(item.get("symbol", ""))
        )
