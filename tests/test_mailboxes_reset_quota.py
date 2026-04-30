import re


def _csrf(authed_client, page="/aktivite"):
    r = authed_client.get(page)
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


def test_reset_password_manual_mode(authed_client, monkeypatch):
    called = {}
    def fake_chg(domain, user, password):
        called["args"] = (domain, user, password)
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_password", fake_chg)
    r = authed_client.post("/mailboxes/reset-password", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test",
        "mode": "manual",
        "password": "NewStrongPass#456!",
        "password_confirm": "NewStrongPass#456!",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert called["args"] == ("x.com", "test", "NewStrongPass#456!")


def test_reset_password_generate_mode_creates_random(authed_client, monkeypatch):
    captured = {}
    def fake_chg(domain, user, password):
        captured["pwd"] = password
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_password", fake_chg)
    r = authed_client.post("/mailboxes/reset-password", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test",
        "mode": "generate",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert len(captured["pwd"]) >= 24
    # Faz 4a Fix 3: generated password is delivered via signed flash cookie
    # (`ma_flash`), NOT via the URL query string. Verify Location header is
    # password-free and the flash cookie was set.
    loc = r.headers["location"]
    assert "generated_password=" not in loc
    assert captured["pwd"] not in loc
    assert any(h.startswith("ma_flash=") for h in r.headers.get_list("set-cookie")), (
        "Generated password flow must set ma_flash cookie."
    )


def test_reset_password_manual_mismatch(authed_client, monkeypatch):
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_password", lambda *a: (_ for _ in ()).throw(AssertionError("nope")))
    r = authed_client.post("/mailboxes/reset-password", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "mode": "manual",
        "password": "NewStrongPass#456!", "password_confirm": "diff",
    }, follow_redirects=False)
    assert r.status_code in (303, 400)


def test_reset_password_writes_audit(authed_client, monkeypatch, tmp_path):
    import services.hestia as hestia
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    monkeypatch.setattr(hestia, "change_password", lambda *a: None)
    authed_client.post("/mailboxes/reset-password", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "mode": "generate",
    }, follow_redirects=False)
    assert "mailbox.password_reset" in audit_file.read_text()


def test_change_quota_calls_hestia(authed_client, monkeypatch):
    called = {}
    def fake_q(domain, user, quota_mb):
        called["args"] = (domain, user, quota_mb)
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_quota", fake_q)
    r = authed_client.post("/mailboxes/change-quota", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "quota_mb": "2048",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert called["args"] == ("x.com", "test", 2048)


def test_change_quota_writes_audit(authed_client, monkeypatch, tmp_path):
    import services.hestia as hestia
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    monkeypatch.setattr(hestia, "change_quota", lambda *a: None)
    authed_client.post("/mailboxes/change-quota", data={
        "csrf_token": _csrf(authed_client),
        "domain": "x.com", "user": "test", "quota_mb": "2048",
    }, follow_redirects=False)
    assert "mailbox.quota_change" in audit_file.read_text()
