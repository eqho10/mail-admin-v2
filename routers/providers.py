"""Mail provider status JSON endpoint — Faz 6.U (2026-05-01).

GET /api/providers/status → per-domain primary/fallback config + live
Mailjet/Brevo API status (when env keys present).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from services import providers as svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/providers/status")
async def api_providers_status(request: Request) -> dict[str, Any]:
    from app import require_auth
    require_auth(request)

    rows = await asyncio.to_thread(svc.list_relay_configs)
    summary = svc.summarize(rows)

    mailjet, brevo = await asyncio.gather(
        svc.get_mailjet_status(),
        svc.get_brevo_status(),
        return_exceptions=True,
    )
    if isinstance(mailjet, Exception):
        mailjet = {"configured": False, "reachable": False, "message": str(mailjet)}
    if isinstance(brevo, Exception):
        brevo = {"configured": False, "reachable": False, "message": str(brevo)}

    return {
        "summary": summary,
        "domains": rows,
        "providers": {"mailjet": mailjet, "brevo": brevo},
    }
