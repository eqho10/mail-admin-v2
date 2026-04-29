import pytest
import services.mailbox_stats as ms


def test_refresh_mailbox_stats_requires_token(client, monkeypatch):
    monkeypatch.setenv("MAILBOX_STATS_CRON_TOKEN", "secret-test-99")
    r = client.post("/cron/refresh-mailbox-stats")
    assert r.status_code == 401


def test_refresh_mailbox_stats_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setenv("MAILBOX_STATS_CRON_TOKEN", "secret-test-99")
    r = client.post("/cron/refresh-mailbox-stats", headers={"X-Cron-Token": "wrong"})
    assert r.status_code == 401


def test_refresh_mailbox_stats_with_valid_token_runs(client, monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBOX_STATS_CRON_TOKEN", "secret-test-99")
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(ms, "TRIGGER_FILE", tmp_path / "trigger")

    async def fake_list_domains(): return []
    monkeypatch.setattr(ms, "list_mail_domains", fake_list_domains)
    ms._cache_clear_lock()
    r = client.post("/cron/refresh-mailbox-stats", headers={"X-Cron-Token": "secret-test-99"})
    assert r.status_code == 200
    body = r.json()
    assert body["domain_count"] == 0
    assert body["mailbox_count"] == 0


def test_refresh_mailbox_stats_concurrent_returns_in_progress(client, monkeypatch, tmp_path):
    """Second concurrent request returns 200 with status=in_progress (not 5xx)."""
    import asyncio
    monkeypatch.setenv("MAILBOX_STATS_CRON_TOKEN", "secret-test-99")
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(ms, "TRIGGER_FILE", tmp_path / "trigger")
    ms._cache_clear_lock()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(ms._refresh_lock.acquire())
        r = client.post("/cron/refresh-mailbox-stats", headers={"X-Cron-Token": "secret-test-99"})
        assert r.status_code == 200
        assert r.json() == {"status": "in_progress"}
    finally:
        ms._refresh_lock.release()
        loop.close()
