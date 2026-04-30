"""Faz 4a hardening Fix 1: hestia subprocess wrapped in asyncio.to_thread.

All write CLIs (`v-add-mail-account`, `v-delete-mail-account`, ...) take
3-15 seconds on a real HestiaCP install. If routers call these directly
(sync), they block FastAPI's event loop for that duration — every other
request stalls.

Fix: routers wrap each hestia.<write_fn> call in `asyncio.to_thread(...)` so
the subprocess runs on a worker thread, leaving the event loop free.

These tests assert the routers use `asyncio.to_thread` for hestia writes
by patching `asyncio.to_thread` itself with a counter.
"""
import asyncio
import re

import pytest


def _csrf(authed_client, page="/aktivite"):
    r = authed_client.get(page)
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


def _patch_to_thread_tracker(monkeypatch):
    """Replace asyncio.to_thread with a tracker. Returns a list of called fn.__name__."""
    real_to_thread = asyncio.to_thread
    called = []

    async def tracked_to_thread(func, *args, **kwargs):
        called.append(getattr(func, "__name__", repr(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("asyncio.to_thread", tracked_to_thread)
    # Also patch in the routers.mailboxes module if it imports asyncio
    import routers.mailboxes as rm
    if hasattr(rm, "asyncio"):
        monkeypatch.setattr(rm.asyncio, "to_thread", tracked_to_thread)
    return called


def _named(name):
    """Helper: produce a named no-op so we can assert on `func.__name__`."""
    def _f(*a, **kw): return None
    _f.__name__ = name
    return _f


def test_add_mailbox_route_uses_to_thread(authed_client, monkeypatch):
    """POST /mailboxes/create must call hestia.add_mailbox via asyncio.to_thread."""
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "add_mailbox", _named("add_mailbox"))
    called = _patch_to_thread_tracker(monkeypatch)

    r = authed_client.post(
        "/mailboxes/create",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "email_local": "asynctest",
            "password": "VeryStrongPass#123!",
            "password_confirm": "VeryStrongPass#123!",
            "quota_mb": "1024",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    # add_mailbox went through asyncio.to_thread
    assert any("add_mailbox" in name for name in called), (
        f"Expected hestia.add_mailbox to run via asyncio.to_thread, "
        f"but to_thread call list was: {called}"
    )


def test_delete_mailbox_route_uses_to_thread(authed_client, monkeypatch):
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "delete_mailbox", _named("delete_mailbox"))
    monkeypatch.setattr(hestia, "list_aliases", _make_async_return([]))
    called = _patch_to_thread_tracker(monkeypatch)

    r = authed_client.post(
        "/mailboxes/delete",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "user": "asynctest",
            "expected_email": "asynctest@x.com",
            "confirmation_typed": "asynctest@x.com",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert any("delete_mailbox" in name for name in called), (
        f"Expected hestia.delete_mailbox to run via asyncio.to_thread, "
        f"but to_thread call list was: {called}"
    )


def test_change_quota_route_uses_to_thread(authed_client, monkeypatch):
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_quota", _named("change_quota"))
    called = _patch_to_thread_tracker(monkeypatch)

    r = authed_client.post(
        "/mailboxes/change-quota",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "user": "asynctest",
            "quota_mb": "2048",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert any("change_quota" in name for name in called), (
        f"Expected hestia.change_quota to run via asyncio.to_thread, "
        f"but to_thread call list was: {called}"
    )


def test_reset_password_route_uses_to_thread(authed_client, monkeypatch):
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_password", _named("change_password"))
    called = _patch_to_thread_tracker(monkeypatch)

    r = authed_client.post(
        "/mailboxes/reset-password",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "user": "asynctest",
            "mode": "generate",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert any("change_password" in name for name in called), (
        f"Expected hestia.change_password to run via asyncio.to_thread, "
        f"but to_thread call list was: {called}"
    )


def test_alias_add_route_uses_to_thread(authed_client, monkeypatch):
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "add_alias", _named("add_alias"))
    called = _patch_to_thread_tracker(monkeypatch)

    r = authed_client.post(
        "/mailboxes/aliases/add",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "user": "asynctest",
            "alias_local": "info",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert any("add_alias" in name for name in called), (
        f"Expected hestia.add_alias to run via asyncio.to_thread, "
        f"but to_thread call list was: {called}"
    )


def _make_async_return(value):
    async def _coro(*a, **kw):
        return value
    return _coro
