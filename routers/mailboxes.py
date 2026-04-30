"""Mailbox CRUD router. Read endpoints first; writes added in subsequent tasks.

Faz 4a hardening (2026-04-30):
- Fix 1: hestia write CLIs wrapped in `asyncio.to_thread` (event-loop safe).
- Fix 2: handler-entry RE_DOMAIN validation for every endpoint that takes domain.
- Fix 3: generated password delivered via signed flash cookie (not URL query).
- Fix 4: `mailbox.create_failed` audit event on hestia error path.
- Fix 6: `total` + `next_offset` returned from /api/list (pagination correctness).
"""
import asyncio
import json
import re
import secrets
import string
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote

from services import mailbox_stats
from services import hestia
from services.audit import audit
from services.templates import _ctx, set_flash


router = APIRouter()
templates = Jinja2Templates(directory="templates")


# Faz 4a Fix 2 — strict domain regex applied at handler entry. Same character
# set as services.hestia.RE_DOMAIN but enforced one layer earlier so we never
# even reach the (slow, awaited-via-thread) hestia call on garbage input.
RE_DOMAIN = re.compile(r"^[a-z0-9.-]{3,253}$")
RE_LOCAL = re.compile(r"^[a-z0-9._-]{1,64}$")


def _validate_domain(domain: str) -> None:
    """Reject malformed domains BEFORE calling hestia. 422 to match FastAPI's
    own validation responses; HestiaCLIError -> 303 was reserved for upstream
    errors discovered during the actual CLI call."""
    if not RE_DOMAIN.match(domain or ""):
        raise HTTPException(status_code=422, detail="geçersiz domain formatı")


def _validate_local(local: str, label: str = "user") -> None:
    if not RE_LOCAL.match(local or ""):
        raise HTTPException(status_code=422, detail=f"geçersiz {label} formatı")


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
        page_title="Mail Kutuları",
        breadcrumb=[{"label": "Mail Kutuları", "href": None}],
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
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """List mailboxes for a domain with pagination metadata.

    Faz 4a Fix 6: returns `total` (post-filter count) and `next_offset` so the
    UI never shows "Next" on an exact-multiple boundary that would resolve to
    an empty page.
    """
    _require_auth(request)
    _validate_domain(domain)
    stats = _read_stats()
    if not stats or domain not in stats.get("domains", {}):
        return {
            "domain": domain,
            "mailboxes": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
            "next_offset": None,
            "stats_meta": None,
        }
    all_boxes = _filter_mailboxes(stats["domains"][domain]["mailboxes"], q)
    total = len(all_boxes)
    page = all_boxes[offset:offset + limit]
    next_offset = offset + limit if (offset + limit) < total else None
    return {
        "domain": domain,
        "mailboxes": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
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
    _validate_domain(domain)
    _validate_local(email_local, "email_local")
    if password != password_confirm:
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error=password_mismatch",
            status_code=303,
        )
    failures: list[str] = []
    try:
        await asyncio.to_thread(
            hestia.add_mailbox, domain, email_local, password, quota_mb
        )
    except hestia.HestiaCLIError as e:
        eid = e.translated.get("id", "unknown")
        # Fix 4: emit audit on failure path so silent failures aren't lost.
        audit(
            "mailbox.create_failed",
            domain=domain, user=email_local,
            error_id=eid,
        )
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error={quote(eid)}",
            status_code=303,
        )

    advanced_used = bool(aliases or forward_to or autoreply)
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()] if aliases else []
    for alias in alias_list:
        try:
            await asyncio.to_thread(hestia.add_alias, domain, email_local, alias)
        except hestia.HestiaCLIError as e:
            failures.append(f"alias:{alias}:{e.translated.get('id', 'unknown')}")

    if forward_to:
        try:
            await asyncio.to_thread(
                hestia.set_forward, domain, email_local, forward_to
            )
        except hestia.HestiaCLIError as e:
            failures.append(f"forward:{e.translated.get('id', 'unknown')}")

    if autoreply:
        try:
            await asyncio.to_thread(
                hestia.set_autoreply, domain, email_local, autoreply
            )
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


@router.post("/mailboxes/delete")
async def post_mailbox_delete(
    request: Request,
    domain: str = Form(...),
    user: str = Form(...),
    expected_email: str = Form(...),
    confirmation_typed: str = Form(...),
):
    _require_auth(request)
    _validate_domain(domain)
    _validate_local(user, "user")
    if confirmation_typed.strip() != expected_email.strip():
        raise HTTPException(status_code=400, detail="confirmation does not match")

    # Capture rich audit metadata BEFORE delete
    stats = _read_stats()
    size_mb = None
    alias_count = None
    if stats and domain in stats.get("domains", {}):
        for box in stats["domains"][domain]["mailboxes"]:
            if box.get("user") == user:
                size_mb = box.get("disk_size_mb")
                alias_count = box.get("alias_count")
                break
    aliases_list: list[str] = []
    try:
        aliases_list = await hestia.list_aliases(domain, user)
    except hestia.HestiaAPIError:
        pass

    try:
        await asyncio.to_thread(hestia.delete_mailbox, domain, user)
    except hestia.HestiaCLIError as e:
        eid = e.translated.get("id", "unknown")
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error={quote(eid)}",
            status_code=303,
        )

    audit(
        "mailbox.delete",
        email=f"{user}@{domain}",
        domain=domain, user=user,
        size_mb=size_mb, alias_count=alias_count,
        aliases=aliases_list,
    )
    return RedirectResponse(
        url=f"/mailboxes?domain={quote(domain)}&deleted={quote(user + '@' + domain)}",
        status_code=303,
    )


