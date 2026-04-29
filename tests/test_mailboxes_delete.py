import re


def _csrf(authed_client, page="/aktivite"):
    r = authed_client.get(page)
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


def test_delete_requires_csrf(authed_client):
    authed_client.headers.pop("X-CSRF-Token", None)  # pop default fixture header
    r = authed_client.post("/mailboxes/delete", data={"domain": "x.com", "user": "t"})
    assert r.status_code == 403


def test_delete_rejects_when_expected_email_mismatch(authed_client, monkeypatch):
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "delete_mailbox",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")))
    r = authed_client.post("/mailboxes/delete", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test",
        "expected_email": "test@x.com",
        "confirmation_typed": "wrong@x.com",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_delete_calls_hestia_when_match(authed_client, monkeypatch):
    called = {}
    def fake_delete(domain, user):
        called["args"] = (domain, user)
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "delete_mailbox", fake_delete)

    r = authed_client.post("/mailboxes/delete", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test",
        "expected_email": "test@x.com",
        "confirmation_typed": "test@x.com",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert called["args"] == ("x.com", "test")


def test_delete_writes_rich_audit(authed_client, monkeypatch, tmp_path):
    import services.hestia as hestia
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    monkeypatch.setattr(hestia, "delete_mailbox", lambda *a, **kw: None)
    import services.mailbox_stats as ms
    stats_file = tmp_path / "stats.json"
    import json
    stats_file.write_text(json.dumps({
        "refreshed_at": "2026-04-29T14:00:00+00:00",
        "duration_sec": 0.0, "domain_count": 1, "mailbox_count": 1, "errors": [],
        "domains": {"x.com": {"mailboxes": [{
            "email": "test@x.com", "user": "test", "quota_mb": 1024, "used_mb": 200,
            "status": "active", "created_at": "", "alias_count": 2,
            "last_login": None, "disk_size_mb": 250,
        }]}}
    }))
    monkeypatch.setattr(ms, "STATS_JSON_PATH", stats_file)

    authed_client.post("/mailboxes/delete", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test",
        "expected_email": "test@x.com",
        "confirmation_typed": "test@x.com",
    }, follow_redirects=False)
    log = audit_file.read_text()
    assert "mailbox.delete" in log
    assert "test@x.com" in log
    assert "250" in log or "alias_count" in log
