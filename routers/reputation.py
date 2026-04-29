# routers/reputation.py
"""Reputation API: current (5min cache) + history + cron snapshot (HMAC)."""
import os
import time
import hmac
from typing import Optional

from fastapi import APIRouter, Request, Header, HTTPException, Query

import services.reputation as _rep

router = APIRouter()

_CACHE_TTL = 300  # 5 min
_cache: dict = {'data': None, 'ts': 0.0}


def _cache_clear():
    """Test helper — wipe in-memory cache."""
    _cache['data'] = None
    _cache['ts'] = 0.0


def _require_auth(request: Request):
    from app import require_auth
    return require_auth(request)


@router.get("/api/reputation/current")
async def api_reputation_current(request: Request):
    _require_auth(request)
    now = time.time()
    if _cache['data'] is not None and (now - _cache['ts']) < _CACHE_TTL:
        return _cache['data']

    exim = _rep._fetch_exim_window(days=30)
    brevo = _rep._fetch_brevo_complaints(days=30, total_sent=exim['total_sent'])
    complaint_rate = brevo['complaint_rate'] if brevo else None
    complaint_count = brevo['complaint_count'] if brevo else None
    score = _rep.composite_score(
        bounce_rate=exim['bounce_rate'],
        complaint_rate=complaint_rate,
        deferred_rate=exim['deferred_rate'],
    )
    payload = {
        'score': score,
        'bounce_rate': exim['bounce_rate'],
        'complaint_rate': complaint_rate,
        'deferred_rate': exim['deferred_rate'],
        'total_sent': exim['total_sent'],
        'complaint_count': complaint_count,
        'complaint_available': brevo is not None,
        'computed_at': time.time(),
    }
    _cache['data'] = payload
    _cache['ts'] = now
    return payload


@router.get("/api/reputation/history")
async def api_reputation_history(request: Request, days: int = Query(30, ge=1, le=90)):
    _require_auth(request)
    points = _rep.query_history(days=days)
    return {'points': points, 'count': len(points)}


@router.post("/api/reputation/snapshot")
async def api_reputation_snapshot(
    request: Request,
    x_cron_token: Optional[str] = Header(None, alias="X-Cron-Token"),
):
    expected = os.getenv("REPUTATION_CRON_TOKEN", "")
    if not expected or not x_cron_token or not hmac.compare_digest(x_cron_token, expected):
        raise HTTPException(401, "invalid cron token")
    result = _rep.snapshot_now(source='cron')
    _cache_clear()  # invalidate so next /current returns fresh
    return result
