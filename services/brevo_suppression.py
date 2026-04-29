"""Brevo suppression list wrapper — list (cached) + remove (flushes cache).

Verified 2026-04-29 against api.brevo.com/v3/smtp/blockedContacts:

  GET /v3/smtp/blockedContacts response shape:
    {"contacts": [
       {"email": "x@y.com",
        "reason": {"code": "hardBounce",
                   "message": "This contact's email address generated..."},
        "blockedAt": "2026-04-27T11:45:09.000Z"}
     ], "count": <int>}

  Allowed reason.code values (per official Brevo Python SDK enum
  GetTransacBlockedContactsReason.code, sib_api_v3_sdk):
    - hardBounce
    - contactFlaggedAsSpam
    - adminBlocked
    - unsubscribedViaEmail
    - unsubscribedViaApi
    - unsubscribedViaMA
    (no softBounce — soft bounces don't end up on the blocked list)

  Query params (per Brevo Python SDK get_transac_blocked_contacts):
    startDate, endDate, limit, offset, senders, sort.
    `senders` is "comma separated list of EMAILS of the senders" — NOT a
    reason/category filter. Brevo API does NOT support server-side filter
    by reason code. So we paginate and filter client-side using _REASON_MAP.

  Unknown query params (e.g. `reason=hardBounce`, `type=hardBounce`) are
  silently ignored — verified live (returns full unfiltered set).
"""
import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx


BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_BASE = "https://api.brevo.com/v3"
CACHE_TTL_SEC = 300

# UI tab id → Brevo `reason.code` values to match.
# Empty list = no filter (return all). Used CLIENT-SIDE because the Brevo API
# does not expose a reason-code filter (verified 2026-04-29).
_REASON_MAP: dict[str, list[str]] = {
    "all": [],
    "hard": ["hardBounce"],
    "soft": [],  # Brevo does not put soft bounces in the blocked list; tab returns empty
    "blocked": ["adminBlocked"],
    "unsub": ["unsubscribedViaEmail", "unsubscribedViaApi", "unsubscribedViaMA"],
    "spam": ["contactFlaggedAsSpam"],
}

_cache: dict = {}
_cache_lock = asyncio.Lock()


class BrevoSuppressionError(Exception):
    """API auth, network, or upstream error from Brevo suppression endpoints."""


@dataclass
class Block:
    email: str
    reason: str       # the verbatim `reason.code` from Brevo (e.g. "hardBounce")
    blocked_at: str   # ISO8601 string from `blockedAt`


def _cache_clear():
    """Test helper / write-side cache flush."""
    _cache.clear()


def _cache_flush_email(email: str):
    """Drop all blocked-* cache entries; per-email tracking would be overkill."""
    for key in list(_cache.keys()):
        if key[0] == "blocked":
            _cache.pop(key, None)


async def _api_request(method: str, path: str, params: Optional[dict] = None) -> dict:
    if not BREVO_API_KEY:
        raise BrevoSuppressionError("BREVO_API_KEY not configured")
    headers = {"api-key": BREVO_API_KEY, "accept": "application/json"}
    url = f"{BREVO_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers, params=params or {})
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:
                raise BrevoSuppressionError(f"unsupported method: {method}")
            if resp.status_code in (401, 403):
                raise BrevoSuppressionError(f"Brevo API {resp.status_code} unauthorized")
            if resp.status_code == 204:
                return {}
            resp.raise_for_status()
            return resp.json() if resp.content else {}
    except BrevoSuppressionError:
        raise
    except httpx.HTTPError as e:
        raise BrevoSuppressionError(f"Brevo API error: {e}") from e


async def list_blocked(category: str = "all", limit: int = 100, offset: int = 0) -> list[Block]:
    """List suppressed contacts.

    `category` is one of _REASON_MAP keys; filtering happens CLIENT-SIDE
    after fetching from Brevo (their API has no reason-code filter).
    Result is cached for CACHE_TTL_SEC seconds keyed by (category, limit, offset).
    """
    key = ("blocked", category, limit, offset)
    now = time.time()
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and (now - entry[0]) < CACHE_TTL_SEC:
            return entry[1]

    params: dict = {"limit": limit, "offset": offset}
    # NOTE: We intentionally do NOT pass `senders` to Brevo here. `senders` is
    # a sender-email filter, not a reason-code filter. Filtering by reason
    # code is done client-side below.
    data = await _api_request("GET", "/smtp/blockedContacts", params=params)
    contacts = data.get("contacts", []) if isinstance(data, dict) else []

    wanted_codes = set(_REASON_MAP.get(category, []))
    blocks: list[Block] = []
    for c in contacts:
        reason = c.get("reason", {})
        if isinstance(reason, dict):
            reason_code = reason.get("code", "unknown")
        else:
            reason_code = str(reason)
        if wanted_codes and reason_code not in wanted_codes:
            continue
        blocks.append(Block(
            email=c.get("email", ""),
            reason=reason_code,
            blocked_at=c.get("blockedAt", ""),
        ))

    async with _cache_lock:
        _cache[key] = (time.time(), blocks)
    return blocks


async def remove_from_suppression(email: str) -> None:
    """DELETE /v3/smtp/blockedContacts/{email}. Flushes the blocked-list cache."""
    if not email or "@" not in email:
        raise BrevoSuppressionError(f"invalid email: {email!r}")
    await _api_request("DELETE", f"/smtp/blockedContacts/{email}")
    async with _cache_lock:
        _cache_flush_email(email)
