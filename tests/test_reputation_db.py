"""snapshot_now + query_history + 90d retention."""
from datetime import datetime, timedelta
import pytest


def test_snapshot_now_inserts_and_returns_score(tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'rep.db')
    import services.db
    services.db._conn = None  # force fresh

    import services.reputation as rep
    # Mock data sources
    monkeypatch.setattr(rep, '_fetch_exim_window', lambda days: {
        'bounce_rate': 0.01, 'deferred_rate': 0.0, 'total_sent': 100,
    })
    monkeypatch.setattr(rep, '_fetch_brevo_complaints', lambda days, total: {
        'complaint_rate': 0.0, 'complaint_count': 0,
    })

    result = rep.snapshot_now(source='manual')
    assert result['score'] == 95
    assert result['source'] == 'manual'

    # Verify INSERT
    conn = services.db.get_conn()
    rows = conn.execute("SELECT score, source FROM reputation_snapshots").fetchall()
    assert len(rows) == 1
    assert rows[0]['score'] == 95


def test_snapshot_handles_brevo_none(tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'rep.db')
    import services.db
    services.db._conn = None

    import services.reputation as rep
    monkeypatch.setattr(rep, '_fetch_exim_window', lambda days: {
        'bounce_rate': 0.01, 'deferred_rate': 0.0, 'total_sent': 100,
    })
    monkeypatch.setattr(rep, '_fetch_brevo_complaints', lambda days, total: None)

    result = rep.snapshot_now(source='cron')
    assert result['complaint_rate'] is None
    assert result['score'] == 95  # complaint penalty zeroed


def test_retention_drops_old_rows(tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'rep.db')
    import services.db
    services.db._conn = None
    conn = services.db.get_conn()

    # Insert 1 old (100 days ago) + 1 fresh
    old_ts = (datetime.utcnow() - timedelta(days=100)).isoformat()
    fresh_ts = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO reputation_snapshots (ts, score, bounce_rate, deferred_rate, total_sent) "
        "VALUES (?, 95, 0.01, 0.0, 100)", (old_ts,)
    )
    conn.execute(
        "INSERT INTO reputation_snapshots (ts, score, bounce_rate, deferred_rate, total_sent) "
        "VALUES (?, 90, 0.02, 0.0, 100)", (fresh_ts,)
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM reputation_snapshots").fetchone()[0] == 2

    import services.reputation as rep
    deleted = rep._prune_old(days=90)
    assert deleted == 1

    remaining = conn.execute("SELECT score FROM reputation_snapshots").fetchall()
    assert len(remaining) == 1
    assert remaining[0]['score'] == 90


def test_query_history_returns_ordered(tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'rep.db')
    import services.db
    services.db._conn = None
    conn = services.db.get_conn()

    from datetime import datetime, timedelta
    for days_ago, score in [(5, 80), (1, 90), (3, 85)]:
        ts = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
        conn.execute(
            "INSERT INTO reputation_snapshots (ts, score, bounce_rate, deferred_rate, total_sent) "
            "VALUES (?, ?, 0.0, 0.0, 100)", (ts, score)
        )
    conn.commit()

    import services.reputation as rep
    points = rep.query_history(days=30)
    assert len(points) == 3
    # Ordered ascending by ts → oldest first
    assert [p['score'] for p in points] == [80, 85, 90]
