"""Tests for services/brevo_suppression.py — list (cached) + remove (flushes cache).

Note on category filtering: Brevo's GET /v3/smtp/blockedContacts does NOT
support a reason-code query param (verified live 2026-04-29 — `senders` is
sender-EMAIL filter, `reason`/`type` silently ignored). So `_REASON_MAP` is
applied CLIENT-SIDE after the fetch. Tests assert the client-side filter
behavior, not a query param.
"""
import pytest
import httpx

from services.brevo_suppression import (
    Block, list_blocked, remove_from_suppression, _cache_clear,
    BrevoSuppressionError,
)
import services.brevo_suppression as bs


def test_block_dataclass():
    b = Block(email="a@b.com", reason="hardBounce", blocked_at="2026-04-29T12:00:00Z")
    assert b.email == "a@b.com"
    assert b.reason == "hardBounce"


@pytest.mark.asyncio
async def test_list_blocked_no_filter(monkeypatch):
    _cache_clear()
    captured = {}

    class FakeResp:
        status_code = 200
        content = b"{}"
        def json(self):
            return {"contacts": [
                {"email": "x@y.com",
                 "reason": {"code": "hardBounce", "message": "mailbox not found"},
                 "blockedAt": "2026-04-28T10:00:00Z"},
            ]}
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            captured["url"] = url
            captured["params"] = kw.get("params")
            return FakeResp()
        async def delete(self, url, **kw):
            captured["delete_url"] = url
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")
    blocks = await list_blocked()
    assert len(blocks) == 1
    assert blocks[0].email == "x@y.com"
    assert blocks[0].reason == "hardBounce"
    assert "blockedContacts" in captured["url"]


@pytest.mark.asyncio
async def test_list_blocked_category_filters_client_side(monkeypatch):
    """category='hard' → only hardBounce records returned, others filtered out."""
    _cache_clear()

    class FakeResp:
        status_code = 200
        content = b"{}"
        def json(self):
            return {"contacts": [
                {"email": "h@y.com",
                 "reason": {"code": "hardBounce", "message": "x"},
                 "blockedAt": "2026-04-28T10:00:00Z"},
                {"email": "u@y.com",
                 "reason": {"code": "unsubscribedViaEmail", "message": "x"},
                 "blockedAt": "2026-04-28T10:00:00Z"},
                {"email": "s@y.com",
                 "reason": {"code": "contactFlaggedAsSpam", "message": "x"},
                 "blockedAt": "2026-04-28T10:00:00Z"},
            ]}
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")

    hard_only = await list_blocked(category="hard")
    assert len(hard_only) == 1
    assert hard_only[0].email == "h@y.com"
    assert hard_only[0].reason == "hardBounce"

    _cache_clear()
    unsub_only = await list_blocked(category="unsub")
    assert len(unsub_only) == 1
    assert unsub_only[0].reason == "unsubscribedViaEmail"

    _cache_clear()
    spam_only = await list_blocked(category="spam")
    assert len(spam_only) == 1
    assert spam_only[0].reason == "contactFlaggedAsSpam"


@pytest.mark.asyncio
async def test_list_blocked_uses_cache(monkeypatch):
    _cache_clear()
    call_count = {"n": 0}

    class FakeResp:
        status_code = 200
        content = b"{}"
        def json(self): return {"contacts": []}
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            call_count["n"] += 1
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")
    await list_blocked()
    await list_blocked()
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_remove_from_suppression_calls_delete(monkeypatch):
    _cache_clear()
    captured = {}

    class FakeResp:
        status_code = 204
        content = b""
        def json(self): return {}
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def delete(self, url, **kw):
            captured["url"] = url
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")
    await remove_from_suppression("user@example.com")
    assert "blockedContacts/user@example.com" in captured["url"]


@pytest.mark.asyncio
async def test_remove_flushes_cache(monkeypatch):
    _cache_clear()
    bs._cache[("blocked", "all", 100, 0)] = (9999999999.0, ["stale"])

    class FakeResp:
        status_code = 204
        content = b""
        def json(self): return {}
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def delete(self, url, **kw):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")
    await remove_from_suppression("user@example.com")
    assert ("blocked", "all", 100, 0) not in bs._cache


@pytest.mark.asyncio
async def test_remove_invalid_email_raises(monkeypatch):
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")
    with pytest.raises(BrevoSuppressionError):
        await remove_from_suppression("not-an-email")
    with pytest.raises(BrevoSuppressionError):
        await remove_from_suppression("")


@pytest.mark.asyncio
async def test_remove_404_is_idempotent_no_raise(monkeypatch):
    """Admin 'remove' should succeed silently if email is already not suppressed."""
    _cache_clear()

    class FakeResp:
        status_code = 404
        content = b'{"code":"document_not_found","message":"Email not in suppression list"}'
        def json(self): return {"code": "document_not_found"}
        def raise_for_status(self): raise httpx.HTTPStatusError("404", request=None, response=self)

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def delete(self, url, **kw):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")
    # Should NOT raise
    await remove_from_suppression("nonexistent@example.com")


@pytest.mark.asyncio
async def test_remove_url_encodes_plus_in_email(monkeypatch):
    """Plus-addressing must be URL-encoded so Brevo doesn't ambiguously interpret +."""
    _cache_clear()
    captured = {}

    class FakeResp:
        status_code = 204
        content = b""
        def json(self): return {}
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def delete(self, url, **kw):
            captured["url"] = url
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bs, "BREVO_API_KEY", "test-fake-not-real")
    await remove_from_suppression("user+tag@example.com")
    # %2B = encoded +
    assert "user%2Btag@example.com" in captured["url"], f"URL not encoded: {captured['url']}"
