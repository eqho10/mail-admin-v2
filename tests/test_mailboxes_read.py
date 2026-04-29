import json
from pathlib import Path
import pytest


def test_mailboxes_page_requires_auth(client):
    r = client.get("/mailboxes")
    assert r.status_code in (401, 303)


def test_mailboxes_page_renders_when_authed(authed_client, monkeypatch, tmp_path):
    """Stats JSON missing → page shows 'first refresh' banner."""
    import services.mailbox_stats as ms
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "missing.json")

    async def fake_list_domains(): return ["bilgeworld.com"]
    monkeypatch.setattr("services.hestia.list_mail_domains", fake_list_domains)
    r = authed_client.get("/mailboxes")
    assert r.status_code == 200
    assert "first refresh" in r.text.lower() or "ilk yenileme" in r.text.lower()


def test_mailboxes_page_renders_with_stats(authed_client, monkeypatch, tmp_path):
    import services.mailbox_stats as ms
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps({
        "refreshed_at": "2026-04-29T14:00:00+00:00",
        "duration_sec": 1.0, "domain_count": 1, "mailbox_count": 1,
        "errors": [],
        "domains": {
            "bilgeworld.com": {"mailboxes": [{
                "email": "ekrem@bilgeworld.com", "user": "ekrem",
                "quota_mb": 1024, "used_mb": 200, "status": "active",
                "created_at": "2026-01-10", "alias_count": 2,
                "last_login": "2026-04-29T13:00:00Z", "disk_size_mb": 240,
            }]}
        }
    }))
    monkeypatch.setattr(ms, "STATS_JSON_PATH", stats_file)
    async def fake_list_domains(): return ["bilgeworld.com"]
    monkeypatch.setattr("services.hestia.list_mail_domains", fake_list_domains)

    r = authed_client.get("/mailboxes?domain=bilgeworld.com")
    assert r.status_code == 200
    assert "ekrem@bilgeworld.com" in r.text


def test_mailboxes_api_list_returns_json(authed_client, monkeypatch, tmp_path):
    import services.mailbox_stats as ms
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps({
        "refreshed_at": "2026-04-29T14:00:00+00:00",
        "duration_sec": 1.0, "domain_count": 1, "mailbox_count": 2,
        "errors": [],
        "domains": {
            "x.com": {"mailboxes": [
                {"email": "a@x.com", "user": "a", "quota_mb": 1024, "used_mb": 0,
                 "status": "active", "created_at": "", "alias_count": 0,
                 "last_login": None, "disk_size_mb": None},
                {"email": "info@x.com", "user": "info", "quota_mb": 2048, "used_mb": 100,
                 "status": "active", "created_at": "", "alias_count": 1,
                 "last_login": None, "disk_size_mb": None},
            ]}
        }
    }))
    monkeypatch.setattr(ms, "STATS_JSON_PATH", stats_file)
    r = authed_client.get("/mailboxes/api/list?domain=x.com&q=info")
    assert r.status_code == 200
    body = r.json()
    assert len(body["mailboxes"]) == 1
    assert body["mailboxes"][0]["email"] == "info@x.com"
    assert body["domain"] == "x.com"
