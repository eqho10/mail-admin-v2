"""Smoke pages — 8 sayfa + login + verify GET 200 + data-page marker."""
import os
import pytest
from fastapi.testclient import TestClient


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
        "email": os.getenv("ADMIN_EMAIL", "ekrem.mutlu@hotmail.com.tr"),
        "password": os.getenv("ADMIN_PASS", "VkCngJrPL9Bspcmdg5rBIfRS"),
    }, follow_redirects=False)
    r = client.get("/verify")
    assert r.status_code == 200
    assert 'data-page="verify"' in r.text
