"""Activity router — /aktivite HTML + /api/activity, /api/message/{id}, /api/events/stream"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from services.exim import (
    read_tail, aggregate_messages, parse_line, EXIM_MAINLOG,
)
from services.audit import audit

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ALLOWED_SSE_TOPICS = {"activity"}


def _require_auth(request: Request):
    """Lazy import from app to avoid circular dependency at module load time."""
    from app import require_auth
    return require_auth(request)


@router.get("/aktivite", response_class=HTMLResponse)
async def page_activity(request: Request):
    _require_auth(request)
    from app import get_session
    return templates.TemplateResponse(request, "pages/activity.html", {
        "current_page": "activity",
        "page_title": "Aktivite",
        "breadcrumb": [{"label": "Aktivite", "href": None}],
        "user_email": get_session(request) or "",
    })


@router.get("/api/activity")
async def api_activity(
    request: Request,
    status: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
):
    _require_auth(request)
    lines = read_tail(EXIM_MAINLOG, n_lines=2000)
    msgs = aggregate_messages(lines)

    if status:
        msgs = [m for m in msgs if m.get("status") == status]
    if domain:
        dl = domain.lower()
        msgs = [m for m in msgs if dl in (m.get("from") or "").lower()
                or any(dl in (t or "").lower() for t in (m.get("to") or []))]
    if q:
        ql = q.lower()
        msgs = [m for m in msgs if ql in json.dumps(m, default=str).lower()]
    msgs = msgs[:limit]
    return {"messages": msgs, "count": len(msgs)}


@router.get("/api/message/{msgid}")
async def api_message_detail(request: Request, msgid: str):
    _require_auth(request)
    lines = read_tail(EXIM_MAINLOG, n_lines=5000)
    matched_lines = [l for l in lines if msgid in l]
    if not matched_lines:
        raise HTTPException(404, f"msgid {msgid} bulunamadı")
    parsed = [parse_line(l) for l in matched_lines]
    parsed = [p for p in parsed if p]
    aggregated = aggregate_messages(matched_lines)
    if not aggregated:
        raise HTTPException(404, f"msgid {msgid} parse edilemedi")
    audit("message.detail.view", msgid=msgid)
    return {
        "message": aggregated[0],
        "events": parsed,
        "raw_lines": matched_lines,
    }


@router.get("/api/events/stream")
async def api_events_stream(request: Request, topic: str = Query("activity")):
    _require_auth(request)
    if topic not in ALLOWED_SSE_TOPICS:
        raise HTTPException(400, f"bilinmeyen topic: {topic}")

    async def event_generator():
        last_size = 0
        initialized = False
        while True:
            if await request.is_disconnected():
                break
            try:
                with open(EXIM_MAINLOG, "rb") as f:
                    if not initialized:
                        f.seek(0, 2)
                        last_size = f.tell()
                        yield f"event: connected\ndata: {{\"topic\":\"{topic}\"}}\n\n"
                        initialized = True
                    else:
                        # Check for log rotation (file shrunk → reset to start)
                        f.seek(0, 2)
                        current_size = f.tell()
                        if current_size < last_size:
                            last_size = 0  # rotated, read from beginning of new file
                        f.seek(last_size)
                        new = f.read()
                        if new:
                            for raw_line in new.decode("utf-8", errors="replace").splitlines():
                                parsed = parse_line(raw_line)
                                if parsed:
                                    yield f"event: line\ndata: {json.dumps(parsed)}\n\n"
                            last_size = f.tell()
            except FileNotFoundError:
                yield f"event: error\ndata: {{\"message\":\"mainlog yok\"}}\n\n"
                return
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
