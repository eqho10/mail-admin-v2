"""DB singleton + schema migration smoke."""
import sqlite3
import pytest


def test_get_conn_returns_singleton(tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'test.db')
    # Force re-init by clearing cached module conn
    import services.db
    services.db._conn = None

    c1 = services.db.get_conn()
    c2 = services.db.get_conn()
    assert c1 is c2  # singleton

    # reputation_snapshots table created
    cur = c1.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reputation_snapshots'")
    assert cur.fetchone() is not None


def test_schema_migrate_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'test.db')
    import services.db
    services.db._conn = None

    services.db.get_conn()
    services.db._conn = None  # force re-open
    services.db.get_conn()  # should not raise

    conn = services.db.get_conn()
    cur = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='reputation_snapshots'")
    assert cur.fetchone()[0] == 1  # still single table
