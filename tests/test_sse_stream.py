"""SSE stream — handshake event, topic filter, bilinmeyen topic 400."""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_client(monkeypatch):
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


def test_sse_unknown_topic_400(authed_client):
    r = authed_client.get("/api/events/stream?topic=garbage")
    assert r.status_code == 400


def test_sse_content_type_is_event_stream(authed_client, monkeypatch, tmp_path):
    # mainlog yok → FileNotFoundError branch → error event gönderir ve çıkar.
    # Generator sonlanır, tüm response okunabilir.
    import routers.activity as activity_module
    monkeypatch.setattr(activity_module, "EXIM_MAINLOG", "/nonexistent/mainlog_test_only")
    r = authed_client.get("/api/events/stream?topic=activity")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    # Error event içermeli — generator kapanır
    assert b"error" in r.content or b"mainlog" in r.content


def test_sse_default_topic_is_activity(authed_client, monkeypatch, tmp_path):
    import routers.activity as activity_module
    monkeypatch.setattr(activity_module, "EXIM_MAINLOG", "/nonexistent/mainlog_test_only")
    r = authed_client.get("/api/events/stream")
    assert r.status_code == 200
    # Varsayılan topic activity — endpoint ulaşılabilir
    assert "text/event-stream" in r.headers["content-type"]
