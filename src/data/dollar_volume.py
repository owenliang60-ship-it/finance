"""
Dollar Volume 核心模块
- 独立数据库 (不受周六重建影响)
- 存储每日 Top 200 排名
- 检测"新面孔"动量信号
"""
import sqlite3
import logging
import math
import json
import hashlib
from statistics import median
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import DOLLAR_VOLUME_DB, DOLLAR_VOLUME_TOP_N, DOLLAR_VOLUME_LOOKBACK

logger = logging.getLogger(__name__)

MIN_SCANNED = 800
BASELINE_RATIO = 0.8


def snapshot_digest(rows, scanned):
    values = [(r["rank"], r["symbol"], float(r["price"]), float(r["volume"]),
               float(r["dollar_volume"])) for r in rows]
    return hashlib.sha256(json.dumps([scanned, values], separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def leader_coverage_error(symbols, reference):
    leaders = set(reference.get("symbols", [])) if reference else set()
    if leaders and len(leaders & set(symbols)) < math.ceil(len(leaders)*BASELINE_RATIO):
        return "上一完整采集日Top20中超过20%的证券缺少有效量价"
    return ""


def latest_quality_reference(date, db_path=DOLLAR_VOLUME_DB):
    """Quality anchor may cross failed dates; daily rank comparisons must not.

    Legacy complete snapshots are transition anchors only, not verified caches.
    A snapshot with evidence must pass its evidence checks to become an anchor.
    """
    for day in get_all_dates(db_path):
        if day < date and not snapshot_integrity_error(day, db_path, require_evidence=False):
            rows = get_rankings(day, DOLLAR_VOLUME_TOP_N, db_path)
            return {"date": day, "symbols": [r["symbol"] for r in rows[:20]],
                    "mode": ("legacy_transition" if snapshot_integrity_error(day, db_path)
                             else "verified"),
                    "digest": snapshot_digest(rows, None)}
    return None


def make_quality_evidence(rows, scanned, valid_symbols, reference):
    return {"version": 1, "digest": snapshot_digest(rows, scanned),
            "valid_symbols": sorted(set(valid_symbols)), "reference": reference}


def rankings_integrity_error(rows: List[Dict]) -> str:
    """Validate a full stored snapshot, never just the displayed Top50."""
    if len(rows) != DOLLAR_VOLUME_TOP_N:
        return f"排名不足或超量：{len(rows)}/{DOLLAR_VOLUME_TOP_N}"
    seen = set()
    previous = math.inf
    for rank, row in enumerate(rows, 1):
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip() or symbol.upper() in seen:
            return "证券代码缺失或重复"
        seen.add(symbol.upper())
        if row.get("rank") != rank:
            return "排名不连续"
        for field in ("price", "volume", "dollar_volume"):
            value = row.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                return "价格、成交量或成交额无效"
        if row["dollar_volume"] > previous:
            return "成交额排序错误"
        previous = row["dollar_volume"]
    return ""


def collection_minimum(date: str, db_path: Path = DOLLAR_VOLUME_DB) -> int:
    """Use prior complete successful samples only; no future or failed anchors."""
    conn = get_connection(db_path)
    try:
        logs = conn.execute(
            "SELECT date,total_scanned FROM collection_log WHERE date < ? "
            "AND status='ok' AND stored=? AND total_scanned>=? ORDER BY date DESC",
            (date, DOLLAR_VOLUME_TOP_N, MIN_SCANNED),
        ).fetchall()
        counts = []
        for log in logs:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM daily_rankings WHERE date=? ORDER BY rank", (log["date"],))]
            if not rankings_integrity_error(rows):
                counts.append(log["total_scanned"])
                if len(counts) == 20:
                    break
        return max(MIN_SCANNED, math.ceil(median(counts)*BASELINE_RATIO)) if counts else MIN_SCANNED
    finally:
        conn.close()


def snapshot_integrity_error(date: str, db_path: Path = DOLLAR_VOLUME_DB,
                             require_evidence: bool = True) -> str:
    conn = get_connection(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_rankings WHERE date=? ORDER BY rank", (date,))]
        error = rankings_integrity_error(rows)
        if error:
            return error
        log = conn.execute("SELECT * FROM collection_log WHERE date=?", (date,)).fetchone()
        if not log or log["status"] != "ok" or log["stored"] != DOLLAR_VOLUME_TOP_N:
            return "缺少完整采集记录"
        minimum = collection_minimum(date, db_path)
        if not isinstance(log["total_scanned"], (int, float)) or log["total_scanned"] < minimum:
            return f"候选数量未达完整性门槛 {minimum}"
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                 "AND name='quality_evidence'").fetchone()
        evidence = conn.execute("SELECT evidence_json FROM quality_evidence WHERE date=?",
                                (date,)).fetchone() if has_table else None
        if not evidence:
            return "缺少完整验收证据（旧快照）" if require_evidence else ""
        try:
            proof = json.loads(evidence[0])
            if proof["version"] != 1 or proof["digest"] != snapshot_digest(rows, log["total_scanned"]):
                return "验收证据版本或快照指纹不匹配"
            symbols = proof["valid_symbols"]
            if (not isinstance(symbols, list) or not all(isinstance(s, str) and s for s in symbols)
                    or len(set(symbols)) != len(symbols)
                    or len(symbols) > log["total_scanned"]
                    or not {r["symbol"] for r in rows}.issubset(symbols)):
                return "验收候选证据不完整"
            reference = proof["reference"]
            if reference is not None:
                if not isinstance(reference["date"], str) or reference["date"] >= date:
                    return "验收参照日期无效"
                anchor_rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM daily_rankings WHERE date=? ORDER BY rank", (reference["date"],))]
                if (rankings_integrity_error(anchor_rows)
                        or reference["digest"] != snapshot_digest(anchor_rows, None)
                        or reference["symbols"] != [r["symbol"] for r in anchor_rows[:20]]):
                    return "验收参照已变化或损坏"
            elif conn.execute("SELECT 1 FROM daily_rankings WHERE date < ? LIMIT 1", (date,)).fetchone():
                return "已有历史但验收缺少可信参照"
            error = leader_coverage_error(symbols, reference)
            if error:
                return error
        except (KeyError, TypeError, ValueError):
            return "验收证据无法解析"
        return ""
    finally:
        conn.close()


def get_history_warning(date: str, lookback: int = DOLLAR_VOLUME_LOOKBACK,
                        db_path: Path = DOLLAR_VOLUME_DB) -> str:
    conn = get_connection(db_path)
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT date FROM (SELECT date FROM daily_rankings UNION SELECT date FROM collection_log) "
            "WHERE date < ? ORDER BY date DESC LIMIT ?", (date, lookback))]
    finally:
        conn.close()
    if len(dates) < lookback or any(snapshot_integrity_error(d, db_path) for d in dates):
        return f"历史窗口不足{lookback}个完整采集日或含异常数据，暂停新面孔判断"
    return ""

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
            CREATE TABLE IF NOT EXISTS quality_evidence (
                date TEXT PRIMARY KEY,
                evidence_json TEXT NOT NULL
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
                         db_path: Path = DOLLAR_VOLUME_DB, *, stats=None, evidence=None):
    """存储某天的 Top N 排名"""
    conn = get_connection(db_path)
    try:
        # 先清除该日旧数据（支持重跑）
        conn.execute("DELETE FROM daily_rankings WHERE date = ?", (date,))
        if evidence is None:
            # A legacy writer must not turn a previously verified row into an
            # apparently pre-migration transition anchor by deleting its proof.
            conn.execute("UPDATE quality_evidence SET evidence_json='{}' WHERE date=?", (date,))
        else:
            conn.execute("DELETE FROM quality_evidence WHERE date = ?", (date,))

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

        if stats is not None:
            _write_collection_log(conn, date, stats)
        if evidence is not None:
            conn.execute("INSERT INTO quality_evidence VALUES (?,?)",
                         (date, json.dumps(evidence, allow_nan=False)))
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
    if snapshot_integrity_error(date, db_path) or get_history_warning(date, lookback, db_path):
        return []
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

