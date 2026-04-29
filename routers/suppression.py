"""Brevo suppression list router."""
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
    limit: int = Query(100, ge=1, le=500),
):
    _require_auth(request)
    valid_cats = {"all", "hard", "soft", "blocked", "unsub", "spam"}
    if category not in valid_cats:
        category = "all"
    try:
        blocks = await brevo_suppression.list_blocked(category=category, limit=limit, offset=offset)
        api_error = None
    except brevo_suppression.BrevoSuppressionError as e:
        blocks = []
        api_error = str(e)
    blocks = _filter(blocks, q)
    ctx = _ctx(
        request,
        page="suppression",
        category=category,
        blocks=blocks,
        search_q=q,
        offset=offset,
        limit=limit,
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
    blocks = await brevo_suppression.list_blocked(category=category, limit=limit, offset=offset)
    blocks = _filter(blocks, q)
    return {
        "category": category,
        "blocks": [{"email": b.email, "reason": b.reason, "blocked_at": b.blocked_at} for b in blocks],
        "offset": offset,
        "limit": limit,
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
