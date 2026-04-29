"""Sendas router endpoints."""
import json
from pathlib import Path
from unittest.mock import MagicMock


def test_dispatch_endpoint_returns_run_id(authed_client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.sendas._RUNS_DIR", tmp_path)
    monkeypatch.setattr("services.mailboxes.list_all",
                        lambda: ["info@bilgeworld.com"])

    fake_run = MagicMock(returncode=0, stdout=b"")
    monkeypatch.setattr("services.sendas.subprocess.run", lambda *a, **kw: fake_run)
    monkeypatch.setattr("services.sendas._extract_msgid", lambda *a: "1abc-def-01")

    r = authed_client.post("/api/sendas/dispatch", json={
        "from": "info@bilgeworld.com",
        "to": "someone@example.com",
        "subject": "test",
        "body": "hi",
    })
    assert r.status_code == 200
    body = r.json()
    assert "run_id" in body
    assert body["msgid"] == "1abc-def-01"
    assert body["status"] == "sent"


def test_dispatch_400_on_invalid_from(authed_client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.sendas._RUNS_DIR", tmp_path)
    monkeypatch.setattr("services.mailboxes.list_all", lambda: ["info@bilgeworld.com"])

    r = authed_client.post("/api/sendas/dispatch", json={
        "from": "attacker@evil.com",
        "to": "someone@example.com",
    })
    assert r.status_code == 400


def test_poll_returns_state(authed_client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.sendas._RUNS_DIR", tmp_path)
    state = {"run_id": "r1", "status": "sent", "msgid": "1abc"}
    (tmp_path / "r1.json").write_text(json.dumps(state))

    r = authed_client.get("/api/sendas/poll/r1")
    assert r.status_code == 200
    assert r.json()["status"] == "sent"


def test_poll_404_unknown(authed_client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.sendas._RUNS_DIR", tmp_path)
    r = authed_client.get("/api/sendas/poll/nonexistent")
    assert r.status_code == 404
