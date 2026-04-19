"""
core/database.py — SQLite persistence layer.
Stores pipeline runs, requirements, test cases, execution results, and defects.
All reads/writes go through this module — no raw file I/O elsewhere.
"""

import sqlite3
import json
import datetime
import os
import pandas as pd
from contextlib import contextmanager
from typing import Optional

from core.config import get_config


def _db_path() -> str:
    path = get_config().db_path
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    return path


@contextmanager
def _conn():
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT UNIQUE NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            product     TEXT,
            provider    TEXT,
            model       TEXT,
            platform    TEXT,
            stage       INTEGER DEFAULT 0,
            mock_mode   INTEGER DEFAULT 1,
            status      TEXT DEFAULT 'in_progress',
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS requirements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL REFERENCES runs(run_id),
            mrd_json    TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS test_cases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL REFERENCES runs(run_id),
            tc_json     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            approved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS execution_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL REFERENCES runs(run_id),
            results_json TEXT NOT NULL,
            executed_at  TEXT NOT NULL,
            duration_sec REAL
        );

        CREATE TABLE IF NOT EXISTS defects (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL REFERENCES runs(run_id),
            defects_json TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT REFERENCES runs(run_id),
            ts          TEXT NOT NULL,
            level       TEXT NOT NULL,
            message     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS token_usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT REFERENCES runs(run_id),
            ts          TEXT NOT NULL,
            agent       TEXT,
            provider    TEXT,
            model       TEXT,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_logs_run ON pipeline_logs(run_id);
        CREATE INDEX IF NOT EXISTS idx_token_run ON token_usage(run_id);
        """)


# ── Run management ────────────────────────────────────────────────

def create_run(run_id: str, provider: str, model: str, platform: str, mock_mode: bool) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO runs (run_id, created_at, updated_at, provider, model, platform, mock_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, now, now, provider, model, platform, int(mock_mode))
        )
    return {"run_id": run_id, "created_at": now}


def update_run(run_id: str, **kwargs):
    if not kwargs:
        return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    kwargs["updated_at"] = now
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [run_id]
    with _conn() as conn:
        conn.execute(f"UPDATE runs SET {cols} WHERE run_id = ?", vals)


def get_run(run_id: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_run(run_id: str):
    with _conn() as conn:
        for table in ["requirements", "test_cases", "execution_results", "defects", "pipeline_logs", "token_usage"]:
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


# ── Artifact storage ──────────────────────────────────────────────

def save_requirements(run_id: str, mrd: dict):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO requirements (run_id, mrd_json, created_at) VALUES (?, ?, ?)",
            (run_id, json.dumps(mrd), now)
        )
    update_run(run_id, product=mrd.get("product", ""), stage=2)


def load_requirements(run_id: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT mrd_json FROM requirements WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        return json.loads(row["mrd_json"]) if row else None


def save_test_cases(run_id: str, df: pd.DataFrame):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO test_cases (run_id, tc_json, created_at) VALUES (?, ?, ?)",
            (run_id, df.to_json(orient="records"), now)
        )
    update_run(run_id, stage=4)


def load_test_cases(run_id: str) -> Optional[pd.DataFrame]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT tc_json FROM test_cases WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        if not row:
            return None
        from io import StringIO
        return pd.read_json(StringIO(row["tc_json"]), orient="records")


def save_execution_results(run_id: str, df: pd.DataFrame, duration_sec: float = 0.0):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO execution_results (run_id, results_json, executed_at, duration_sec) VALUES (?, ?, ?, ?)",
            (run_id, df.to_json(orient="records"), now, duration_sec)
        )
    update_run(run_id, stage=6)


def load_execution_results(run_id: str) -> Optional[pd.DataFrame]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT results_json FROM execution_results WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        if not row:
            return None
        from io import StringIO
        return pd.read_json(StringIO(row["results_json"]), orient="records")


def save_defects(run_id: str, df: pd.DataFrame):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO defects (run_id, defects_json, created_at) VALUES (?, ?, ?)",
            (run_id, df.to_json(orient="records"), now)
        )
    update_run(run_id, stage=8, status="complete")


def load_defects(run_id: str) -> Optional[pd.DataFrame]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT defects_json FROM defects WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        if not row:
            return None
        from io import StringIO
        return pd.read_json(StringIO(row["defects_json"]), orient="records")


# ── Logging ───────────────────────────────────────────────────────

def db_log(run_id: Optional[str], level: str, message: str):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO pipeline_logs (run_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (run_id, now, level, message)
        )


def get_logs(run_id: str, limit: int = 500) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ts, level, message FROM pipeline_logs WHERE run_id = ? ORDER BY id DESC LIMIT ?",
            (run_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# ── Token tracking ────────────────────────────────────────────────

def record_token_usage(run_id: str, agent: str, provider: str, model: str,
                        input_tokens: int, output_tokens: int):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO token_usage (run_id, ts, agent, provider, model, input_tokens, output_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, now, agent, provider, model, input_tokens, output_tokens)
        )


def get_token_summary(run_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            """SELECT SUM(input_tokens) as total_in, SUM(output_tokens) as total_out,
                      COUNT(*) as calls
               FROM token_usage WHERE run_id = ?""",
            (run_id,)
        ).fetchone()
        return {
            "input": row["total_in"] or 0,
            "output": row["total_out"] or 0,
            "calls": row["calls"] or 0,
        }
