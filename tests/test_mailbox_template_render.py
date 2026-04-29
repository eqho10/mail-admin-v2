"""Faz 4a Task 14 — mailboxes.html full UI render testleri.

Authed_client kullanıyor; route /mailboxes (plural) Task 8'de tanımlı,
Task 14 bu route'un template'ini full UI'a çıkardı. Legacy /mailbox
(singular) ve smoke test'lerin breakable olup olmadığı suite genelinde
ayrıca doğrulanır."""

import json


def test_full_template_renders_8_columns(authed_client, monkeypatch, tmp_path):
    import services.mailbox_stats as ms
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps({
        "refreshed_at": "2026-04-29T14:00:00+00:00",
        "duration_sec": 1.0, "domain_count": 1, "mailbox_count": 1,
        "errors": [],
        "domains": {"x.com": {"mailboxes": [{
            "email": "ekrem@x.com", "user": "ekrem", "quota_mb": 1024,
            "used_mb": 200, "status": "active", "created_at": "2026-01-10T10:00:00Z",
            "alias_count": 2, "last_login": "2026-04-29T13:00:00Z", "disk_size_mb": 240,
        }]}}
    }))
    monkeypatch.setattr(ms, "STATS_JSON_PATH", stats_file)
    async def fake_list_domains(): return ["x.com"]
    monkeypatch.setattr("services.hestia.list_mail_domains", fake_list_domains)

    r = authed_client.get("/mailboxes?domain=x.com")
    assert r.status_code == 200
    text = r.text
    expected = ["Email", "Quota", "Status", "Created", "Alias", "login", "Disk", "Action"]
    expected_tr = ["E-posta", "Quota", "Durum", "Oluştur", "Alias", "giriş", "Disk", "İşlem"]
    found_eng = [k for k in expected if k.lower() in text.lower()]
    found_tr = [k for k in expected_tr if k.lower() in text.lower()]
    assert len(found_eng) >= 6 or len(found_tr) >= 6, "Not enough column headers"
    assert "ekrem@x.com" in text
    assert "1024" in text or "1 GB" in text


def test_template_shows_domain_sidebar(authed_client, monkeypatch, tmp_path):
    import services.mailbox_stats as ms
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps({
        "refreshed_at": "2026-04-29T14:00:00+00:00",
        "duration_sec": 1.0, "domain_count": 3, "mailbox_count": 0, "errors": [],
        "domains": {
            "a.com": {"mailboxes": []},
            "b.com": {"mailboxes": []},
            "c.com": {"mailboxes": []},
        }
    }))
    monkeypatch.setattr(ms, "STATS_JSON_PATH", stats_file)
    async def fake_list_domains(): return ["a.com", "b.com", "c.com"]
    monkeypatch.setattr("services.hestia.list_mail_domains", fake_list_domains)
    r = authed_client.get("/mailboxes")
    assert r.status_code == 200
    assert "a.com" in r.text
    assert "b.com" in r.text
    assert "c.com" in r.text


def test_template_first_refresh_banner(authed_client, monkeypatch, tmp_path):
    import services.mailbox_stats as ms
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "missing.json")
    async def fake_list_domains(): return ["x.com"]
    monkeypatch.setattr("services.hestia.list_mail_domains", fake_list_domains)
    r = authed_client.get("/mailboxes")
    assert r.status_code == 200
    assert "first refresh" in r.text.lower() or "ilk yenileme" in r.text.lower()


def test_template_includes_create_modal(authed_client, monkeypatch, tmp_path):
    import services.mailbox_stats as ms
    monkeypatch.setattr(ms, "STATS_JSON_PATH", tmp_path / "missing.json")
    async def fake_list_domains(): return ["x.com"]
    monkeypatch.setattr("services.hestia.list_mail_domains", fake_list_domains)
    r = authed_client.get("/mailboxes")
    text = r.text.lower()
    assert "new mailbox" in text or "yeni" in text
    assert 'name="email_local"' in r.text or 'name="email_local"' in r.text
