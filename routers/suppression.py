"""Brevo suppression list router.

Faz 4a hardening (2026-04-30):
- Fix 6: API returns `total` (None when unknown) and `next_offset` so the UI
  doesn't show a "Next" link on an exact-multiple page boundary.
"""
from urllib.parse import quote

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from services import brevo_suppression
from services.audit import audit
from services.templates import _ctx


router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _require_auth(request: Request):
    """Lazy import from app to avoid circular dependency at module load time."""
    from app import require_auth
    return require_auth(request)


def _filter(blocks, q: str):
    if not q:
        return blocks
    qlow = q.lower()
    return [b for b in blocks if qlow in (b.email or "").lower()]


@router.get("/suppression", response_class=HTMLResponse)
async def page_suppression(
    request: Request,
    category: str = Query("all"),
    q: str = Query(""),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    _require_auth(request)
    valid_cats = {"all", "hard", "soft", "blocked", "unsub", "spam"}
    if category not in valid_cats:
        category = "all"
    # Brevo caps server-side at 100 — original limit+1 over-fetch hack
    # produced 400 Bad Request. We now ask for exactly `limit` and
    # use a heuristic: if we got back the full page, assume there might
    # be more. False positive (one extra empty next click) is acceptable.
    try:
        peek_blocks = await brevo_suppression.list_blocked(
            category=category, limit=limit, offset=offset
        )
        api_error = None
    except brevo_suppression.BrevoSuppressionError as e:
        peek_blocks = []
        api_error = str(e)
    has_more = len(peek_blocks) >= limit
    blocks = peek_blocks
    next_offset = offset + limit if has_more else None
    blocks = _filter(blocks, q)
    ctx = _ctx(
        request,
        page="suppression",
        category=category,
        blocks=blocks,
        search_q=q,
        offset=offset,
        limit=limit,
        next_offset=next_offset,
        has_more=has_more,
        api_error=api_error,
    )
    return templates.TemplateResponse(request, "pages/suppression.html", ctx)


@router.get("/suppression/api/list")
async def api_suppression_list(
    request: Request,
    category: str = Query("all"),
    q: str = Query(""),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    _require_auth(request)
    # Fix 6: over-fetch by 1; total is None (Brevo doesn't expose a cheap
    # count for the filtered category). The UI uses `next_offset` for the
    # Next button, so total being unknown is fine.
    peek_blocks = await brevo_suppression.list_blocked(
        category=category, limit=limit + 1, offset=offset
    )
    has_more = len(peek_blocks) > limit
    blocks = peek_blocks[:limit]
    blocks = _filter(blocks, q)
    next_offset = offset + limit if has_more else None
    return {
        "category": category,
        "blocks": [{"email": b.email, "reason": b.reason, "blocked_at": b.blocked_at} for b in blocks],
        "offset": offset,
        "limit": limit,
        "total": None,
        "next_offset": next_offset,
    }


@router.post("/suppression/remove")
async def post_suppression_remove(
    request: Request,
    email: str = Form(...),
):
    _require_auth(request)
    try:
        await brevo_suppression.remove_from_suppression(email)
    except brevo_suppression.BrevoSuppressionError as e:
        return RedirectResponse(
            url=f"/suppression?error={quote(str(e)[:200])}",
            status_code=303,
        )
    audit("suppression.remove", email=email)
    return RedirectResponse(
        url=f"/suppression?removed={quote(email)}",
        status_code=303,
    )
