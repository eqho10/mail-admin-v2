"""routers/activity.py — list, filter (status/domain/date/search), drawer payload."""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_client(monkeypatch):
    """https://testserver TestClient, login + verify ile session cookie."""
    from app import app, OTP_STORE
    import app as app_module
    async def fake_send_mail(*a, **kw): return None
    monkeypatch.setattr(app_module, "send_mail", fake_send_mail)
    client = TestClient(app, base_url="https://testserver")
    client.post("/login", data={
        "email": os.getenv("ADMIN_EMAIL", "ekrem.mutlu@hotmail.com.tr"),
        "password": os.getenv("ADMIN_PASS", "VkCngJrPL9Bspcmdg5rBIfRS"),
    }, follow_redirects=False)
    import json
    code = json.loads(OTP_STORE.read_text())["code"]
    client.post("/verify", data={"code": code}, follow_redirects=False)
    return client


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
