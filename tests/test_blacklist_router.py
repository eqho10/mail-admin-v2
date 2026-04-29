"""Tests for routers/blacklist.py — Faz 4b Task 4."""
import json
import re
import time

import pytest

from services import dnsbl
from services.dnsbl import Snapshot, ZoneResult


def _csrf(authed_client):
    """Pull CSRF token from any authed page (default header may be stripped)."""
    r = authed_client.get("/blacklist")
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


def _seed_cache(ip="1.2.3.4", listed_zone=None):
    """Populate dnsbl._cache directly so /api/status returns deterministic data
    without DNS lookup."""
    results = []
    for z in dnsbl.DNSBL_ZONES:
        if z == listed_zone:
            results.append(ZoneResult(z, "listed", "127.0.0.4", None, 100))
        else:
            results.append(ZoneResult(z, "clean", None, None, 100))
    dnsbl._cache = Snapshot(ts="2026-04-29T00:00:00+00:00", ip=ip, results=results)
    dnsbl._cache_at = time.time()


def test_blacklist_page_renders_for_authed_user(authed_client):
    _seed_cache()
    r = authed_client.get("/blacklist")
    assert r.status_code == 200
    assert "Blacklist" in r.text


def test_blacklist_page_requires_auth(client):
    r = client.get("/blacklist", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


def test_blacklist_api_status_returns_seeded_snapshot(authed_client):
    _seed_cache(ip="1.2.3.4", listed_zone="zen.spamhaus.org")
    r = authed_client.get("/blacklist/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ip"] == "1.2.3.4"
    zen = next(rr for rr in body["results"] if rr["zone"] == "zen.spamhaus.org")
    assert zen["status"] == "listed"
    assert zen["return_code"] == "127.0.0.4"


def test_blacklist_api_history_returns_recent_snapshots(authed_client, tmp_path, monkeypatch):
    history_path = tmp_path / "dnsbl_history.json"
    history = [
        {"ts": f"2026-04-{29-i:02d}T00:00:00+00:00", "ip": "1.2.3.4",
         "results": [{"zone": "zen.spamhaus.org", "status": "clean",
                      "return_code": None, "return_text": None, "duration_ms": 100}]}
        for i in range(10)
    ]
    history_path.write_text(json.dumps(history))
    monkeypatch.setattr(dnsbl, "HISTORY_PATH", history_path)
    r = authed_client.get("/blacklist/api/history?days=5")
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 5
    # 5 days × 4 snaps/day cap = 20, but we only seeded 10 → returns all 10
    assert len(body["snapshots"]) == 10


def test_blacklist_api_history_rejects_out_of_range(authed_client):
    r = authed_client.get("/blacklist/api/history?days=0")
    assert r.status_code == 422
    r = authed_client.get("/blacklist/api/history?days=61")
    assert r.status_code == 422


def test_recheck_returns_429_within_rate_limit(authed_client, tmp_path, monkeypatch):
    monkeypatch.setattr("routers.blacklist.RECHECK_LOCK_PATH", tmp_path / "lock.json")
    (tmp_path / "lock.json").write_text(json.dumps({"last_at": time.time()}))
    r = authed_client.post("/blacklist/recheck")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_recheck_succeeds_outside_rate_limit(authed_client, tmp_path, monkeypatch, mock_dns_resolver):
    monkeypatch.setattr("routers.blacklist.RECHECK_LOCK_PATH", tmp_path / "lock.json")
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnsbl, "LATEST_PATH", tmp_path / "dnsbl_latest.json")
    monkeypatch.setattr(dnsbl, "HISTORY_PATH", tmp_path / "dnsbl_history.json")
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", tmp_path / "alerts.json")
    (tmp_path / "lock.json").write_text(json.dumps({"last_at": time.time() - 120}))
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    dnsbl._cache = None
    dnsbl._cache_at = 0.0
    r = authed_client.post("/blacklist/recheck")
    assert r.status_code == 200
    body = r.json()
    assert body["ip"] == "1.2.3.4"
    assert len(body["results"]) == 20


def test_recheck_requires_csrf(authed_client):
    authed_client.headers.pop("X-CSRF-Token", None)
    r = authed_client.post("/blacklist/recheck")
    assert r.status_code == 403


def test_recheck_requires_auth(client):
    r = client.post("/blacklist/recheck", follow_redirects=False)
    # Anonymous (no session cookie) → CSRF middleware passes, then
    # require_auth raises 401.
    assert r.status_code in (401, 403)


def test_recheck_writes_audit_entry(authed_client, tmp_path, monkeypatch, mock_dns_resolver):
    monkeypatch.setattr("routers.blacklist.RECHECK_LOCK_PATH", tmp_path / "lock.json")
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnsbl, "LATEST_PATH", tmp_path / "dnsbl_latest.json")
    monkeypatch.setattr(dnsbl, "HISTORY_PATH", tmp_path / "dnsbl_history.json")
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", tmp_path / "alerts.json")
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    dnsbl._cache = None
    dnsbl._cache_at = 0.0
    r = authed_client.post("/blacklist/recheck")
    assert r.status_code == 200
    assert "blacklist.recheck" in audit_file.read_text()