def _gen_password(length: int = 24) -> str:
    """Strong random: 24 chars from alphanumeric + symbols, guaranteed to satisfy
    _check_password (12+chars + digit + symbol)."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.isdigit() for c in pw)
                and any(c.isalpha() for c in pw)
                and any(not c.isalnum() for c in pw)):
            return pw


@router.post("/mailboxes/reset-password")
async def post_mailbox_reset_password(
    request: Request,
    domain: str = Form(...),
    user: str = Form(...),
    mode: str = Form(...),
    password: str = Form(""),
    password_confirm: str = Form(""),
    send_to: str = Form(""),
):
    _require_auth(request)
    _validate_domain(domain)
    _validate_local(user, "user")
    if mode == "manual":
        if password != password_confirm:
            return RedirectResponse(
                url=f"/mailboxes?domain={quote(domain)}&error=password_mismatch",
                status_code=303,
            )
        new_password = password
        generated = False
    elif mode == "generate":
        new_password = _gen_password(24)
        generated = True
    else:
        raise HTTPException(status_code=400, detail="invalid mode")

    try:
        await asyncio.to_thread(
            hestia.change_password, domain, user, new_password
        )
    except hestia.HestiaCLIError as e:
        eid = e.translated.get("id", "unknown")
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error={quote(eid)}",
            status_code=303,
        )

    audit("mailbox.password_reset", domain=domain, user=user, generated=generated)

    if send_to:
        try:
            from app import send_mail
            await send_mail(
                send_to,
                f"[Mail Admin] {user}@{domain} şifresi sıfırlandı",
                f"Yeni şifre: {new_password}\n\nBu mesajı sildikten sonra giriş yapıp tekrar değiştir.",
            )
        except Exception:
            pass

    # Fix 3: generated password is delivered via signed one-shot flash cookie,
    # never as a URL query param (browser history / server log leak).
    response = RedirectResponse(
        url=f"/mailboxes?domain={quote(domain)}&pwreset={quote(user + '@' + domain)}",
        status_code=303,
    )
    if generated:
        set_flash(response, generated_password=new_password,
                  for_email=f"{user}@{domain}")
    return response


@router.post("/mailboxes/change-quota")
async def post_mailbox_change_quota(
    request: Request,
    domain: str = Form(...),
    user: str = Form(...),
    quota_mb: int = Form(...),
):
    _require_auth(request)
    _validate_domain(domain)
    _validate_local(user, "user")
    try:
        await asyncio.to_thread(hestia.change_quota, domain, user, quota_mb)
    except hestia.HestiaCLIError as e:
        eid = e.translated.get("id", "unknown")
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error={quote(eid)}",
            status_code=303,
        )
    audit("mailbox.quota_change", domain=domain, user=user, quota_mb=quota_mb)
    return RedirectResponse(
        url=f"/mailboxes?domain={quote(domain)}&quota={quote(user + '@' + domain)}",
        status_code=303,
    )


@router.post("/mailboxes/aliases/add")
async def post_mailbox_alias_add(
    request: Request,
    domain: str = Form(...),
    user: str = Form(...),
    alias_local: str = Form(...),
):
    _require_auth(request)
    _validate_domain(domain)
    _validate_local(user, "user")
    _validate_local(alias_local, "alias")
    try:
        await asyncio.to_thread(hestia.add_alias, domain, user, alias_local)
    except hestia.HestiaCLIError as e:
        eid = e.translated.get("id", "unknown")
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error={quote(eid)}",
            status_code=303,
        )
    audit("mailbox.alias_add", domain=domain, user=user, alias=alias_local)
    return RedirectResponse(
        url=f"/mailboxes?domain={quote(domain)}&alias_added={quote(alias_local)}",
        status_code=303,
    )


@router.post("/mailboxes/aliases/remove")
async def post_mailbox_alias_remove(
    request: Request,
    domain: str = Form(...),
    user: str = Form(...),
    alias_local: str = Form(...),
):
    _require_auth(request)
    _validate_domain(domain)
    _validate_local(user, "user")
    _validate_local(alias_local, "alias")
    try:
        await asyncio.to_thread(hestia.delete_alias, domain, user, alias_local)
    except hestia.HestiaCLIError as e:
        eid = e.translated.get("id", "unknown")
        return RedirectResponse(
            url=f"/mailboxes?domain={quote(domain)}&error={quote(eid)}",
            status_code=303,
        )
    audit("mailbox.alias_remove", domain=domain, user=user, alias=alias_local)
    return RedirectResponse(
        url=f"/mailboxes?domain={quote(domain)}&alias_removed={quote(alias_local)}",
        status_code=303,
    )
