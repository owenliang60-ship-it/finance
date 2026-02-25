"""
Unified Company Database — SQLite backend.

Single source of truth for company profiles, OPRMS ratings, analysis summaries,
and kill conditions. Lives at data/company.db.

Usage:
    from terminal.company_store import get_store
    store = get_store()
    store.upsert_company("AAPL", company_name="Apple Inc.", sector="Technology")
    store.save_oprms_rating("AAPL", dna="S", timing="A", timing_coeff=0.9, ...)
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "company.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    symbol TEXT PRIMARY KEY,
    company_name TEXT DEFAULT '',
    sector TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    exchange TEXT DEFAULT '',
    market_cap REAL,
    in_pool INTEGER DEFAULT 0,
    source TEXT DEFAULT '',
    first_seen TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS oprms_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL REFERENCES companies(symbol),
    dna TEXT NOT NULL,
    timing TEXT NOT NULL,
    timing_coeff REAL NOT NULL,
    conviction_modifier REAL,
    evidence TEXT,
    investment_bucket TEXT DEFAULT '',
    verdict TEXT DEFAULT '',
    position_pct REAL,
    is_current INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oprms_symbol ON oprms_ratings(symbol);
CREATE INDEX IF NOT EXISTS idx_oprms_current ON oprms_ratings(symbol, is_current);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL REFERENCES companies(symbol),
    analysis_date TEXT NOT NULL,
    depth TEXT DEFAULT 'deep',
    lens_quality_compounder TEXT,
    lens_imaginative_growth TEXT,
    lens_fundamental_long_short TEXT,
    lens_deep_value TEXT,
    lens_event_driven TEXT,
    debate_verdict TEXT,
    debate_summary TEXT,
    executive_summary TEXT,
    key_forces TEXT,
    red_team_summary TEXT,
    cycle_position TEXT,
    conviction_modifier REAL,
    asymmetric_bet_summary TEXT,
    oprms_dna TEXT,
    oprms_timing TEXT,
    oprms_timing_coeff REAL,
    oprms_position_pct REAL,
    price_at_analysis REAL,
    regime_at_analysis TEXT,
    research_dir TEXT,
    report_path TEXT,
    html_report_path TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyses_symbol ON analyses(symbol);

CREATE TABLE IF NOT EXISTS kill_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL REFERENCES companies(symbol),
    description TEXT NOT NULL,
    source_lens TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kill_symbol ON kill_conditions(symbol);

CREATE TABLE IF NOT EXISTS iv_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    iv_30d REAL,
    iv_60d REAL,
    hv_30d REAL,
    put_call_ratio REAL,
    total_volume INTEGER,
    total_oi INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_iv_daily_symbol ON iv_daily(symbol);
CREATE INDEX IF NOT EXISTS idx_iv_daily_date ON iv_daily(symbol, date);

CREATE TABLE IF NOT EXISTS options_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    expiration TEXT NOT NULL,
    strike REAL NOT NULL,
    side TEXT NOT NULL,
    bid REAL,
    ask REAL,
    mid REAL,
    last REAL,
    volume INTEGER,
    open_interest INTEGER,
    iv REAL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    dte INTEGER,
    in_the_money INTEGER,
    underlying_price REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_options_snap_symbol ON options_snapshots(symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_options_snap_exp ON options_snapshots(symbol, expiration);
"""


# ---------------------------------------------------------------------------
# CompanyStore class
# ---------------------------------------------------------------------------

