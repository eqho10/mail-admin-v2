"""Tests for services/hestia.py — read API client + Mailbox dataclass + cache."""
import importlib

import httpx
import pytest

import services.hestia
from services.hestia import (
    HestiaAPIError,
    Mailbox,
    _cache_clear,
    list_aliases,
    list_mail_domains,
    list_mailboxes,
)


def test_mailbox_dataclass_required_fields():
    m = Mailbox(
        email="ekrem@bilgeworld.com",
        domain="bilgeworld.com",
        user="ekrem",
        quota_mb=1024,
        used_mb=200,
        status="active",
        created_at="2026-01-10T10:00:00Z",
        alias_count=None,
        last_login=None,
        disk_size_mb=None,
    )
    assert m.email == "ekrem@bilgeworld.com"
    assert m.quota_mb == 1024
    assert m.alias_count is None


# ---------- Helpers --------------------------------------------------------


def _reload_with_env(monkeypatch, url="https://example.com:8083", key="test-key"):
    """Reload services.hestia after setting env vars so module-level
    HESTIA_API_URL / HESTIA_API_KEY pick up the test values."""
    monkeypatch.setenv("HESTIA_API_URL", url)
    monkeypatch.setenv("HESTIA_API_KEY", key)
    importlib.reload(services.hestia)
    return services.hestia


# ---------- list_mail_domains ---------------------------------------------


@pytest.mark.asyncio
async def test_list_mail_domains_calls_correct_url(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            # Real HestiaCP shape: top-level keys = domain names.
            return {
                "bilgeworld.com": {"ACCOUNTS": "2"},
                "rapor.bilgeworld.com": {"ACCOUNTS": "0"},
            }

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            captured["url"] = url
            captured["params"] = kw.get("params")
            captured["headers"] = kw.get("headers")
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    mod = _reload_with_env(monkeypatch)
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    mod._cache_clear()

    domains = await mod.list_mail_domains()
    assert "bilgeworld.com" in domains
    assert "rapor.bilgeworld.com" in domains
    # cmd is passed via params
    assert captured["params"]["cmd"] == "v-list-mail-domains"
    assert captured["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_list_mail_domains_uses_cache(monkeypatch):
    call_count = {"n": 0}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"a.com": {}}

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            call_count["n"] += 1
            return FakeResponse()

    mod = _reload_with_env(monkeypatch)
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    mod._cache_clear()

    await mod.list_mail_domains()
    await mod.list_mail_domains()  # cache hit
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_list_mail_domains_raises_on_http_error(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            raise httpx.ConnectError("connection refused")

    mod = _reload_with_env(monkeypatch)
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    mod._cache_clear()

    with pytest.raises(mod.HestiaAPIError):
        await mod.list_mail_domains()


# ---------- list_mailboxes -------------------------------------------------


@pytest.mark.asyncio
async def test_list_mailboxes_returns_mailbox_objects(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            # Real HestiaCP shape captured 2026-04-29 from
            # /usr/local/hestia/bin/v-list-mail-accounts ekrem bilgeworld.com json
            return {
                "info": {
                    "ALIAS": "",
                    "FWD": "",
                    "FWD_ONLY": "",
                    "AUTOREPLY": "no",
                    "QUOTA": "1024",
                    "U_DISK": "200",
                    "SUSPENDED": "no",
                    "TIME": "10:00:00",
                    "DATE": "2026-01-10",
                }
            }

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            return FakeResponse()

    mod = _reload_with_env(monkeypatch)
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    mod._cache_clear()

    boxes = await mod.list_mailboxes("bilgeworld.com")
    assert len(boxes) == 1
    assert isinstance(boxes[0], mod.Mailbox)
    assert boxes[0].email == "info@bilgeworld.com"
    assert boxes[0].quota_mb == 1024
    assert boxes[0].used_mb == 200
    assert boxes[0].status == "active"
    assert boxes[0].created_at == "2026-01-10T10:00:00Z"


@pytest.mark.asyncio
async def test_list_mailboxes_handles_unlimited_quota(monkeypatch):
    """Real HestiaCP returns QUOTA='unlimited' for unrestricted accounts."""

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "ekrem": {
                    "ALIAS": "",
                    "FWD": "",
                    "FWD_ONLY": "",
                    "AUTOREPLY": "no",
                    "QUOTA": "unlimited",
                    "U_DISK": "5",
                    "SUSPENDED": "yes",
                    "TIME": "22:40:29",
                    "DATE": "2026-02-06",
                }
            }

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            return FakeResponse()

    mod = _reload_with_env(monkeypatch)
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    mod._cache_clear()

    boxes = await mod.list_mailboxes("bilgeworld.com")
    assert len(boxes) == 1
    # 'unlimited' → 0 (sentinel for no quota)
    assert boxes[0].quota_mb == 0
    assert boxes[0].used_mb == 5
    assert boxes[0].status == "suspended"
