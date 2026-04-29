"""Mailbox CRUD router. Read endpoints first; writes added in subsequent tasks."""
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote

from services import mailbox_stats
from services import hestia
from services.audit import audit
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


@router.post("/mailboxes/create")
async def post_mailbox_create(
    request: Request,
    domain: str = Form(...),
    email_local: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    quota_mb: int = Form(...),
    aliases: str = Form(""),
    forward_to: str = Form(""),
    autoreply: str = Form(""),
    send_credentials_to: str = Form(""),
):
    _require_auth(request)
    if password != password_confirm:
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error=password_mismatch",
            status_code=303,
        )
    failures: list[str] = []
    try:
        hestia.add_mailbox(domain, email_local, password, quota_mb)
    except hestia.HestiaCLIError as e:
        eid = e.translated.get("id", "unknown")
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error={quote(eid)}",
            status_code=303,
        )

    advanced_used = bool(aliases or forward_to or autoreply)
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()] if aliases else []
    for alias in alias_list:
        try:
            hestia.add_alias(domain, email_local, alias)
        except hestia.HestiaCLIError as e:
            failures.append(f"alias:{alias}:{e.translated.get('id', 'unknown')}")

    if forward_to:
        try:
            hestia.set_forward(domain, email_local, forward_to)
        except hestia.HestiaCLIError as e:
            failures.append(f"forward:{e.translated.get('id', 'unknown')}")

    if autoreply:
        try:
            hestia.set_autoreply(domain, email_local, autoreply)
        except hestia.HestiaCLIError as e:
            failures.append(f"autoreply:{e.translated.get('id', 'unknown')}")

    audit(
        "mailbox.create",
        domain=domain, user=email_local,
        advanced_used=advanced_used,
        send_credentials=bool(send_credentials_to),
        partial_failures=failures,
    )

    if send_credentials_to:
        try:
            from app import send_mail
            await send_mail(
                send_credentials_to,
                f"[Mail Admin] {email_local}@{domain} kimlik bilgileri",
                f"E-posta: {email_local}@{domain}\nŞifre: {password}\n\nBu mesajı sildikten sonra şifre değiştirmeyi unutma.",
            )
        except Exception as e:
            failures.append(f"send_credentials:{e}")

    msg = f"created={quote(email_local + '@' + domain)}"
    if failures:
        msg += f"&warn={quote(','.join(failures))}"
    return RedirectResponse(
        url=f"/mailboxes?domain={quote(domain)}&{msg}",
        status_code=303,
    )