def _write_collection_log(conn, date, stats):
    conn.execute("""
        INSERT OR REPLACE INTO collection_log
            (date, total_scanned, stored, api_calls, elapsed, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date, stats.get("total_scanned", 0), stats.get("stored", 0),
          stats.get("api_calls", 0), stats.get("elapsed", 0), stats.get("status", "ok")))


def log_collection(date: str, stats: Dict,
                   db_path: Path = DOLLAR_VOLUME_DB):
    """记录采集日志"""
    conn = get_connection(db_path)
    try:
        _write_collection_log(conn, date, stats)
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


def get_previous_day_ranks(date: str, db_path: Path = DOLLAR_VOLUME_DB) -> "Dict[str, int] | None":
    """最近一次采集的排名；失败或缺失返回None，不能跳过失败日。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM (SELECT date FROM daily_rankings "
            "UNION SELECT date FROM collection_log) WHERE date < ?", (date,)
        ).fetchone()
        if not row or not row["d"] or snapshot_integrity_error(row["d"], db_path):
            return None
        rows = conn.execute(
            "SELECT symbol, rank FROM daily_rankings WHERE date = ?", (row["d"],)
        ).fetchall()
        return {r["symbol"]: r["rank"] for r in rows}
    finally:
        conn.close()


def annotate_rank_changes(rankings: "List[Dict]", prev_ranks: "Dict[str, int] | None") -> None:
    """原地注入 rank_change_label。"""
    for item in rankings:
        item["rank_change_label"] = "—" if prev_ranks is None else rank_change_label(
            item["rank"], prev_ranks.get(item.get("symbol", ""))
        )
