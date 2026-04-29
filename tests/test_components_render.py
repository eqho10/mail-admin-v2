"""test_components_render -- showcase route renders all 16 component markers."""
import json
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_client(monkeypatch):
    """Login + verify ile session cookie almis client.
    https://testserver kullaniliyor cunku ma_sess cookie'si Secure=True."""
    import app as app_module

    async def fake_send_mail(*a, **kw):
        return None

    monkeypatch.setattr(app_module, "send_mail", fake_send_mail)

    from app import app, OTP_STORE

    client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")

    client.post(
        "/login",
        data={
            "email": os.getenv("ADMIN_EMAIL", "ekrem.mutlu@hotmail.com.tr"),
            "password": os.getenv("ADMIN_PASS", "VkCngJrPL9Bspcmdg5rBIfRS"),
        },
        follow_redirects=False,
    )
    code = json.loads(OTP_STORE.read_text())["code"]
    client.post("/verify", data={"code": code}, follow_redirects=False)
    return client


def test_showcase_renders_all_16_components(authed_client):
    r = authed_client.get("/dev/components")
    assert r.status_code == 200
    expected_markers = [
        'data-component="sidebar"',
        'data-component="topbar"',
        'data-component="stat-card"',
        'data-component="gauge"',
        'data-component="status-badge"',
        'data-component="table"',
        'data-component="drawer"',
        'data-component="command-palette"',
        'data-component="toast"',
        'data-component="filter-bar"',
        'data-component="empty-state"',
        'data-component="skeleton"',
        'data-component="tabs"',
        'data-component="json-viewer"',
        'data-component="code-block"',
        'data-component="confirm-modal"',
    ]
    missing = [m for m in expected_markers if m not in r.text]
    assert not missing, f"Showcase'de eksik komponent marker'lari: {missing}"
