# routers/sendas.py
"""Send-as test API: dispatch + poll."""
import asyncio

from fastapi import APIRouter, Request, HTTPException

import services.sendas as sendas

router = APIRouter()


def _require_auth(request: Request):
    from app import require_auth
    return require_auth(request)


@router.post("/api/sendas/dispatch")
async def api_sendas_dispatch(request: Request):
    _require_auth(request)
    payload = await request.json()
    from_mailbox = payload.get("from")
    to_email = payload.get("to")
    if not from_mailbox or not to_email:
        raise HTTPException(400, "from and to required")

    try:
        result = sendas.dispatch(
            from_mailbox=from_mailbox,
            to_email=to_email,
            subject=payload.get("subject"),
            body=payload.get("body"),
        )
    except sendas.DispatchError as e:
        raise HTTPException(400, str(e))

    # Spawn polling background task — fire-and-forget
    asyncio.create_task(sendas.poll_arrival(result["run_id"], timeout=60, interval=2.0))
    return result


@router.get("/api/sendas/poll/{run_id}")
async def api_sendas_poll(request: Request, run_id: str):
    _require_auth(request)
    state = sendas.get_run_state(run_id)
    if not state:
        raise HTTPException(404, f"run_id {run_id} not found")
    return state
