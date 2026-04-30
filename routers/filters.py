"""Inbound filter editor — Faz 4b Task 12.

Endpoints (6):
  - GET  /filters                             — page (3-tab UI + prefill query)
  - GET  /filters/api/list?file=...           — list entries by file
  - POST /filters/add                         — add + 2-stage validate + reload
  - POST /filters/remove                      — type-to-confirm + remove + validate + reload
  - POST /filters/reload                      — standalone reload (10s rate-limit)
  - GET  /filters/api/validate-config         — exim4 -bV result (30s cache)

Pattern recap (Tasks 4-9):
  - `_require_auth` lazy-imported from app to dodge circular imports
  - `_get_session_email` for audit `by=` field
  - `templates.TemplateResponse(request, "pages/x.html", ctx)` new-style
  - CSRF middleware in app.py:65-87 covers all POSTs except cron/login/verify
  - Service layer (services/exim_filters.py) is pure; router plumbs HTTP <-> service
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from services import exim_filters as ef
from services.audit import audit
from services.templates import _ctx


router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ---- module-level state (rate limit + validate cache) ----------------------
RELOAD_RATE_LIMIT_S = 10.0
_last_reload_ts: float = 0.0

VALIDATE_CACHE_TTL = 30.0
_validate_cache: Optional[dict] = None
_validate_at: float = 0.0


def _require_auth(request: Request):
    """Lazy import from app to avoid circular dependency at module load time."""
    from app import require_auth
    return require_auth(request)


def _get_session_email(request: Request) -> str:
    from app import get_session
    return get_session(request) or ""


# Map URL-friendly names → enum members. Single source of truth so the
# router and template stay in sync.
_FILE_MAP = {
    "spam-blocks": ef.FilterFile.SPAM_BLOCKS,
    "white-blocks": ef.FilterFile.WHITE_BLOCKS,
    "dnsbl": ef.FilterFile.DNSBL,
}


def _file_from_param(name: str) -> Optional[ef.FilterFile]:
    """Translate URL param to enum; returns None on unknown (caller emits 400)."""
    return _FILE_MAP.get(name)


def _entry_to_dict(e: ef.FilterEntry) -> dict:
    return {
        "value": e.value,
        "comment": e.comment,
        "line_no": e.line_no,
        "added_at": e.added_at,
    }


# ============================ PAGE ============================

@router.get("/filters", response_class=HTMLResponse)
async def page_filters(
    request: Request,
    prefill_ip: Optional[str] = Query(None),
    list: Optional[str] = Query(None, alias="list"),
):
    _require_auth(request)
    ctx = _ctx(
        request,
        page="filters",
        title="Filtreler",
        prefill_ip=prefill_ip or "",
        prefill_list=list or "",
    )
    return templates.TemplateResponse(request, "pages/filters.html", ctx)


# ============================ API: list ============================

@router.get("/filters/api/list")
async def api_list(request: Request, file: str = Query(...)):
    _require_auth(request)
    f = _file_from_param(file)
    if f is None:
        return JSONResponse(
            {"error": f"unknown file '{file}' (allowed: spam-blocks, white-blocks, dnsbl)"},
            status_code=400,
        )
    entries = ef.list_entries(f)
    return {
        "file": file,
        "entries": [_entry_to_dict(e) for e in entries],
    }


# ============================ POST: add ============================

@router.post("/filters/add")
async def post_add(
    request: Request,
    file: str = Form(...),
    value: str = Form(...),
    comment: str = Form(""),
):
    _require_auth(request)
    f = _file_from_param(file)
    if f is None:
        return JSONResponse(
            {"error": f"unknown file '{file}' (allowed: spam-blocks, white-blocks, dnsbl)"},
            status_code=400,
        )
    by = _get_session_email(request)
    try:
        result = await ef.add_entry_with_reload(f, value, comment, by=by)
    except ef.ConflictError as ce:
        # File mtime changed externally — UI should refresh and retry.
        audit("filters.add.conflict", file=file, value=value, by=by, err=str(ce))
        return JSONResponse({"error": str(ce)}, status_code=409)
    except ValueError as ve:
        # validate_value failure, duplicate, or rolled-back validate-fail.
        audit("filters.add.failed", file=file, value=value, by=by, err=str(ve))
        return JSONResponse({"error": str(ve)}, status_code=422)

    audit(
        "filters.add",
        file=file,
        value=result["entry"].value,
        comment=comment,
        by=by,
    )
    out: dict = {
        "ok": True,
        "entry": _entry_to_dict(result["entry"]),
    }
    if "reload_warning" in result:
        out["reload_warning"] = result["reload_warning"]
    return out


# ============================ POST: remove ============================

@router.post("/filters/remove")
async def post_remove(
    request: Request,
    file: str = Form(...),
    line_no: int = Form(...),
    confirmation_typed: str = Form(...),
):
    _require_auth(request)
    f = _file_from_param(file)
    if f is None:
        return JSONResponse(
            {"error": f"unknown file '{file}' (allowed: spam-blocks, white-blocks, dnsbl)"},
            status_code=400,
        )
    by = _get_session_email(request)

    # Look up the entry at line_no BEFORE calling remove, so we can enforce
    # the type-to-confirm check server-side. UI is just sugar.
    entries = ef.list_entries(f)
    target = next((e for e in entries if e.line_no == line_no), None)
    if target is None:
        return JSONResponse(
            {"error": f"line {line_no} not found in {file}"},
            status_code=404,
        )
    if confirmation_typed.strip() != target.value.strip():
        return JSONResponse(
            {"error": "confirmation_typed must equal entry value"},
            status_code=422,
        )

    try:
        result = await ef.remove_entry_with_reload(f, line_no, by=by)
    except ef.ConflictError as ce:
        audit("filters.remove.conflict", file=file, line_no=line_no, by=by, err=str(ce))
        return JSONResponse({"error": str(ce)}, status_code=409)
    except ValueError as ve:
        audit("filters.remove.failed", file=file, line_no=line_no, by=by, err=str(ve))
        return JSONResponse({"error": str(ve)}, status_code=422)

    audit(
        "filters.remove",
        file=file,
        line_no=line_no,
        removed_value=result["removed_value"],
        by=by,
    )
    out: dict = {
        "ok": True,
        "removed_value": result["removed_value"],
    }
    if "reload_warning" in result:
        out["reload_warning"] = result["reload_warning"]
    return out


# ============================ POST: reload ============================

@router.post("/filters/reload")
async def post_reload(request: Request):
    _require_auth(request)
    global _last_reload_ts
    now = time.time()
    elapsed = now - _last_reload_ts
    if elapsed < RELOAD_RATE_LIMIT_S:
        retry_after = max(1, int(RELOAD_RATE_LIMIT_S - elapsed) + 1)
        return JSONResponse(
            {"error": f"reload rate limit, {retry_after}s sonra tekrar deneyin"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    ok, msg = ef.reload_exim()
    by = _get_session_email(request)
    audit("filters.reload", ok=ok, msg=msg[:200], by=by)
    if not ok:
        # NOT a rate-limit issue — bubble up the systemctl failure.
        return JSONResponse({"ok": False, "error": msg}, status_code=500)
    _last_reload_ts = now
    return {"ok": True, "msg": msg}


# ============================ GET: validate-config ============================

@router.get("/filters/api/validate-config")
async def api_validate_config(request: Request):
    _require_auth(request)
    global _validate_cache, _validate_at
    now = time.time()
    if _validate_cache is not None and (now - _validate_at) < VALIDATE_CACHE_TTL:
        return _validate_cache
    ok, msg = ef.validate_exim_config()
    payload = {"ok": ok, "msg": msg, "checked_at": now}
    _validate_cache = payload
    _validate_at = now
    return payload
