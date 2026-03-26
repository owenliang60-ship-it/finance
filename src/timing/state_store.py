"""SQLite-backed persistence for the dual-engine timing state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.path_utils import resolve_shared_repo_root
from src.timing.dual_engine import DualEngineEvaluation, DualEngineState


_ROOT = resolve_shared_repo_root(Path(__file__).resolve().parent.parent.parent)
DEFAULT_STATE_DB_PATH = _ROOT / "data" / "crypto" / "btc_timing.db"


class DualEngineStateStore:
    """Persist a small state vector across 4H runs."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path or DEFAULT_STATE_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS engine_state (
                    system_name TEXT PRIMARY KEY,
                    risk_mode TEXT NOT NULL,
                    risk_active INTEGER NOT NULL,
                    escape_price REAL,
                    k REAL NOT NULL,
                    risk_breakout_streak INTEGER NOT NULL,
                    left_latch_active INTEGER NOT NULL,
                    left_latch_position REAL NOT NULL,
                    left_trigger_price REAL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS engine_history (
                    system_name TEXT NOT NULL,
                    engine_timestamp TEXT NOT NULL,
                    target_position_pct REAL NOT NULL,
                    right_raw_position_pct REAL NOT NULL,
                    right_risked_position_pct REAL NOT NULL,
                    left_position_pct REAL NOT NULL,
                    k REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    risk_mode TEXT NOT NULL,
                    risk_active INTEGER NOT NULL,
                    escape_price REAL,
                    left_latch_active INTEGER NOT NULL,
                    left_latch_position REAL NOT NULL,
                    left_trigger_price REAL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (system_name, engine_timestamp)
                )
                """
            )
            self._migrate_legacy_tables(conn)
            conn.commit()

    def _migrate_legacy_tables(self, conn: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "dual_engine_state" not in tables:
            return

        conn.execute(
            """
            INSERT OR IGNORE INTO engine_state (
                system_name, risk_mode, risk_active, escape_price, k,
                risk_breakout_streak, left_latch_active, left_latch_position,
                left_trigger_price, updated_at
            )
            SELECT system_name, risk_mode, risk_active, escape_price, k,
                   risk_breakout_streak, left_latch_active, left_latch_position,
                   left_trigger_price, updated_at
            FROM dual_engine_state
            """
        )

    def load(self, system_name: str = "btc_dual_engine") -> DualEngineState:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT risk_mode, risk_active, escape_price, k, risk_breakout_streak,
                       left_latch_active, left_latch_position, left_trigger_price
                FROM engine_state
                WHERE system_name = ?
                """,
                (system_name,),
            ).fetchone()

        if row is None:
            return DualEngineState()

        return DualEngineState(
            risk_mode=row[0],
            risk_active=bool(row[1]),
            escape_price=row[2],
            k=float(row[3]),
            risk_breakout_streak=int(row[4]),
            left_latch_active=bool(row[5]),
            left_latch_position=float(row[6]),
            left_trigger_price=row[7],
        )

    def save(self, state: DualEngineState, system_name: str = "btc_dual_engine") -> None:
        payload = asdict(state)
        payload["system_name"] = system_name
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO engine_state (
                    system_name, risk_mode, risk_active, escape_price, k,
                    risk_breakout_streak, left_latch_active, left_latch_position,
                    left_trigger_price, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(system_name) DO UPDATE SET
                    risk_mode = excluded.risk_mode,
                    risk_active = excluded.risk_active,
                    escape_price = excluded.escape_price,
                    k = excluded.k,
                    risk_breakout_streak = excluded.risk_breakout_streak,
                    left_latch_active = excluded.left_latch_active,
                    left_latch_position = excluded.left_latch_position,
                    left_trigger_price = excluded.left_trigger_price,
                    updated_at = excluded.updated_at
                """,
                (
                    payload["system_name"],
                    payload["risk_mode"],
                    int(payload["risk_active"]),
                    payload["escape_price"],
                    payload["k"],
                    payload["risk_breakout_streak"],
                    int(payload["left_latch_active"]),
                    payload["left_latch_position"],
                    payload["left_trigger_price"],
                    payload["updated_at"],
                ),
            )
            conn.commit()

    def save_evaluation(
        self,
        evaluation: DualEngineEvaluation,
        system_name: str = "btc_dual_engine",
    ) -> None:
        payload = {
            "system_name": system_name,
            "engine_timestamp": evaluation.timestamp,
            "target_position_pct": evaluation.target_position_pct,
            "right_raw_position_pct": evaluation.right_raw_position_pct,
            "right_risked_position_pct": evaluation.right_risked_position_pct,
            "left_position_pct": evaluation.left_position_pct,
            "k": evaluation.k,
            "reasons_json": json.dumps(evaluation.reasons, ensure_ascii=False),
            "risk_mode": evaluation.state.risk_mode,
            "risk_active": int(evaluation.state.risk_active),
            "escape_price": evaluation.state.escape_price,
            "left_latch_active": int(evaluation.state.left_latch_active),
            "left_latch_position": evaluation.state.left_latch_position,
            "left_trigger_price": evaluation.state.left_trigger_price,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO engine_history (
                    system_name, engine_timestamp, target_position_pct,
                    right_raw_position_pct, right_risked_position_pct,
                    left_position_pct, k, reasons_json, risk_mode,
                    risk_active, escape_price, left_latch_active,
                    left_latch_position, left_trigger_price, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(system_name, engine_timestamp) DO UPDATE SET
                    target_position_pct = excluded.target_position_pct,
                    right_raw_position_pct = excluded.right_raw_position_pct,
                    right_risked_position_pct = excluded.right_risked_position_pct,
                    left_position_pct = excluded.left_position_pct,
                    k = excluded.k,
                    reasons_json = excluded.reasons_json,
                    risk_mode = excluded.risk_mode,
                    risk_active = excluded.risk_active,
                    escape_price = excluded.escape_price,
                    left_latch_active = excluded.left_latch_active,
                    left_latch_position = excluded.left_latch_position,
                    left_trigger_price = excluded.left_trigger_price,
                    created_at = excluded.created_at
                """,
                (
                    payload["system_name"],
                    payload["engine_timestamp"],
                    payload["target_position_pct"],
                    payload["right_raw_position_pct"],
                    payload["right_risked_position_pct"],
                    payload["left_position_pct"],
                    payload["k"],
                    payload["reasons_json"],
                    payload["risk_mode"],
                    payload["risk_active"],
                    payload["escape_price"],
                    payload["left_latch_active"],
                    payload["left_latch_position"],
                    payload["left_trigger_price"],
                    payload["created_at"],
                ),
            )
            conn.commit()