class CompanyStore:
    """SQLite-backed company database."""

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
        self._migrate_if_needed()

    def _migrate_if_needed(self) -> None:
        """Add new columns to analyses table if missing (idempotent)."""
        conn = self._get_conn()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
        _ALLOWED_COLS = {"situation_summary", "debate_conviction_modifier", "debate_final_action", "debate_key_disagreement"}
        _ALLOWED_TYPES = {"TEXT", "REAL", "INTEGER"}
        new_cols = [
            ("situation_summary", "TEXT"),
            ("debate_conviction_modifier", "REAL"),
            ("debate_final_action", "TEXT"),
            ("debate_key_disagreement", "TEXT"),
        ]
        for col, typ in new_cols:
            if col not in _ALLOWED_COLS or typ not in _ALLOWED_TYPES:
                raise ValueError(f"Unexpected migration column: {col} {typ}")
            if col not in columns:
                conn.execute(f"ALTER TABLE analyses ADD COLUMN {col} {typ}")
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---- Companies ----

    def upsert_company(
        self,
        symbol: str,
        company_name: str = "",
        sector: str = "",
        industry: str = "",
        exchange: str = "",
        market_cap: Optional[float] = None,
        in_pool: bool = False,
        source: str = "",
    ) -> None:
        """Insert or update a company profile."""
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO companies (symbol, company_name, sector, industry,
                                   exchange, market_cap, in_pool, source,
                                   first_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                company_name = CASE WHEN excluded.company_name != '' THEN excluded.company_name ELSE companies.company_name END,
                sector = CASE WHEN excluded.sector != '' THEN excluded.sector ELSE companies.sector END,
                industry = CASE WHEN excluded.industry != '' THEN excluded.industry ELSE companies.industry END,
                exchange = CASE WHEN excluded.exchange != '' THEN excluded.exchange ELSE companies.exchange END,
                market_cap = COALESCE(excluded.market_cap, companies.market_cap),
                in_pool = MAX(companies.in_pool, excluded.in_pool),
                source = CASE WHEN excluded.source != '' THEN excluded.source ELSE companies.source END,
                updated_at = excluded.updated_at
            """,
            (symbol, company_name, sector, industry, exchange,
             market_cap, int(in_pool), source, now, now),
        )
        conn.commit()

    def get_company(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get a single company profile."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM companies WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        return dict(row) if row else None

    def list_companies(
        self,
        in_pool_only: bool = False,
        has_oprms_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List companies with optional filters."""
        conn = self._get_conn()
        query = "SELECT * FROM companies"
        conditions = []
        if in_pool_only:
            conditions.append("in_pool = 1")
        if has_oprms_only:
            conditions.append(
                "symbol IN (SELECT symbol FROM oprms_ratings WHERE is_current = 1)"
            )
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY symbol"
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    def sync_pool(self, pool_symbols: List[str]) -> int:
        """Sync stock pool: set in_pool=1 for given symbols, 0 for others.

        Returns number of companies updated.
        """
        conn = self._get_conn()
        pool_set = {s.upper() for s in pool_symbols}
        now = datetime.now().isoformat()

        with conn:
            # Reset all to out of pool
            conn.execute("UPDATE companies SET in_pool = 0")

            # Set pool members
            count = 0
            for sym in pool_set:
                result = conn.execute(
                    "UPDATE companies SET in_pool = 1, updated_at = ? WHERE symbol = ?",
                    (now, sym),
                )
                if result.rowcount > 0:
                    count += 1
                else:
                    # Company not in DB yet — insert minimal record
                    conn.execute(
                        "INSERT INTO companies (symbol, in_pool, source, first_seen, updated_at) "
                        "VALUES (?, 1, 'pool', ?, ?)",
                        (sym, now, now),
                    )
                    count += 1
        return count

    # ---- OPRMS Ratings ----

    def save_oprms_rating(
        self,
        symbol: str,
        dna: str,
        timing: str,
        timing_coeff: float,
        conviction_modifier: Optional[float] = None,
        evidence: Optional[List[str]] = None,
        investment_bucket: str = "",
        verdict: str = "",
        position_pct: Optional[float] = None,
    ) -> int:
        """Save a new OPRMS rating, marking it as current.

        Previous ratings for this symbol are marked is_current=0.
        Returns the new rating ID.
        """
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()

        with conn:
            # Mark previous as non-current
            conn.execute(
                "UPDATE oprms_ratings SET is_current = 0 WHERE symbol = ? AND is_current = 1",
                (symbol,),
            )

            cursor = conn.execute(
                """
                INSERT INTO oprms_ratings
                    (symbol, dna, timing, timing_coeff, conviction_modifier,
                     evidence, investment_bucket, verdict, position_pct,
                     is_current, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (symbol, dna, timing, timing_coeff, conviction_modifier,
                 json.dumps(evidence or [], ensure_ascii=False),
                 investment_bucket, verdict, position_pct, now),
            )
        logger.info(
            "Saved OPRMS for %s: DNA=%s Timing=%s Coeff=%.2f",
            symbol, dna, timing, timing_coeff,
        )
        return cursor.lastrowid

    def get_current_oprms(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get the current OPRMS rating for a symbol."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM oprms_ratings WHERE symbol = ? AND is_current = 1",
            (symbol.upper(),),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["evidence"] = json.loads(result["evidence"]) if result["evidence"] else []
        return result

    def get_oprms_history(self, symbol: str) -> List[Dict[str, Any]]:
        """Get all OPRMS ratings for a symbol, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM oprms_ratings WHERE symbol = ? ORDER BY created_at DESC",
            (symbol.upper(),),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["evidence"] = json.loads(d["evidence"]) if d["evidence"] else []
            results.append(d)
        return results

    # ---- Analyses ----

    def save_analysis(self, symbol: str, data: Dict[str, Any]) -> int:
        """Save a structured analysis summary.

        Args:
            symbol: Stock ticker
            data: Dict with analysis fields (see schema)

        Returns:
            New analysis row ID
        """
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()

        # Serialize key_forces as JSON if it's a list
        key_forces = data.get("key_forces")
        if isinstance(key_forces, list):
            key_forces = json.dumps(key_forces, ensure_ascii=False)

        cursor = conn.execute(
            """
            INSERT INTO analyses
                (symbol, analysis_date, depth,
                 lens_quality_compounder, lens_imaginative_growth,
                 lens_fundamental_long_short, lens_deep_value, lens_event_driven,
                 debate_verdict, debate_summary,
                 executive_summary, key_forces,
                 red_team_summary, cycle_position,
                 conviction_modifier, asymmetric_bet_summary,
                 oprms_dna, oprms_timing, oprms_timing_coeff, oprms_position_pct,
                 price_at_analysis, regime_at_analysis,
                 research_dir, report_path, html_report_path,
                 debate_conviction_modifier, debate_final_action,
                 debate_key_disagreement,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                data.get("analysis_date", now[:10]),
                data.get("depth", "deep"),
                data.get("lens_quality_compounder"),
                data.get("lens_imaginative_growth"),
                data.get("lens_fundamental_long_short"),
                data.get("lens_deep_value"),
                data.get("lens_event_driven"),
                data.get("debate_verdict"),
                data.get("debate_summary"),
                data.get("executive_summary"),
                key_forces,
                data.get("red_team_summary"),
                data.get("cycle_position"),
                data.get("conviction_modifier"),
                data.get("asymmetric_bet_summary"),
                data.get("oprms_dna"),
                data.get("oprms_timing"),
                data.get("oprms_timing_coeff"),
                data.get("oprms_position_pct"),
                data.get("price_at_analysis"),
                data.get("regime_at_analysis"),
                data.get("research_dir"),
                data.get("report_path"),
                data.get("html_report_path"),
                data.get("debate_conviction_modifier"),
                data.get("debate_final_action"),
                data.get("debate_key_disagreement"),
                now,
            ),
        )
        conn.commit()
        logger.info("Saved analysis for %s (id=%d)", symbol, cursor.lastrowid)
        return cursor.lastrowid

    def get_latest_analysis(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get the most recent analysis for a symbol."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM analyses WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("key_forces"):
            try:
                result["key_forces"] = json.loads(result["key_forces"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def get_analyses(self, symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get analysis history for a symbol, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM analyses WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get("key_forces"):
                try:
                    d["key_forces"] = json.loads(d["key_forces"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)
        return results

    def update_situation_summary(
        self,
        symbol: str,
        situation_json: str,
        analysis_id: Optional[int] = None,
    ) -> None:
        """Update situation_summary on an analysis row.

        Args:
            symbol: Stock ticker
            situation_json: JSON string of structured situation
            analysis_id: Specific analysis ID; if None, updates latest
        """
        symbol = symbol.upper()
        conn = self._get_conn()
        if analysis_id is not None:
            conn.execute(
                "UPDATE analyses SET situation_summary = ? WHERE id = ?",
                (situation_json, analysis_id),
            )
        else:
            # Update the latest analysis for this symbol
            row = conn.execute(
                "SELECT id FROM analyses WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE analyses SET situation_summary = ? WHERE id = ?",
                    (situation_json, row["id"]),
                )
        conn.commit()

    def get_analyses_with_memory(
        self,
        symbol: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Get analyses that have a situation_summary (memory-enabled).

        Args:
            symbol: Stock ticker
            limit: Max results

        Returns:
            List of analysis dicts with situation_summary, newest first
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM analyses WHERE symbol = ? "
            "AND situation_summary IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get("key_forces"):
                try:
                    d["key_forces"] = json.loads(d["key_forces"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)
        return results

    # ---- Kill Conditions ----

    def save_kill_conditions(
        self,
        symbol: str,
        conditions: List[Dict[str, str]],
    ) -> int:
        """Save kill conditions, replacing all existing active ones.

        Each condition: {description, source_lens}
        Returns number of conditions saved.
        """
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()

        with conn:
            # Deactivate existing
            conn.execute(
                "UPDATE kill_conditions SET is_active = 0 WHERE symbol = ? AND is_active = 1",
                (symbol,),
            )

            for cond in conditions:
                desc = cond.get("description", "")
                if not desc:
                    continue
                conn.execute(
                    """
                    INSERT INTO kill_conditions (symbol, description, source_lens, is_active, created_at)
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (symbol, cond["description"], cond.get("source_lens", ""), now),
                )
        return len(conditions)

    def get_kill_conditions(self, symbol: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get kill conditions for a symbol."""
        conn = self._get_conn()
        query = "SELECT * FROM kill_conditions WHERE symbol = ?"
        params: list = [symbol.upper()]
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ---- IV Daily ----

    def save_iv_daily(
        self,
        symbol: str,
        date: str,
        iv_30d: Optional[float] = None,
        iv_60d: Optional[float] = None,
        hv_30d: Optional[float] = None,
        put_call_ratio: Optional[float] = None,
        total_volume: Optional[int] = None,
        total_oi: Optional[int] = None,
    ) -> None:
        """Save daily IV summary for a symbol (upsert on symbol+date)."""
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO iv_daily
                (symbol, date, iv_30d, iv_60d, hv_30d,
                 put_call_ratio, total_volume, total_oi, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                iv_30d = excluded.iv_30d,
                iv_60d = excluded.iv_60d,
                hv_30d = excluded.hv_30d,
                put_call_ratio = excluded.put_call_ratio,
                total_volume = excluded.total_volume,
                total_oi = excluded.total_oi,
                created_at = excluded.created_at
            """,
            (symbol, date, iv_30d, iv_60d, hv_30d,
             put_call_ratio, total_volume, total_oi, now),
        )
        conn.commit()

    def get_iv_history(
        self, symbol: str, limit: int = 252
    ) -> List[Dict[str, Any]]:
        """Get IV history for a symbol, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM iv_daily WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_iv(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get the most recent IV daily record for a symbol."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM iv_daily WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return dict(row) if row else None

    # ---- Options Snapshots ----

    def save_options_snapshot(
        self,
        symbol: str,
        snapshot_date: str,
        contracts: List[Dict[str, Any]],
    ) -> int:
        """Save a batch of option contracts from a chain snapshot.

        Args:
            symbol: Underlying symbol
            snapshot_date: Date of the snapshot (YYYY-MM-DD)
            contracts: List of contract dicts with keys matching schema columns

        Returns:
            Number of contracts saved
        """
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()

        count = 0
        with conn:
            for c in contracts:
                conn.execute(
                    """
                    INSERT INTO options_snapshots
                        (symbol, snapshot_date, expiration, strike, side,
                         bid, ask, mid, last, volume, open_interest, iv,
                         delta, gamma, theta, vega, dte, in_the_money,
                         underlying_price, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol, snapshot_date,
                        c.get("expiration", ""),
                        c.get("strike", 0),
                        c.get("side", ""),
                        c.get("bid"),
                        c.get("ask"),
                        c.get("mid"),
                        c.get("last"),
                        c.get("volume"),
                        c.get("open_interest"),
                        c.get("iv"),
                        c.get("delta"),
                        c.get("gamma"),
                        c.get("theta"),
                        c.get("vega"),
                        c.get("dte"),
                        1 if c.get("in_the_money") else 0,
                        c.get("underlying_price"),
                        now,
                    ),
                )
                count += 1
        logger.info(
            "Saved %d option contracts for %s (%s)", count, symbol, snapshot_date
        )
        return count

    def get_options_snapshot(
        self,
        symbol: str,
        snapshot_date: Optional[str] = None,
        expiration: Optional[str] = None,
        side: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get option contracts from a snapshot.

        Args:
            symbol: Underlying symbol
            snapshot_date: Filter by snapshot date; if None, uses latest
            expiration: Filter by expiration date
            side: Filter by 'call' or 'put'

        Returns:
            List of contract dicts
        """
        conn = self._get_conn()
        symbol = symbol.upper()

        if snapshot_date is None:
            row = conn.execute(
                "SELECT MAX(snapshot_date) as d FROM options_snapshots WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            if not row or not row["d"]:
                return []
            snapshot_date = row["d"]

        query = "SELECT * FROM options_snapshots WHERE symbol = ? AND snapshot_date = ?"
        params: list = [symbol, snapshot_date]

        if expiration:
            query += " AND expiration = ?"
            params.append(expiration)
        if side:
            query += " AND side = ?"
            params.append(side)

        query += " ORDER BY expiration, strike, side"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def cleanup_old_snapshots(self, retain_days: int = 7) -> int:
        """Delete option snapshots older than retain_days.

        Returns:
            Number of rows deleted
        """
        conn = self._get_conn()
        cutoff = datetime.now()
        # Calculate cutoff date
        from datetime import timedelta
        cutoff_date = (cutoff - timedelta(days=retain_days)).strftime("%Y-%m-%d")

        cursor = conn.execute(
            "DELETE FROM options_snapshots WHERE snapshot_date < ?",
            (cutoff_date,),
        )
        conn.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info("Cleaned up %d old option snapshot rows (before %s)", deleted, cutoff_date)
        return deleted

    # ---- Aggregate Queries ----

    def get_dashboard_data(self) -> List[Dict[str, Any]]:
        """Get all companies with their current OPRMS + latest analysis for dashboard.

        Returns list of dicts with company + oprms + analysis fields merged.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT
                c.*,
                o.dna, o.timing, o.timing_coeff, o.conviction_modifier,
                o.investment_bucket, o.verdict AS oprms_verdict,
                o.position_pct, o.created_at AS oprms_date,
                a.analysis_date, a.debate_verdict,
                a.executive_summary, a.report_path, a.html_report_path
            FROM companies c
            LEFT JOIN oprms_ratings o ON c.symbol = o.symbol AND o.is_current = 1
            LEFT JOIN (
                SELECT symbol, MAX(created_at) AS max_created
                FROM analyses GROUP BY symbol
            ) latest_a ON c.symbol = latest_a.symbol
            LEFT JOIN analyses a ON a.symbol = latest_a.symbol AND a.created_at = latest_a.max_created
            ORDER BY c.symbol
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        in_pool = conn.execute("SELECT COUNT(*) FROM companies WHERE in_pool = 1").fetchone()[0]
        rated = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM oprms_ratings WHERE is_current = 1"
        ).fetchone()[0]
        analyzed = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM analyses"
        ).fetchone()[0]

        # DNA distribution
        dna_dist = {}
        rows = conn.execute(
            "SELECT dna, COUNT(*) as cnt FROM oprms_ratings WHERE is_current = 1 GROUP BY dna"
        ).fetchall()
        for row in rows:
            dna_dist[row["dna"]] = row["cnt"]

        return {
            "total_companies": total,
            "in_pool": in_pool,
            "rated": rated,
            "analyzed": analyzed,
            "dna_distribution": dna_dist,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[CompanyStore] = None


def get_store(db_path: Optional[Path] = None) -> CompanyStore:
    """Get or create the singleton CompanyStore instance."""
    global _store
    resolved = db_path or _DEFAULT_DB_PATH
    if _store is None or _store.db_path != resolved:
        _store = CompanyStore(db_path)
    return _store
