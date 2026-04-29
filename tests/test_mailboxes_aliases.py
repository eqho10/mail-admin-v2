import re


def _csrf(authed_client, page="/aktivite"):
    r = authed_client.get(page)
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


def test_alias_add_calls_hestia(authed_client, monkeypatch):
    called = {}
    def fake_add(domain, user, alias_local):
        called["args"] = (domain, user, alias_local)
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "add_alias", fake_add)
    r = authed_client.post("/mailboxes/aliases/add", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "alias_local": "info",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert called["args"] == ("x.com", "test", "info")


def test_alias_remove_calls_hestia(authed_client, monkeypatch):
    called = {}
    def fake_del(domain, user, alias_local):
        called["args"] = (domain, user, alias_local)
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "delete_alias", fake_del)
    r = authed_client.post("/mailboxes/aliases/remove", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "alias_local": "info",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert called["args"] == ("x.com", "test", "info")


def test_alias_add_writes_audit(authed_client, monkeypatch, tmp_path):
    import services.hestia as hestia
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    monkeypatch.setattr(hestia, "add_alias", lambda *a: None)
    authed_client.post("/mailboxes/aliases/add", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "alias_local": "info",
    }, follow_redirects=False)
    assert "mailbox.alias_add" in audit_file.read_text()


def test_alias_remove_writes_audit(authed_client, monkeypatch, tmp_path):
    import services.hestia as hestia
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    monkeypatch.setattr(hestia, "delete_alias", lambda *a: None)
    authed_client.post("/mailboxes/aliases/remove", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "alias_local": "info",
    }, follow_redirects=False)
    assert "mailbox.alias_remove" in audit_file.read_text()
