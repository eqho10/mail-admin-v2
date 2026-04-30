"""Tests for routers/quarantine.py — Faz 4b Task 9.

Covers:
  - GET  /quarantine            page render + auth gate
  - GET  /quarantine/api/list   frozen / rejected / unknown source / pagination
  - GET  /quarantine/api/raw/{msgid}   not_found / truncated / invalid msgid
  - POST /quarantine/release    happy path + invalid msgid
  - POST /quarantine/delete     mismatched confirmation / matched confirmation
"""
from __future__ import annotations

import pytest

from services import exim as exim_svc
from services import brevo_suppression


# ============================ Page render ============================

def test_quarantine_page_renders(authed_client, monkeypatch):
    monkeypatch.setattr(exim_svc, "exim_queue_list", lambda: [])
    monkeypatch.setattr(exim_svc, "read_tail", lambda p, n: [])

    async def fake_brevo(*a, **kw):
        return []
    monkeypatch.setattr(brevo_suppression, "list_blocked", fake_brevo)

    r = authed_client.get("/quarantine")
    assert r.status_code == 200
    assert "Quarantine" in r.text


def test_quarantine_page_requires_auth(client):
    r = client.get("/quarantine", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


# ============================ /api/list ============================

def test_quarantine_api_list_frozen(authed_client, monkeypatch):
    monkeypatch.setattr(exim_svc, "exim_queue_list", lambda: [
        {"msgid": "AAA", "frozen": True, "from": "a@b.com", "to": ["c@d.com"], "size": "1K", "age": "1h"},
        {"msgid": "BBB", "frozen": False, "from": "x@y.com", "to": ["z@w.com"], "size": "2K", "age": "2h"},
    ])
    monkeypatch.setattr(exim_svc, "read_tail", lambda p, n: [])

    r = authed_client.get("/quarantine/api/list?source=frozen")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "frozen"
    assert len(body["items"]) == 1
    assert body["items"][0]["msgid"] == "AAA"
    assert body["items"][0]["from"] == "a@b.com"


def test_quarantine_api_list_rejected(authed_client, monkeypatch):
    monkeypatch.setattr(exim_svc, "exim_queue_list", lambda: [])
    monkeypatch.setattr(exim_svc, "read_tail", lambda p, n: [
        '2026-04-29 14:00:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: bad',
    ])
    r = authed_client.get("/quarantine/api/list?source=rejected")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "rejected"
    assert len(body["items"]) == 1
    assert body["items"][0]["ip"] == "1.2.3.4"


def test_api_list_unknown_source_returns_400(authed_client):
    r = authed_client.get("/quarantine/api/list?source=garbage")
    assert r.status_code == 400


def test_api_list_pagination_limits_items(authed_client, monkeypatch):
    monkeypatch.setattr(exim_svc, "exim_queue_list", lambda: [
        {"msgid": f"M{i}", "frozen": True, "from": "", "to": [], "size": "", "age": ""}
        for i in range(75)
    ])
    monkeypatch.setattr(exim_svc, "read_tail", lambda p, n: [])

    r = authed_client.get("/quarantine/api/list?source=frozen&limit=20&offset=0")
    body = r.json()
    assert len(body["items"]) == 20
    assert body["total"] == 75
    assert body["next_offset"] == 20


# ============================ POST /release ============================

def test_release_calls_exim_Mt(authed_client, monkeypatch):
    calls = []
    def fake_release(mid):
        calls.append(mid)
        return (0, "released", "")
    monkeypatch.setattr(exim_svc, "exim_release_msg", fake_release)

    r = authed_client.post("/quarantine/release", data={"msgid": "1wEk9N"})
    assert r.status_code == 200
    assert "1wEk9N" in calls
    body = r.json()
    assert body["ok"] is True
    assert body["msgid"] == "1wEk9N"


def test_release_rejects_invalid_msgid(authed_client):
    r = authed_client.post("/quarantine/release", data={"msgid": "../etc/passwd"})
    assert r.status_code == 422


# ============================ POST /delete ============================

def test_delete_requires_confirmation_match(authed_client, monkeypatch):
    called = []
    def _capture(mid):
        called.append(mid)
        return (0, "", "")
    monkeypatch.setattr(exim_svc, "exim_delete_msg", _capture)

    r = authed_client.post(
        "/quarantine/delete",
        data={"msgid": "AAA", "confirmation_typed": "BBB"},
    )
    assert r.status_code == 422
    assert called == []


def test_delete_succeeds_with_match(authed_client, monkeypatch):
    monkeypatch.setattr(exim_svc, "exim_delete_msg", lambda mid: (0, "", ""))
    r = authed_client.post(
        "/quarantine/delete",
        data={"msgid": "AAA", "confirmation_typed": "AAA"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


# ============================ /api/raw ============================

def test_api_raw_returns_404_when_not_found(authed_client, monkeypatch):
    monkeypatch.setattr(
        exim_svc, "exim_view_msg",
        lambda mid: {"not_found": True, "error": "spool: message AAA not found"},
    )
    r = authed_client.get("/quarantine/api/raw/AAA")
    assert r.status_code == 404


def test_api_raw_returns_truncated_flag(authed_client, monkeypatch):
    monkeypatch.setattr(
        exim_svc, "exim_view_msg",
        lambda mid: {"headers": "From: a", "body": "x" * 100, "truncated": True},
    )
    r = authed_client.get("/quarantine/api/raw/AAA")
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert body["headers"] == "From: a"


def test_api_raw_rejects_invalid_msgid(authed_client):
    # URL-encoded "../etc/passwd" — the router regex must reject it
    r = authed_client.get("/quarantine/api/raw/..%2Fetc%2Fpasswd")
    # Either 422 (we matched the path then validated) or 404 (Starlette
    # routing wouldn't match if it interprets %2F differently).
    assert r.status_code in (422, 404)
