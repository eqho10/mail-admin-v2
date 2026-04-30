"""routers/activity.py — list, filter (status/domain/date/search), drawer payload."""
import pytest


@pytest.fixture
def fake_mainlog(monkeypatch, tmp_path):
    """services.exim.EXIM_MAINLOG'u golden fixture'a yönlendir."""
    import services.exim as exim_module
    fixture = tmp_path / "mainlog"
    src = open("/root/mail-admin-v2/tests/fixtures/exim_mainlog_sample.txt").read()
    fixture.write_text(src)
    monkeypatch.setattr(exim_module, "EXIM_MAINLOG", str(fixture))
    import routers.activity as activity_module
    monkeypatch.setattr(activity_module, "EXIM_MAINLOG", str(fixture))
    return fixture


def test_api_activity_returns_messages(authed_client, fake_mainlog):
    r = authed_client.get("/api/activity")
    assert r.status_code == 200
    body = r.json()
    assert "messages" in body
    assert body["count"] >= 1
    assert isinstance(body["messages"], list)


def test_api_activity_filter_by_domain(authed_client, fake_mainlog):
    r = authed_client.get("/api/activity?domain=bilgeworld.com")
    assert r.status_code == 200
    body = r.json()
    assert all(
        "bilgeworld.com" in (m.get("from") or "").lower()
        or any("bilgeworld.com" in (t or "").lower() for t in (m.get("to") or []))
        for m in body["messages"]
    )


def test_api_activity_filter_by_search_query(authed_client, fake_mainlog):
    r = authed_client.get("/api/activity?q=destek")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1


def test_api_activity_limit_clamp(authed_client, fake_mainlog):
    r = authed_client.get("/api/activity?limit=2")
    assert r.status_code == 200
    assert r.json()["count"] <= 2


def test_api_message_detail_returns_payload(authed_client, fake_mainlog):
    r = authed_client.get("/api/message/1rXyZ-0001Ab-7K")
    assert r.status_code == 200
    body = r.json()
    assert "message" in body
    assert "events" in body
    assert "raw_lines" in body
    assert body["message"]["msgid"] == "1rXyZ-0001Ab-7K"


def test_api_message_detail_404_unknown(authed_client, fake_mainlog):
    r = authed_client.get("/api/message/UNKNOWN-9999")
    assert r.status_code == 404


def test_api_activity_topic_blacklist_reads_alerts(authed_client, tmp_path, monkeypatch):
    """Topic=blacklist surfaces dnsbl alerts.json events."""
    import json as _json
    from services import dnsbl
    alerts_file = tmp_path / "alerts.json"
    alerts_file.write_text(_json.dumps([
        {"ts": "2026-04-29T14:00:00+00:00", "zone": "zen.spamhaus.org",
         "return_code": "127.0.0.4", "ip": "1.2.3.4"},
        {"ts": "2026-04-29T15:00:00+00:00", "zone": "bl.spamcop.net",
         "return_code": "127.0.0.2", "ip": "5.6.7.8"},
    ]))
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", alerts_file)
    r = authed_client.get("/api/activity?topic=blacklist")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert body["count"] == 2
    assert any("zen.spamhaus.org" in (e.get("title") or "") for e in body["events"])
    # Newest first
    assert body["events"][0]["ts"] == "2026-04-29T15:00:00+00:00"


def test_api_activity_topic_blacklist_handles_missing_file(authed_client, tmp_path, monkeypatch):
    from services import dnsbl
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", tmp_path / "does-not-exist.json")
    r = authed_client.get("/api/activity?topic=blacklist")
    assert r.status_code == 200
    assert r.json() == {"events": [], "count": 0}
