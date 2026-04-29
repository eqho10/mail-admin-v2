import re
import pytest


def _csrf(authed_client, page="/aktivite"):
    r = authed_client.get(page)
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


def test_create_requires_csrf(authed_client):
    # Pop the default CSRF header set by conftest fixture, to test rejection path
    authed_client.headers.pop("X-CSRF-Token", None)
    r = authed_client.post("/mailboxes/create", data={"domain": "x.com", "email_local": "a"})
    assert r.status_code == 403


def test_create_calls_add_mailbox(authed_client, monkeypatch):
    calls = {}
    def fake_add(domain, user, password, quota_mb):
        calls["args"] = (domain, user, password, quota_mb)
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "add_mailbox", fake_add)

    r = authed_client.post("/mailboxes/create", data={
        "csrf_token": _csrf(authed_client),
        "domain": "bilgeworld.com",
        "email_local": "test",
        "password": "VeryStrongPass#123!",
        "password_confirm": "VeryStrongPass#123!",
        "quota_mb": "1024",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert calls["args"][0] == "bilgeworld.com"
    assert calls["args"][1] == "test"
    assert calls["args"][3] == 1024


def test_create_password_mismatch_rejected(authed_client, monkeypatch):
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "add_mailbox", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")))
    r = authed_client.post("/mailboxes/create", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "email_local": "t",
        "password": "VeryStrongPass#123!", "password_confirm": "different",
        "quota_mb": "1024",
    }, follow_redirects=False)
    assert r.status_code in (303, 400)
    body = r.text + r.headers.get("location", "")
    assert "password" in body.lower() or "şifre" in body.lower() or "match" in body.lower() or "eşleş" in body.lower() or "mismatch" in body.lower()


def test_create_calls_aliases_when_provided(authed_client, monkeypatch):
    captured_aliases = []
    def fake_add(*a, **kw): pass
    def fake_alias(domain, user, alias_local):
        captured_aliases.append(alias_local)
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "add_mailbox", fake_add)
    monkeypatch.setattr(hestia, "add_alias", fake_alias)

    authed_client.post("/mailboxes/create", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "email_local": "t",
        "password": "VeryStrongPass#123!", "password_confirm": "VeryStrongPass#123!",
        "quota_mb": "1024",
        "aliases": "info, support, sales",
    }, follow_redirects=False)
    assert set(captured_aliases) == {"info", "support", "sales"}


def test_create_writes_audit_log(authed_client, monkeypatch, tmp_path):
    import services.hestia as hestia
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    monkeypatch.setattr(hestia, "add_mailbox", lambda *a, **kw: None)
    authed_client.post("/mailboxes/create", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "email_local": "t",
        "password": "VeryStrongPass#123!", "password_confirm": "VeryStrongPass#123!",
        "quota_mb": "1024",
    }, follow_redirects=False)
    text = audit_file.read_text()
    assert "mailbox.create" in text
    assert "x.com" in text


def test_create_translates_hestia_error(authed_client, monkeypatch):
    import services.hestia as hestia
    def boom(*a, **kw):
        raise hestia.HestiaCLIError(
            translated={"id": "hestia_user_exists", "title": "exists",
                        "body": "Bu kullanıcı zaten var.", "severity": "warning"},
            raw_stderr="Error: mail account t exists",
        )
    monkeypatch.setattr(hestia, "add_mailbox", boom)
    r = authed_client.post("/mailboxes/create", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "email_local": "t",
        "password": "VeryStrongPass#123!", "password_confirm": "VeryStrongPass#123!",
        "quota_mb": "1024",
    }, follow_redirects=False)
    assert r.status_code in (303, 400)
    loc = r.headers.get("location", "") + r.text
    assert "hestia_user_exists" in loc or "zaten var" in loc
