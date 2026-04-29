import re


def _csrf(authed_client, page="/aktivite"):
    r = authed_client.get(page)
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


def test_suppression_page_requires_auth(client):
    r = client.get("/suppression")
    assert r.status_code in (401, 303)


def test_suppression_page_renders(authed_client, monkeypatch):
    from services.brevo_suppression import Block
    async def fake_list(category="all", limit=100, offset=0):
        return [Block(email="a@b.com", reason="hard_bounce", blocked_at="2026-04-29T12:00:00Z")]
    monkeypatch.setattr("services.brevo_suppression.list_blocked", fake_list)
    r = authed_client.get("/suppression")
    assert r.status_code == 200
    assert "a@b.com" in r.text


def test_suppression_api_list_returns_json(authed_client, monkeypatch):
    from services.brevo_suppression import Block
    async def fake_list(category="all", limit=100, offset=0):
        return [
            Block(email="a@b.com", reason="hard_bounce", blocked_at=""),
            Block(email="info@c.com", reason="hard_bounce", blocked_at=""),
        ]
    monkeypatch.setattr("services.brevo_suppression.list_blocked", fake_list)
    r = authed_client.get("/suppression/api/list?category=hard&q=info")
    assert r.status_code == 200
    body = r.json()
    assert len(body["blocks"]) == 1
    assert body["blocks"][0]["email"] == "info@c.com"
    assert body["category"] == "hard"


def test_suppression_remove_csrf(authed_client):
    authed_client.headers.pop("X-CSRF-Token", None)  # pop default fixture header
    r = authed_client.post("/suppression/remove", data={"email": "a@b.com"})
    assert r.status_code == 403


def test_suppression_remove_calls_service_and_audits(authed_client, monkeypatch, tmp_path):
    called = {}
    async def fake_remove(email):
        called["email"] = email
    monkeypatch.setattr("services.brevo_suppression.remove_from_suppression", fake_remove)
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)
    r = authed_client.post("/suppression/remove", data={
        "csrf_token": _csrf(authed_client),
        "email": "a@b.com",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert called["email"] == "a@b.com"
    assert "suppression.remove" in audit_file.read_text()
