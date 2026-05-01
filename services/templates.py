"""Template context builder with CSRF token + flash injection.

Use _ctx(request, **kwargs) instead of {"request": request, ...} when calling
templates.TemplateResponse, so authenticated pages get the csrf_token in
context (rendered as <meta name="csrf-token"> in base.html).

Faz 4a Fix 3: flash cookie helpers (`set_flash`, `consume_flash`). Used to
deliver one-shot sensitive payloads (e.g. a freshly generated password) to
the next page render without putting them in the URL query string. The
cookie is signed (itsdangerous TimestampSigner with SESSION_SECRET) and has
a 60s max-age — naturally consumed-by-read for our redirect-and-render
pattern.
"""
import json
import os
from typing import Optional

from fastapi import Request
from fastapi.responses import Response
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from services.csrf import issue_token


_FLASH_COOKIE = "ma_flash"
_FLASH_MAX_AGE = 60  # seconds; signed-cookie freshness ceiling


def _flash_signer() -> Optional[TimestampSigner]:
    # Match app.py default — anywhere SESSION_SECRET is unset (test env, dev),
    # we use the same fallback the rest of the auth code uses, so the flash
    # cookie is functional in tests without per-test env wiring.
    secret = os.environ.get("SESSION_SECRET") or "dev-only-change-me"
    return TimestampSigner(secret)


def set_flash(response: Response, **kwargs) -> None:
    """Write a one-shot signed flash payload as the `ma_flash` cookie.

    Caller passes arbitrary JSON-serialisable kwargs. They become available
    on the NEXT request via `consume_flash(request)`. The cookie is httponly,
    samesite=lax, signed, with a 60s ttl.
    """
    s = _flash_signer()
    if s is None:
        return
    try:
        payload = json.dumps(kwargs)
    except (TypeError, ValueError):
        return
    signed = s.sign(payload.encode()).decode()
    response.set_cookie(
        _FLASH_COOKIE,
        signed,
        max_age=_FLASH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
    )


def consume_flash(request: Request) -> dict:
    """Read + return flash payload, or {} on absent / invalid / expired cookie.

    'Consume' is loose here: the cookie is short-lived (60s) and we don't
    actively delete it server-side. After the next render the cookie either
    self-expires or is overwritten by another `set_flash`.
    """
    raw = request.cookies.get(_FLASH_COOKIE, "")
    if not raw:
        return {}
    s = _flash_signer()
    if s is None:
        return {}
    try:
        unsigned = s.unsign(raw.encode(), max_age=_FLASH_MAX_AGE)
        data = json.loads(unsigned.decode())
        if isinstance(data, dict):
            return data
        return {}
    except (BadSignature, SignatureExpired, ValueError):
        return {}


_DOMAIN_FILTER_COOKIE = "ma_domain"


def get_domain_filter(request: Request) -> str:
    """Read the user's active domain filter ('' = all domains)."""
    return request.cookies.get(_DOMAIN_FILTER_COOKIE, "") or ""


def _ctx(request: Request, **kwargs) -> dict:
    """Build template context with CSRF token + flash + domain filter + static_version."""
    ctx: dict = {"request": request}
    sess = request.cookies.get("ma_sess", "")
    if sess:
        ctx["csrf_token"] = issue_token(sess)
    ctx["flash"] = consume_flash(request)
    ctx["active_domain"] = get_domain_filter(request)
    try:
        from app import STATIC_VERSION as _sv
        ctx["static_version"] = _sv
    except Exception:
        ctx["static_version"] = "0"
    ctx.update(kwargs)
    return ctx
