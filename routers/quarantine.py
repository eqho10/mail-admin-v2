"""Quarantine router — Faz 4b Task 9.

Endpoints:
  - GET  /quarantine                      — page (3-source view + KPIs + table + 3 modals)
  - GET  /quarantine/api/list             — frozen | rejected | all (paged JSON)
  - GET  /quarantine/api/raw/{msgid}      — exim_view_msg headers+body+truncated flag
  - POST /quarantine/release              — exim -Mt msgid (CSRF + audit)
  - POST /quarantine/delete               — exim -Mrm with type-to-confirm enforced server-side

Pattern recap (Tasks 4-8):
  - `_require_auth` lazy-imported from app to dodge circular imports
  - `_get_session_email` for audit `by=` field
  - `templates.TemplateResponse(request, "pages/x.html", ctx)` new-style
  - CSRF middleware in app.py:65-87 covers all POSTs except cron/login/verify
  - Service layer (services/quarantine.py + services/exim.py) does heavy lifting;
    router just plumbs HTTP <-> service.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from services import exim as exim_svc
from services import quarantine as quarantine_svc
from services.audit import audit
from services.templates import _ctx


router = APIRouter()
templates = Jinja2Templates(directory="templates")


# Same charset as services/exim.py msgid validation; mirrored here so we can
# 422 *before* shelling out (defense-in-depth — service layer also validates).
RE_MSGID = re.compile(r"^[A-Za-z0-9\-]+$")

VALID_SOURCES = {"all", "frozen", "rejected"}


def _require_auth(request: Request):
    """Lazy import from app to avoid circular dependency at module load time."""
    from app import require_auth
    return require_auth(request)


def _get_session_email(request: Request) -> str:
    from app import get_session
    return get_session(request) or ""


def _frozen_to_item(m: dict) -> dict:
    """Shape exim_queue_list() row into the unified list/api/list item."""
    return {
        "source": "frozen",
        "msgid": m.get("msgid", ""),
        "from": m.get("from", ""),
        "to": m.get("to", []),
        "size": m.get("size", ""),
        "age": m.get("age", ""),
        "reason": "frozen",
        "ts": "",        # exim -bp doesn't expose timestamp
        "ip": "",
        "count": 1,
    }


def _rejected_group_to_item(g: dict) -> dict:
    """Shape parse_rejected_lines() group into unified list/api/list item."""
    return {
        "source": "rejected",
        "key": g.get("key", ""),
        "ip": g.get("ip", ""),
        "from": g.get("sender", ""),
        "to": [g.get("recipient", "")] if g.get("recipient") else [],
        "reason": g.get("reason", ""),
        "count": int(g.get("count", 0)),
        "first_seen": g.get("first_seen", ""),
        "last_seen": g.get("last_seen", ""),
        "ts": g.get("last_seen", ""),
        "size": "",
        "age": "",
        "msgid": "",   # rejected lines don't have a spool msgid
        "raw_lines": g.get("raw_lines", []),
    }


# ============================ PAGE ============================

@router.get("/quarantine", response_class=HTMLResponse)
async def page_quarantine(request: Request):
    _require_auth(request)
    ctx = _ctx(request, page="quarantine", title="Quarantine")
    return templates.TemplateResponse(request, "pages/quarantine.html", ctx)


# ============================ API: list ============================

@router.get("/quarantine/api/list")
async def api_list(
    request: Request,
    source: str = Query("all"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _require_auth(request)
    if source not in VALID_SOURCES:
        return JSONResponse(
            {"error": f"unknown source '{source}' (allowed: {sorted(VALID_SOURCES)})"},
            status_code=400,
        )

    items: list[dict[str, Any]] = []
    if source in ("all", "frozen"):
        items.extend(_frozen_to_item(m) for m in quarantine_svc.get_frozen_messages())
    if source in ("all", "rejected"):
        items.extend(_rejected_group_to_item(g) for g in quarantine_svc.get_rejected_groups())

    # When source=all we mix two streams. Sort by ts desc; rejected groups have
    # last_seen, frozen rows have empty ts → push to bottom.
    items.sort(key=lambda x: x.get("ts", ""), reverse=True)

    total = len(items)
    page_items = items[offset:offset + limit]
    next_offset = offset + len(page_items) if (offset + len(page_items)) < total else None

    return {
        "source": source,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "items": page_items,
    }


# ============================ API: raw msg ============================

@router.get("/quarantine/api/raw/{msgid}")
async def api_raw(request: Request, msgid: str):
    _require_auth(request)
    if not RE_MSGID.match(msgid):
        return JSONResponse({"error": "invalid msgid"}, status_code=422)
    res = exim_svc.exim_view_msg(msgid)
    if res.get("invalid"):
        return JSONResponse({"error": res.get("error", "invalid msgid")}, status_code=422)
    if res.get("not_found"):
        return JSONResponse({"error": res.get("error", "not found")}, status_code=404)
    if "headers" not in res:
        # Generic exim error (e.g. spool unreadable)
        return JSONResponse({"error": res.get("error", "exim view failed")}, status_code=500)
    return {
        "msgid": msgid,
        "headers": res.get("headers", ""),
        "body": res.get("body", ""),
        "truncated": bool(res.get("truncated", False)),
    }


# ============================ POST: release ============================

@router.post("/quarantine/release")
async def post_release(request: Request, msgid: str = Form(...)):
    _require_auth(request)
    if not RE_MSGID.match(msgid):
        return JSONResponse({"error": "invalid msgid"}, status_code=422)
    rc, stdout, stderr = exim_svc.exim_release_msg(msgid)
    by = _get_session_email(request)
    if rc != 0:
        audit("quarantine.release.failed", msgid=msgid, rc=rc, stderr=(stderr or "")[:500], by=by)
        return JSONResponse(
            {"ok": False, "msgid": msgid, "rc": rc, "stderr": stderr or ""},
            status_code=500,
        )
    audit("quarantine.release", msgid=msgid, by=by)
    return {"ok": True, "msgid": msgid, "stdout": stdout or ""}


# ============================ POST: delete ============================

@router.post("/quarantine/delete")
async def post_delete(
    request: Request,
    msgid: str = Form(...),
    confirmation_typed: str = Form(...),
):
    _require_auth(request)
    if not RE_MSGID.match(msgid):
        return JSONResponse({"error": "invalid msgid"}, status_code=422)
    # Server-side enforcement of the type-to-confirm UX. Cannot trust JS only.
    if confirmation_typed.strip() != msgid:
        return JSONResponse(
            {"error": "confirmation_typed must equal msgid"},
            status_code=422,
        )
    rc, stdout, stderr = exim_svc.exim_delete_msg(msgid)
    by = _get_session_email(request)
    if rc != 0:
        audit("quarantine.delete.failed", msgid=msgid, rc=rc, stderr=(stderr or "")[:500], by=by)
        return JSONResponse(
            {"ok": False, "msgid": msgid, "rc": rc, "stderr": stderr or ""},
            status_code=500,
        )
    audit("quarantine.delete", msgid=msgid, by=by)
    return {"ok": True, "msgid": msgid, "stdout": stdout or ""}
