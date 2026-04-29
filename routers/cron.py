"""Token-guarded cron endpoints (mailbox stats refresh, DNSBL snapshot, etc.)."""
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from services import dnsbl, mailbox_stats

router = APIRouter()


def _check_cron_token(x_cron_token: Optional[str], env_var: str) -> None:
    expected = os.getenv(env_var, "")
    if not expected or not x_cron_token or not hmac.compare_digest(x_cron_token, expected):
        raise HTTPException(status_code=401, detail="invalid cron token")


@router.post("/cron/refresh-mailbox-stats")
async def refresh_mailbox_stats(
    x_cron_token: Optional[str] = Header(None, alias="X-Cron-Token"),
):
    _check_cron_token(x_cron_token, "MAILBOX_STATS_CRON_TOKEN")
    return await mailbox_stats.refresh()


@router.post("/cron/dnsbl-snapshot")
async def cron_dnsbl_snapshot(
    x_cron_token: Optional[str] = Header(None, alias="X-Cron-Token"),
):
    """HMAC-token-guarded DNSBL snapshot trigger. Calls refresh_and_persist
    (cache-bypass), so cron runs always produce a fresh datapoint regardless
    of in-mem TTL."""
    _check_cron_token(x_cron_token, "DNSBL_SNAPSHOT_CRON_TOKEN")
    snap = await dnsbl.refresh_and_persist()
    listed = sum(1 for r in snap.results if r.status == "listed")
    return JSONResponse({"ok": True, "ts": snap.ts, "listed_count": listed})
