"""Smoke pages — 8 sayfa + login + verify GET 200 + data-page marker."""
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_client(monkeypatch):
    """Login + verify ile session cookie almış client.
    https://testserver kullanılıyor çünkü ma_sess cookie'si Secure=True."""
    import app as app_module
    async def fake_send_mail(*a, **kw): return None
    monkeypatch.setattr(app_module, "send_mail", fake_send_mail)

    from app import app, OTP_STORE
    client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")

    client.post("/login", data={
        "email": "ekrem.mutlu@hotmail.com.tr",
        "password": "VkCngJrPL9Bspcmdg5rBIfRS",
    }, follow_redirects=False)
    code = json.loads(OTP_STORE.read_text())["code"]
    client.post("/verify", data={"code": code}, follow_redirects=False)
    return client


@pytest.mark.parametrize("path,marker", [
    ("/", 'data-page="overview"'),
    ("/aktivite", 'data-page="activity"'),
    ("/kuyruk", 'data-page="queue"'),
    ("/domain", 'data-page="domains"'),
    ("/mailbox", 'data-page="mailboxes"'),
    ("/deliverability", 'data-page="deliverability"'),
    ("/quarantine", 'data-page="quarantine"'),
    ("/ayarlar", 'data-page="settings"'),
])
def test_admin_pages_render_with_marker(authed_client, path, marker):
    r = authed_client.get(path)
    assert r.status_code == 200
    assert marker in r.text


def test_login_page_marker(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert 'data-page="login"' in r.text


def test_verify_page_marker(client, monkeypatch):
    import app as app_module
    async def fake_send_mail(*a, **kw): return None
    monkeypatch.setattr(app_module, "send_mail", fake_send_mail)
    client.post("/login", data={
        "email": "ekrem.mutlu@hotmail.com.tr",
        "password": "VkCngJrPL9Bspcmdg5rBIfRS",
    }, follow_redirects=False)
    r = client.get("/verify")
    assert r.status_code == 200
    assert 'data-page="verify"' in r.text
