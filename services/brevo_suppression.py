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

from urllib.parse import quote

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
    reason: str            # the verbatim `reason.code` from Brevo (e.g. "hardBounce")
    blocked_at: str        # ISO8601 string from `blockedAt`
    reason_message: str = ""  # human-readable message from Brevo (reason.message)


def _cache_clear():
    """Test helper / write-side cache flush."""
    _cache.clear()


def _cache_flush_blocked():
    """Drop all cache entries keyed under 'blocked' (used after writes)."""
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
            # Idempotent DELETE: 404 means already-not-suppressed, treat as success
            if resp.status_code == 404 and method == "DELETE":
                return {}
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

    # Brevo caps `limit` at 100 — passing 101 returns 400 Bad Request.
    effective_limit = min(int(limit or 0), 100) or 100
    params: dict = {"limit": effective_limit, "offset": offset}
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
            reason_message = reason.get("message", "") or ""
        else:
            reason_code = str(reason)
            reason_message = ""
        if wanted_codes and reason_code not in wanted_codes:
            continue
        blocks.append(Block(
            email=c.get("email", ""),
            reason=reason_code,
            blocked_at=c.get("blockedAt", ""),
            reason_message=reason_message,
        ))

    async with _cache_lock:
        _cache[key] = (time.time(), blocks)
    return blocks


async def category_counts(limit: int = 100) -> dict[str, int]:
    """Pull up to `limit` blocked contacts and bucket them by UI tab id.

    Returns a dict keyed by every _REASON_MAP key (always present, value 0+).
    Used by the Suppression page to render the KPI strip without 5 round-trips.
    Cached under ('counts', limit) for CACHE_TTL_SEC.
    """
    key = ("counts", limit)
    now = time.time()
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and (now - entry[0]) < CACHE_TTL_SEC:
            return entry[1]

    all_blocks = await list_blocked(category="all", limit=limit, offset=0)
    code_to_tab: dict[str, str] = {}
    for tab, codes in _REASON_MAP.items():
        for c in codes:
            code_to_tab[c] = tab
    counts: dict[str, int] = {tab: 0 for tab in _REASON_MAP}
    counts["all"] = len(all_blocks)
    for b in all_blocks:
        tab = code_to_tab.get(b.reason)
        if tab:
            counts[tab] = counts.get(tab, 0) + 1

    async with _cache_lock:
        _cache[key] = (time.time(), counts)
    return counts


async def add_to_suppression(email: str, reason: str = "adminBlocked") -> None:
    """Manually add an address to the Brevo blocked list.

    Brevo endpoint: POST /v3/smtp/blockedContacts/{email}? — actually the
    documented way is POST /v3/smtp/blockedDomains for domains; for individual
    contacts we call POST /v3/smtp/blockedContacts with body. Verified API:
        POST /v3/smtp/blockedContacts
        body: { "email": "x@y.com", "reason": { "code": "adminBlocked" } }
    Returns 201 on success.
    """
    if not email or "@" not in email:
        raise BrevoSuppressionError(f"invalid email: {email!r}")
    valid_reasons = {"adminBlocked", "hardBounce", "contactFlaggedAsSpam"}
    if reason not in valid_reasons:
        reason = "adminBlocked"
    body = {"email": email, "reason": {"code": reason}}
    if not BREVO_API_KEY:
        raise BrevoSuppressionError("BREVO_API_KEY not configured")
    headers = {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }
    url = f"{BREVO_BASE}/smtp/blockedContacts"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code in (401, 403):
                raise BrevoSuppressionError(f"Brevo API {resp.status_code} unauthorized")
            if resp.status_code in (200, 201, 204):
                async with _cache_lock:
                    _cache_flush_blocked()
                return
            raise BrevoSuppressionError(
                f"Brevo API {resp.status_code}: {resp.text[:200]}"
            )
    except BrevoSuppressionError:
        raise
    except httpx.HTTPError as e:
        raise BrevoSuppressionError(f"Brevo API error: {e}") from e


async def remove_from_suppression(email: str) -> None:
    """DELETE /v3/smtp/blockedContacts/{email}. Flushes the blocked-list cache."""
    if not email or "@" not in email:
        raise BrevoSuppressionError(f"invalid email: {email!r}")
    encoded = quote(email, safe="@")
    await _api_request("DELETE", f"/smtp/blockedContacts/{encoded}")
    async with _cache_lock:
        _cache_flush_blocked()
