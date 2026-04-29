import asyncio
import json
from pathlib import Path
import pytest

from services.mailbox_stats import (
    refresh, _cache_clear_lock, STATS_JSON_PATH, TRIGGER_FILE,
)
import services.mailbox_stats as ms


@pytest.mark.asyncio
async def test_refresh_writes_atomic_json(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(ms, "TRIGGER_FILE", tmp_path / "trigger")

    async def fake_list_domains():
        return ["bilgeworld.com"]

    from services.hestia import Mailbox
    async def fake_list_mailboxes(domain):
        return [Mailbox(
            email="ekrem@bilgeworld.com",
            domain=domain,
            user="ekrem",
            quota_mb=1024,
            used_mb=200,
            status="active",
            created_at="2026-01-10T10:00:00Z",
        )]

    async def fake_list_aliases(domain, user):
        return ["info", "support"]

    monkeypatch.setattr(ms, "list_mail_domains", fake_list_domains)
    monkeypatch.setattr(ms, "list_mailboxes", fake_list_mailboxes)
    monkeypatch.setattr(ms, "list_aliases", fake_list_aliases)
    monkeypatch.setattr(ms, "_grep_dovecot_last_login", lambda email: "2026-04-29T12:00:00Z")
    monkeypatch.setattr(ms, "_du_maildir", lambda email, domain, user: 240)

    _cache_clear_lock()
    result = await refresh()
    assert result["domain_count"] == 1
    assert result["mailbox_count"] == 1
    assert (tmp_path / "stats.json").exists()
    on_disk = json.loads((tmp_path / "stats.json").read_text())
    assert on_disk["domains"]["bilgeworld.com"]["mailboxes"][0]["alias_count"] == 2
    assert on_disk["domains"]["bilgeworld.com"]["mailboxes"][0]["last_login"] == "2026-04-29T12:00:00Z"
    assert on_disk["domains"]["bilgeworld.com"]["mailboxes"][0]["disk_size_mb"] == 240


@pytest.mark.asyncio
async def test_refresh_consumes_trigger(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(ms, "TRIGGER_FILE", tmp_path / "trigger")
    (tmp_path / "trigger").touch()

    async def fake_list_domains(): return []
    monkeypatch.setattr(ms, "list_mail_domains", fake_list_domains)
    _cache_clear_lock()
    await refresh()
    assert not (tmp_path / "trigger").exists()


@pytest.mark.asyncio
async def test_refresh_handles_per_mailbox_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(ms, "TRIGGER_FILE", tmp_path / "trigger")

    async def fake_list_domains(): return ["x.com"]
    from services.hestia import Mailbox
    async def fake_list_mailboxes(d):
        return [
            Mailbox(email="ok@x.com", domain="x.com", user="ok", quota_mb=1024,
                    used_mb=0, status="active", created_at=""),
            Mailbox(email="bad@x.com", domain="x.com", user="bad", quota_mb=1024,
                    used_mb=0, status="active", created_at=""),
        ]
    async def fake_list_aliases(d, u):
        if u == "bad":
            raise RuntimeError("alias fetch failed")
        return []

    monkeypatch.setattr(ms, "list_mail_domains", fake_list_domains)
    monkeypatch.setattr(ms, "list_mailboxes", fake_list_mailboxes)
    monkeypatch.setattr(ms, "list_aliases", fake_list_aliases)
    monkeypatch.setattr(ms, "_grep_dovecot_last_login", lambda email: None)
    monkeypatch.setattr(ms, "_du_maildir", lambda *a: None)
    _cache_clear_lock()
    result = await refresh()
    assert result["mailbox_count"] == 2
    assert len(result["errors"]) >= 1
    assert any(e["mailbox"] == "bad@x.com" for e in result["errors"])


@pytest.mark.asyncio
async def test_refresh_concurrent_invocation_returns_in_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(ms, "TRIGGER_FILE", tmp_path / "trigger")
    _cache_clear_lock()

    block_event = asyncio.Event()
    proceed_event = asyncio.Event()

    async def slow_list_domains():
        proceed_event.set()
        await block_event.wait()
        return []

    monkeypatch.setattr(ms, "list_mail_domains", slow_list_domains)

    task1 = asyncio.create_task(refresh())
    await proceed_event.wait()
    # While task1 holds the lock, second call returns immediately
    result2 = await refresh()
    assert result2 == {"status": "in_progress"}
    block_event.set()
    await task1


@pytest.mark.asyncio
async def test_refresh_empty_domain_handled(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(ms, "TRIGGER_FILE", tmp_path / "trigger")
    async def fake_list_domains(): return ["empty.com"]
    async def fake_list_mailboxes(d): return []
    monkeypatch.setattr(ms, "list_mail_domains", fake_list_domains)
    monkeypatch.setattr(ms, "list_mailboxes", fake_list_mailboxes)
    _cache_clear_lock()
    result = await refresh()
    assert result["domain_count"] == 1
    assert result["mailbox_count"] == 0
    assert result["domains"]["empty.com"]["mailboxes"] == []


def test_grep_dovecot_returns_none_on_missing_log(monkeypatch):
    monkeypatch.setattr(ms, "DOVECOT_LOG_PATH", "/nonexistent/path.log")
    assert ms._grep_dovecot_last_login("any@x.com") is None
