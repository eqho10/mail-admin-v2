"""Mailbox CRUD router. Read endpoints first; writes added in subsequent tasks."""
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from services import mailbox_stats
from services import hestia
from services.templates import _ctx


router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _require_auth(request: Request):
    """Lazy import from app to avoid circular dependency at module load time."""
    from app import require_auth
    return require_auth(request)


def _read_stats() -> Optional[dict]:
    try:
        return json.loads(Path(mailbox_stats.STATS_JSON_PATH).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _filter_mailboxes(boxes: list[dict], q: str) -> list[dict]:
    if not q:
        return boxes
    qlow = q.lower()
    return [b for b in boxes if qlow in b.get("email", "").lower()]


@router.get("/mailboxes", response_class=HTMLResponse)
async def page_mailboxes(
    request: Request,
    domain: Optional[str] = Query(None),
    q: str = Query(""),
):
    _require_auth(request)
    stats = _read_stats()
    if stats and stats.get("domains"):
        domains = sorted(stats["domains"].keys())
    else:
        try:
            domains = await hestia.list_mail_domains()
        except hestia.HestiaAPIError:
            domains = []
    selected = domain or (domains[0] if domains else None)
    if selected and stats and selected in stats.get("domains", {}):
        boxes = stats["domains"][selected]["mailboxes"]
        boxes = _filter_mailboxes(boxes, q)
    else:
        boxes = []

    from app import get_session
    ctx = _ctx(
        request,
        current_page="mailboxes",
        page_title="Mailbox'lar",
        breadcrumb=[{"label": "Mailbox'lar", "href": None}],
        user_email=get_session(request) or "",
        domains=domains,
        selected_domain=selected,
        mailboxes=boxes,
        stats_meta=stats,
        search_q=q,
        first_refresh_pending=stats is None,
    )
    return templates.TemplateResponse(request, "pages/mailboxes.html", ctx)


@router.get("/mailboxes/api/list")
async def api_mailboxes_list(
    request: Request,
    domain: str = Query(...),
    q: str = Query(""),
):
    _require_auth(request)
    stats = _read_stats()
    if not stats or domain not in stats.get("domains", {}):
        return {"domain": domain, "mailboxes": [], "stats_meta": None}
    boxes = _filter_mailboxes(stats["domains"][domain]["mailboxes"], q)
    return {
        "domain": domain,
        "mailboxes": boxes,
        "stats_meta": {k: v for k, v in stats.items() if k != "domains"},
    }
