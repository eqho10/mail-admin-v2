"""SQLite singleton helper. Schema migrates on first access.

Used by: services.reputation (Faz 3). Faz 4'te suppression/quarantine
aynı DB'ye eklenecek — tek dosya, çoklu tablo.
"""
import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "mail-admin-v2.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reputation_snapshots (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT NOT NULL,
  score           INTEGER NOT NULL,
  bounce_rate     REAL NOT NULL,
  complaint_rate  REAL,
  deferred_rate   REAL NOT NULL,
  total_sent      INTEGER NOT NULL,
  complaint_count INTEGER,
  source          TEXT NOT NULL DEFAULT 'cron'
);
CREATE INDEX IF NOT EXISTS idx_reputation_ts ON reputation_snapshots(ts);
"""

_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    """Return process-wide singleton connection. First call runs schema migrate."""
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def close() -> None:
    """Close singleton (lifespan shutdown). Commits any in-flight writes first."""
    global _conn
    if _conn is not None:
        try:
            _conn.commit()
        finally:
            _conn.close()
        _conn = None
