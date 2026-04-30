"""Faz 4a hardening — Fix 2-6 tests.

Fix 2: domain regex validated at handler entry → 422 on bad input
Fix 3: generated password delivered via signed flash cookie, not URL query
Fix 4: `mailbox.create_failed` audit event on hestia failure path
Fix 5: no inline JS interpolation of user data (no `onsubmit="confirm('{{...}}')"`)
Fix 6: /mailboxes/api/list and /suppression/api/list return total + next_offset
"""
import json
import re
from pathlib import Path

import pytest


def _csrf(authed_client, page="/aktivite"):
    r = authed_client.get(page)
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
    return m.group(1) if m else ""


# ============================================================================
# Fix 2 — RE_DOMAIN handler-entry validation
# ============================================================================

def test_create_rejects_invalid_domain_format(authed_client, monkeypatch):
    """Bad domain → 422; hestia.add_mailbox is NEVER called."""
    import services.hestia as hestia
    sentinel = {"called": False}
    def boom(*a, **kw):
        sentinel["called"] = True
    monkeypatch.setattr(hestia, "add_mailbox", boom)

    r = authed_client.post(
        "/mailboxes/create",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "Not A Valid Domain!!!",  # spaces + uppercase + bangs
            "email_local": "test",
            "password": "VeryStrongPass#123!",
            "password_confirm": "VeryStrongPass#123!",
            "quota_mb": "1024",
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert sentinel["called"] is False, "hestia.add_mailbox should not be reached on bad domain"


def test_change_quota_rejects_invalid_domain(authed_client, monkeypatch):
    import services.hestia as hestia
    sentinel = {"called": False}
    def boom(*a, **kw):
        sentinel["called"] = True
    monkeypatch.setattr(hestia, "change_quota", boom)

    r = authed_client.post(
        "/mailboxes/change-quota",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "Bad Domain!",
            "user": "test",
            "quota_mb": "2048",
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert sentinel["called"] is False


def test_alias_add_rejects_invalid_domain(authed_client, monkeypatch):
    import services.hestia as hestia
    sentinel = {"called": False}
    def boom(*a, **kw):
        sentinel["called"] = True
    monkeypatch.setattr(hestia, "add_alias", boom)

    r = authed_client.post(
        "/mailboxes/aliases/add",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "BAD DOMAIN!!!",
            "user": "test",
            "alias_local": "info",
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert sentinel["called"] is False


def test_delete_rejects_invalid_domain(authed_client, monkeypatch):
    import services.hestia as hestia
    sentinel = {"called": False}
    def boom(*a, **kw):
        sentinel["called"] = True
    monkeypatch.setattr(hestia, "delete_mailbox", boom)

    r = authed_client.post(
        "/mailboxes/delete",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "BAD!!!",
            "user": "test",
            "expected_email": "test@x.com",
            "confirmation_typed": "test@x.com",
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert sentinel["called"] is False


# ============================================================================
# Fix 3 — generated password via flash cookie (not URL)
# ============================================================================

def test_reset_password_generate_does_not_leak_in_url(authed_client, monkeypatch):
    """Generated password must NOT appear in redirect Location header."""
    import services.hestia as hestia
    captured = {}
    def fake_chg(domain, user, password):
        captured["pwd"] = password
    monkeypatch.setattr(hestia, "change_password", fake_chg)

    r = authed_client.post(
        "/mailboxes/reset-password",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "user": "test",
            "mode": "generate",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    pwd = captured.get("pwd", "")
    assert pwd  # sanity: password was actually generated
    location = r.headers.get("location", "")
    assert "generated_password=" not in location, (
        f"Generated password must not appear in URL. Location was: {location!r}"
    )
    assert pwd not in location, (
        f"Raw password text leaked into redirect URL: {location!r}"
    )


def test_reset_password_generate_sets_flash_cookie(authed_client, monkeypatch):
    """Generated password is delivered via signed flash cookie.

    Flash signer falls back to SESSION_SECRET="dev-only-change-me" when env
    is unset (matches services/templates.py + app.py defaults), so this test
    works without per-test env wiring.
    """
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_password", lambda *a, **kw: None)

    r = authed_client.post(
        "/mailboxes/reset-password",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "user": "test",
            "mode": "generate",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Flash cookie must be set on the redirect response
    set_cookie_headers = [h for h in r.headers.get_list("set-cookie")
                          if h.startswith("ma_flash=")]
    assert set_cookie_headers, (
        f"Expected ma_flash cookie to be set on /mailboxes/reset-password "
        f"redirect for mode=generate. Got cookies: {r.headers.get_list('set-cookie')}"
    )


def test_reset_password_manual_does_not_set_flash(authed_client, monkeypatch):
    """Manual mode (user provided their own password) does NOT trigger the flash."""
    import services.hestia as hestia
    monkeypatch.setattr(hestia, "change_password", lambda *a, **kw: None)

    r = authed_client.post(
        "/mailboxes/reset-password",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "user": "test",
            "mode": "manual",
            "password": "NewStrongPass#456!",
            "password_confirm": "NewStrongPass#456!",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert not any(h.startswith("ma_flash=") for h in r.headers.get_list("set-cookie")), (
        "Manual reset should not set ma_flash cookie."
    )


# ============================================================================
# Fix 4 — audit on failed creation
# ============================================================================

def test_create_failure_emits_audit_event(authed_client, monkeypatch, tmp_path):
    """When hestia.add_mailbox raises, we still emit `mailbox.create_failed`."""
    import services.hestia as hestia
    import services.audit as audit_mod
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", audit_file)

    def boom(*a, **kw):
        raise hestia.HestiaCLIError(
            translated={
                "id": "hestia_user_exists",
                "title": "exists",
                "body": "Bu kullanıcı zaten var.",
                "severity": "warning",
            },
            raw_stderr="Error: mail account exists",
        )
    monkeypatch.setattr(hestia, "add_mailbox", boom)

    r = authed_client.post(
        "/mailboxes/create",
        data={
            "csrf_token": _csrf(authed_client),
            "domain": "x.com",
            "email_local": "alreadyexists",
            "password": "VeryStrongPass#123!",
            "password_confirm": "VeryStrongPass#123!",
            "quota_mb": "1024",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    log = audit_file.read_text()
    assert "mailbox.create_failed" in log, (
        f"Expected audit `mailbox.create_failed` after HestiaCLIError. "
        f"Audit contents: {log!r}"
    )
    assert "hestia_user_exists" in log, (
        "Audit entry should include the translated error id for forensics."
    )
    # Success event must NOT be present
    assert "\"event\": \"mailbox.create\"" not in log.replace("create_failed", ""), (
        "On failure, do not emit the success-path `mailbox.create` event."
    )


# ============================================================================
# Fix 5 — XSS hardening: no inline JS user-data interpolation
# ============================================================================

_TEMPLATES_DIR = Path("/root/mail-admin-v2/templates/pages")


def test_mailboxes_html_no_onsubmit_confirm():
    """No onsubmit="confirm('{{ ... }}')" patterns in mailboxes.html."""
    html = (_TEMPLATES_DIR / "mailboxes.html").read_text()
    # Pattern: onsubmit="...{{ ... }}..." or onclick="...{{ ... }}..."
    assert 'onsubmit="confirm(' not in html, (
        "Inline confirm() with template interpolation is an XSS vector. "
        "Move user data to data-* attrs and use addEventListener delegation."
    )
    # Also flag any onclick that interpolates Jinja directly into a JS string.
    # Find handlers that mix `onclick=` / `onsubmit=` with `{{ ... }}` literally
    # inside the attr value.
    for handler in ("onclick", "onsubmit"):
        pattern = re.compile(rf'{handler}="[^"]*\{{\{{[^"]*"')
        bad = pattern.findall(html)
        # Allow `m | tojson` since Jinja autoescapes and tojson encodes safely
        # for inline JS — but plain `{{ b.email }}` inside a JS string is bad.
        for match in bad:
            if "| tojson" in match or "|tojson" in match:
                continue
            # confirm() / alert() with raw interpolation = XSS hole
            if "confirm(" in match or "alert(" in match:
                pytest.fail(
                    f"XSS-prone inline JS handler in mailboxes.html: {match!r}"
                )


def test_suppression_html_no_onsubmit_confirm():
    """No onsubmit="confirm('{{ b.email }}')" in suppression.html.

    The original Faz 4a code interpolated `{{ b.email }}` directly into a
    JS string inside an inline onsubmit handler — a real XSS vector if a
    Brevo response ever contains an apostrophe or `</script>`.
    """
    html = (_TEMPLATES_DIR / "suppression.html").read_text()
    assert 'onsubmit="return confirm(' not in html, (
        "Inline confirm() with Jinja interpolation in suppression.html is an "
        "XSS vector. Migrate to data-* attrs + addEventListener delegation."
    )
    # Also check no Jinja inside inline JS strings
    for handler in ("onclick", "onsubmit"):
        bad = re.findall(rf'{handler}="[^"]*\{{\{{[^}}]*\}}\}}[^"]*"', html)
        for match in bad:
            if "confirm(" in match or "alert(" in match:
                pytest.fail(
                    f"XSS-prone inline handler in suppression.html: {match!r}"
                )


# ============================================================================
# Fix 6 — pagination: total + next_offset returned, "Next" never on empty page
# ============================================================================

def test_mailbox_api_list_returns_total_and_next_offset(authed_client, monkeypatch, tmp_path):
    """API returns `total` (post-filter) and `next_offset` (None at end)."""
    import services.mailbox_stats as ms
    stats_file = tmp_path / "stats.json"
    boxes = [
        {"email": f"u{i}@x.com", "user": f"u{i}", "quota_mb": 1024,
         "used_mb": 0, "status": "active", "created_at": "",
         "alias_count": 0, "last_login": None, "disk_size_mb": 0}
        for i in range(50)  # exact-multiple of default limit=100? no — limit=10
    ]
    stats_file.write_text(json.dumps({
        "refreshed_at": "2026-04-30T12:00:00+00:00",
        "duration_sec": 0.0, "domain_count": 1, "mailbox_count": 50,
        "errors": [], "domains": {"x.com": {"mailboxes": boxes}},
    }))
    monkeypatch.setattr(ms, "STATS_JSON_PATH", stats_file)

    # Page 1, limit=10 → total=50, next_offset=10
    r = authed_client.get("/mailboxes/api/list?domain=x.com&limit=10&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 50
    assert body["next_offset"] == 10
    assert len(body["mailboxes"]) == 10

    # Last page exactly: offset=40, limit=10 → next_offset=None (no more)
    r2 = authed_client.get("/mailboxes/api/list?domain=x.com&limit=10&offset=40")
    body2 = r2.json()
    assert body2["total"] == 50
    assert body2["next_offset"] is None, (
        f"On exact-boundary last page, next_offset must be null. Got: {body2}"
    )
    assert len(body2["mailboxes"]) == 10


def test_mailbox_api_list_exact_multiple_no_phantom_next(authed_client, monkeypatch, tmp_path):
    """Total=50 + limit=50 + offset=0 → next_offset=None (no phantom Next)."""
    import services.mailbox_stats as ms
    stats_file = tmp_path / "stats.json"
    boxes = [
        {"email": f"u{i}@x.com", "user": f"u{i}", "quota_mb": 1024,
         "used_mb": 0, "status": "active", "created_at": "",
         "alias_count": 0, "last_login": None, "disk_size_mb": 0}
        for i in range(50)
    ]
    stats_file.write_text(json.dumps({
        "refreshed_at": "2026-04-30T12:00:00+00:00",
        "duration_sec": 0.0, "domain_count": 1, "mailbox_count": 50,
        "errors": [], "domains": {"x.com": {"mailboxes": boxes}},
    }))
    monkeypatch.setattr(ms, "STATS_JSON_PATH", stats_file)

    r = authed_client.get("/mailboxes/api/list?domain=x.com&limit=50&offset=0")
    body = r.json()
    assert body["total"] == 50
    assert body["next_offset"] is None, (
        "When offset+limit >= total, next_offset MUST be None even on exact "
        f"boundary. Got: next_offset={body.get('next_offset')!r}"
    )


def test_suppression_api_list_returns_total_and_next_offset(authed_client, monkeypatch):
    """Suppression API also exposes total + next_offset (Fix 6 mirror)."""
    from services.brevo_suppression import Block

    # Build a fake list_blocked that returns 25 records ALL with same category
    captured = {"calls": []}
    async def fake_list(category="all", limit=100, offset=0):
        captured["calls"].append({"category": category, "limit": limit, "offset": offset})
        all_blocks = [
            Block(email=f"u{i}@x.com", reason="hardBounce", blocked_at="")
            for i in range(25)
        ]
        return all_blocks[offset:offset + limit]

    # Also need a total-aware version. The fix should expose total via either:
    #   (a) brevo_suppression.list_blocked returning (blocks, total) — schema change
    #   (b) router computing total from in-page response (over-fetch by 1 trick)
    # We test the API contract: `total` and `next_offset` keys exist.
    monkeypatch.setattr("services.brevo_suppression.list_blocked", fake_list)

    r = authed_client.get("/suppression/api/list?category=hard&limit=10&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert "total" in body, (
        f"Expected 'total' in /suppression/api/list response. Body keys: {list(body)}"
    )
    assert "next_offset" in body, (
        f"Expected 'next_offset' in /suppression/api/list response. Body keys: {list(body)}"
    )


def test_mailbox_api_list_validates_domain(authed_client):
    """Fix 2 — bad domain on API list also rejects with 422."""
    r = authed_client.get("/mailboxes/api/list?domain=BAD!!!&limit=10")
    assert r.status_code == 422
