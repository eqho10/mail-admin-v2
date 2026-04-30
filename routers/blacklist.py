"""Blacklist (DNSBL outbound check) router — Faz 4b Task 4.

Endpoints:
  - GET  /blacklist                 — page render (template stub; full UI Task 5)
  - GET  /blacklist/api/status      — cached snapshot (uses dnsbl.get_status, 60s TTL)
  - GET  /blacklist/api/history     — last N days of snapshots
  - POST /blacklist/recheck         — CSRF-protected, 60s rate-limit, audit, calls
                                       dnsbl.refresh_and_persist
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from services import dnsbl
from services.audit import audit
from services.templates import _ctx


router = APIRouter()
templates = Jinja2Templates(directory="templates")


RECHECK_RATE_LIMIT_S = 60
# Lock file path is module-level so tests can monkeypatch it.
RECHECK_LOCK_PATH = Path(__file__).resolve().parent.parent / "data" / "blacklist_recheck_lock.json"


def _require_auth(request: Request):
    """Lazy import from app to avoid circular dependency at module load time."""
    from app import require_auth
    return require_auth(request)


def _get_session_email(request: Request):
    from app import get_session
    return get_session(request)


def _read_recheck_lock() -> float:
    p = RECHECK_LOCK_PATH
    if not p.exists():
        return 0.0
    try:
        return float(json.loads(p.read_text()).get("last_at", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0.0


def _write_recheck_lock(ts: float) -> None:
    p = RECHECK_LOCK_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"last_at": ts}))


def _serialize_snapshot(snap: dnsbl.Snapshot) -> dict:
    return {
        "ts": snap.ts,
        "ip": snap.ip,
        "results": [
            {
                "zone": r.zone,
                "status": r.status,
                "return_code": r.return_code,
                "return_text": r.return_text,
                "duration_ms": r.duration_ms,
            }
            for r in snap.results
        ],
    }


@router.get("/blacklist", response_class=HTMLResponse)
async def page_blacklist(request: Request):
    _require_auth(request)
    ctx = _ctx(request, page="blacklist", title="Kara Liste")
    return templates.TemplateResponse(request, "pages/blacklist.html", ctx)


@router.get("/blacklist/api/status")
async def api_status(request: Request):
    _require_auth(request)
    snap = await dnsbl.get_status()
    return JSONResponse(_serialize_snapshot(snap))


@router.get("/blacklist/api/history")
async def api_history(request: Request, days: int = Query(30, ge=1, le=60)):
    _require_auth(request)
    snapshots = dnsbl.get_history(days)
    return JSONResponse({"days": days, "snapshots": snapshots})


@router.post("/blacklist/recheck")
async def recheck(request: Request):
    _require_auth(request)
    # Rate-limit assumes single-worker uvicorn (race-free between read+write).
    # Multi-worker would need atomic CAS or shared lock (e.g., redis SETNX).
    last_at = _read_recheck_lock()
    now = time.time()
    elapsed = now - last_at
    if elapsed < RECHECK_RATE_LIMIT_S:
        retry_after = int(RECHECK_RATE_LIMIT_S - elapsed) + 1
        return JSONResponse(
            {"error": f"rate limit: retry in {retry_after}s"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    snap = await dnsbl.refresh_and_persist()
    _write_recheck_lock(now)
    listed_count = sum(1 for r in snap.results if r.status == "listed")
    audit("blacklist.recheck", listed_count=listed_count, by=_get_session_email(request))
    return JSONResponse(_serialize_snapshot(snap))
