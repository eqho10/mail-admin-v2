"""Cmd+K registry — server-side action list, group filter, dynamic mock fetch."""
import pytest


def test_cmdk_actions_returns_13_builtin(authed_client):
    r = authed_client.get("/api/cmdk/actions")
    assert r.status_code == 200
    body = r.json()
    assert "actions" in body
    assert len(body["actions"]) == 13


def test_cmdk_actions_have_required_fields(authed_client):
    r = authed_client.get("/api/cmdk/actions")
    for a in r.json()["actions"]:
        assert "id" in a
        assert "label" in a
        assert "group" in a
        assert a["group"] in ("Aksiyon", "Gezinme")


def test_cmdk_actions_includes_theme_toggle(authed_client):
    r = authed_client.get("/api/cmdk/actions")
    ids = [a["id"] for a in r.json()["actions"]]
    assert "theme.toggle" in ids
    assert "logout" in ids
    assert "test-mail" in ids


def test_cmdk_dynamic_sources_reachable(authed_client):
    """Cmd+K'nin dynamic group'ları için ihtiyaç duyulan endpoint'ler erişilebilir mi."""
    r1 = authed_client.get("/api/domains")
    r2 = authed_client.get("/api/mailboxes")
    # Bu endpoint'ler app.py'da var (Faz 1'den) — 200 dönmeli (boş liste olabilir)
    assert r1.status_code == 200
    # /api/mailboxes Faz 1'de domain query param istiyor; query yoksa 422 veya 200 boş
    assert r2.status_code in (200, 422)
