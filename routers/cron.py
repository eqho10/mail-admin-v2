"""Token-guarded cron endpoints (mailbox stats refresh, etc.)."""
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from services import mailbox_stats

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
