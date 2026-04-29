"""Reputation router endpoints: current (cached), history, snapshot (HMAC)."""
import os
import pytest


def test_current_returns_cached(authed_client, tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'rep.db')
    import services.db
    services.db._conn = None

    import services.reputation as rep
    monkeypatch.setattr(rep, '_fetch_exim_window', lambda days: {
        'bounce_rate': 0.0, 'deferred_rate': 0.0, 'total_sent': 100,
    })
    monkeypatch.setattr(rep, '_fetch_brevo_complaints', lambda days, total_sent: {
        'complaint_count': 0, 'complaint_rate': 0.0,
    })

    # Clear cache
    import routers.reputation
    routers.reputation._cache_clear()

    r1 = authed_client.get('/api/reputation/current')
    assert r1.status_code == 200
    assert r1.json()['score'] == 100

    # Mutate underlying source — should NOT see new value (cached)
    monkeypatch.setattr(rep, '_fetch_exim_window', lambda days: {
        'bounce_rate': 0.5, 'deferred_rate': 0.0, 'total_sent': 100,
    })
    r2 = authed_client.get('/api/reputation/current')
    assert r2.json()['score'] == 100  # still cached


def test_history_returns_ordered_points(authed_client, tmp_path, monkeypatch):
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

    r = authed_client.get('/api/reputation/history?days=30')
    assert r.status_code == 200
    points = r.json()['points']
    assert [p['score'] for p in points] == [80, 85, 90]


def test_snapshot_requires_hmac(client, monkeypatch):
    monkeypatch.setenv('REPUTATION_CRON_TOKEN', 'secret-test-token-12345')

    # No header → 401
    r1 = client.post('/api/reputation/snapshot')
    assert r1.status_code == 401

    # Wrong token → 401
    r2 = client.post('/api/reputation/snapshot', headers={'X-Cron-Token': 'wrong'})
    assert r2.status_code == 401


def test_snapshot_with_valid_hmac_inserts(client, tmp_path, monkeypatch):
    monkeypatch.setenv('REPUTATION_CRON_TOKEN', 'secret-test-token-12345')
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'rep.db')
    import services.db
    services.db._conn = None

    import services.reputation as rep
    monkeypatch.setattr(rep, '_fetch_exim_window', lambda days: {
        'bounce_rate': 0.0, 'deferred_rate': 0.0, 'total_sent': 100,
    })
    monkeypatch.setattr(rep, '_fetch_brevo_complaints', lambda days, total_sent: {
        'complaint_count': 0, 'complaint_rate': 0.0,
    })

    r = client.post('/api/reputation/snapshot',
                    headers={'X-Cron-Token': 'secret-test-token-12345'})
    assert r.status_code == 200
    assert r.json()['score'] == 100
    assert r.json()['source'] == 'cron'


def test_current_handles_brevo_down(authed_client, tmp_path, monkeypatch):
    monkeypatch.setattr('services.db.DB_PATH', tmp_path / 'rep.db')
    import services.db
    services.db._conn = None

    import services.reputation as rep
    monkeypatch.setattr(rep, '_fetch_exim_window', lambda days: {
        'bounce_rate': 0.0, 'deferred_rate': 0.0, 'total_sent': 100,
    })
    monkeypatch.setattr(rep, '_fetch_brevo_complaints', lambda days, total_sent: None)

    import routers.reputation
    routers.reputation._cache_clear()

    r = authed_client.get('/api/reputation/current')
    body = r.json()
    assert body['complaint_available'] is False
    assert body['complaint_rate'] is None
    assert body['score'] == 100
