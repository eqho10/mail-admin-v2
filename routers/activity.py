"""Activity router — /aktivite HTML + /api/activity, /api/message/{id}, /api/events/stream"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from services.exim import (
    read_tail, aggregate_messages, parse_line, EXIM_MAINLOG,
)
from services.audit import audit
from services.templates import _ctx

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ALLOWED_SSE_TOPICS = {"activity", "send_as_test"}


def _require_auth(request: Request):
    """Lazy import from app to avoid circular dependency at module load time."""
    from app import require_auth
    return require_auth(request)


@router.get("/aktivite", response_class=HTMLResponse)
async def page_activity(request: Request):
    _require_auth(request)
    from app import get_session
    return templates.TemplateResponse(request, "pages/activity.html", _ctx(
        request,
        current_page="activity",
        page_title="Aktivite",
        breadcrumb=[{"label": "Aktivite", "href": None}],
        user_email=get_session(request) or "",
    ))


@router.get("/api/activity")
async def api_activity(
    request: Request,
    topic: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
):
    _require_auth(request)

    if topic == "blacklist":
        from services import dnsbl
        from pathlib import Path
        try:
            p = Path(dnsbl.ALERTS_PATH)
            alerts_data = json.loads(p.read_text()) if p.exists() else []
        except (json.JSONDecodeError, OSError):
            alerts_data = []
        events = [
            {
                "type": "blacklist_listed",
                "ts": a.get("ts"),
                "title": f"IP {a.get('ip')} listed on {a.get('zone')}",
                "detail": a.get("return_code"),
                "zone": a.get("zone"),
                "ip": a.get("ip"),
            }
            for a in alerts_data[-limit:]
        ]
        events.sort(key=lambda x: x.get("ts") or "", reverse=True)
        return {"events": events, "count": len(events)}

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


from pathlib import Path as _Path


@router.get("/api/message/{msgid}/body")
async def api_message_body(request: Request, msgid: str, recipient: Optional[str] = Query(None)):
    _require_auth(request)
    if not recipient:
        # Try to resolve recipient from mainlog
        lines = read_tail(EXIM_MAINLOG, n_lines=5000)
        matched = [l for l in lines if msgid in l]
        for line in matched:
            parsed = parse_line(line)
            if parsed and parsed.get("to"):
                recipient = parsed["to"][0] if isinstance(parsed["to"], list) else parsed["to"]
                break
        if not recipient:
            raise HTTPException(400, "recipient query param required")

    from services.maildir import find_by_msgid, parse_message
    file_path = find_by_msgid(msgid, recipient)
    if not file_path:
        return JSONResponse(status_code=404, content={
            "error": "maildir_not_found",
            "reason": "outbound_or_deleted_or_no_account",
        })
    with open(file_path, "rb") as f:
        raw_bytes = f.read()
    parsed = parse_message(raw_bytes)
    parsed["source_file"] = file_path
    audit("message.body.view", msgid=msgid, recipient=recipient)
    return parsed


@router.get("/api/message/{msgid}/attachment/{idx}")
async def api_message_attachment(
    request: Request, msgid: str, idx: int,
    recipient: str = Query(...),
):
    _require_auth(request)
    from services.maildir import find_by_msgid, get_attachment
    file_path = find_by_msgid(msgid, recipient)
    if not file_path:
        raise HTTPException(404, "maildir not found")
    with open(file_path, "rb") as f:
        raw_bytes = f.read()
    att = get_attachment(raw_bytes, idx)
    if not att:
        raise HTTPException(404, "attachment not found")
    safe_name = _Path(att["filename"]).name  # block path traversal
    audit("message.attachment.download", msgid=msgid, idx=idx, filename=safe_name)
    return Response(
        content=att["payload"],
        media_type=att["content_type"],
        headers={
            "Content-Disposition": f"attachment; filename=\"{safe_name}\"",
        },
    )
