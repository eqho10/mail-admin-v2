"""Faz 4a Task 15 — suppression.html full UI render testleri.

6 sekme (all/hard/soft/blocked/unsub/spam), satır başına Kaldır butonu (JS confirm),
Brevo API hatası → banner. Authed_client kullanılır; route /suppression Task 13'te
tanımlı, Task 15 template'i full UI'a çıkardı."""


def test_suppression_template_renders_6_tabs(authed_client, monkeypatch):
    async def fake_list(category="all", limit=100, offset=0): return []
    monkeypatch.setattr("services.brevo_suppression.list_blocked", fake_list)
    r = authed_client.get("/suppression")
    text = r.text.lower()
    for tab in ["all", "hard", "soft", "blocked", "unsub", "spam"]:
        assert tab in text


def test_suppression_template_shows_remove_button(authed_client, monkeypatch):
    from services.brevo_suppression import Block
    async def fake_list(category="all", limit=100, offset=0):
        return [Block(email="a@b.com", reason="hard_bounce", blocked_at="")]
    monkeypatch.setattr("services.brevo_suppression.list_blocked", fake_list)
    r = authed_client.get("/suppression")
    assert "a@b.com" in r.text
    assert "remove" in r.text.lower() or "kaldır" in r.text.lower() or "sil" in r.text.lower()


def test_suppression_template_shows_api_error(authed_client, monkeypatch):
    from services.brevo_suppression import BrevoSuppressionError
    async def fake_list(category="all", limit=100, offset=0):
        raise BrevoSuppressionError("Brevo API connection refused")
    monkeypatch.setattr("services.brevo_suppression.list_blocked", fake_list)
    r = authed_client.get("/suppression")
    assert r.status_code == 200
    assert "brevo" in r.text.lower()
