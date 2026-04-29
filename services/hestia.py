"""HestiaCP integration — read via HTTP API (cached), write via CLI.

Read functions: async, httpx-backed, 5-minute in-memory TTL cache.
Write functions: sync subprocess CLI, callable from async via asyncio.to_thread.
Cache invalidation: every write fn touches data/mailbox_stats.trigger and
invalidates read cache entries matching the affected domain.

Real HestiaCP API response shape (verified 2026-04-29 against live VPS):

  v-list-mail-domains <user> json:
    { "<domain>": { "ACCOUNTS": "2", "U_DISK": "1", "SUSPENDED": "no",
                    "DATE": "2026-02-06", "TIME": "22:40:20", ... } }

  v-list-mail-accounts <user> <domain> json:
    { "<account>": { "ALIAS": "", "FWD": "", "FWD_ONLY": "",
                     "AUTOREPLY": "no", "QUOTA": "unlimited" | "<int>",
                     "U_DISK": "<int>", "SUSPENDED": "no" | "yes",
                     "TIME": "HH:MM:SS", "DATE": "YYYY-MM-DD" } }

  v-list-mail-account <user> <domain> <account> json:
    same shape, single key (the account name).

NOTE: QUOTA is "unlimited" for unrestricted accounts; we map to None
(distinct from explicit 0 MB). U_DISK is in MB (small integers). ALIAS is
comma-separated.
"""
import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx


HESTIA_API_URL = os.getenv("HESTIA_API_URL", "")
HESTIA_API_KEY = os.getenv("HESTIA_API_KEY", "")
HESTIA_USER = os.getenv("HESTIA_USER", "ekrem")
HESTIA_BIN = os.getenv("HESTIA_BIN", "/usr/local/hestia/bin")
TRIGGER_FILE = Path(__file__).resolve().parent.parent / "data" / "mailbox_stats.trigger"

CACHE_TTL_SEC = 300  # 5 minutes
_cache: dict = {}  # key=(fn_name, *args) → (timestamp, value)
# Module-level asyncio.Lock is created at import (no event loop yet).
# Python 3.10+ binds the lock to the running loop on first acquire, so this
# works for our single-loop FastAPI process. If a future test runner spins
# up multiple loops, this lock will need to be lazy-initialized.
_cache_lock = asyncio.Lock()


# Validation regexes (security; spec section 4.2)
RE_LOCAL = re.compile(r"^[a-z0-9._-]{1,64}$")
RE_DOMAIN = re.compile(r"^[a-z0-9.-]{3,253}$")
RE_EMAIL = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class HestiaAPIError(Exception):
    """HTTP API error from HestiaCP (network, 4xx, parse failure)."""


class HestiaCLIError(Exception):
    """CLI subprocess error from HestiaCP (nonzero exit, timeout)."""

    def __init__(self, translated: dict, raw_stderr: str = ""):
        self.translated = translated
        self.raw_stderr = raw_stderr
        super().__init__(translated.get("body", "Hestia CLI error"))


@dataclass
class Mailbox:
    email: str
    domain: str
    user: str
    quota_mb: Optional[int]   # None = unlimited, 0 = literal 0 MB, N = N MB
    used_mb: Optional[int]
    status: str  # 'active' | 'suspended'
    created_at: str  # ISO8601
    alias_count: Optional[int] = None      # filled by stats cron
    last_login: Optional[str] = None       # filled by stats cron
    disk_size_mb: Optional[int] = None     # filled by stats cron


def _cache_clear():
    """Test helper / write-side cache flush."""
    _cache.clear()


def _cache_invalidate_domain(domain: str):
    """Drop all cache entries whose args contain this domain."""
    for key in list(_cache.keys()):
        if domain in key:
            _cache.pop(key, None)


# ---------- Read API ------------------------------------------------------


async def _api_get(cmd: str, *args: str) -> dict:
    """Wrap httpx GET to HestiaCP API. Raises HestiaAPIError on any failure."""
    if not HESTIA_API_URL or not HESTIA_API_KEY:
        raise HestiaAPIError("HestiaCP API URL or key not configured")
    params = {"cmd": cmd}
    for i, a in enumerate(args, 1):
        params[f"arg{i}"] = a
    headers = {"Authorization": f"Bearer {HESTIA_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=True) as client:
            resp = await client.get(
                f"{HESTIA_API_URL}/api/", params=params, headers=headers
            )
            if resp.status_code in (401, 403):
                raise HestiaAPIError(
                    f"HestiaCP API returned {resp.status_code} Unauthorized"
                )
            resp.raise_for_status()
            return resp.json()
    except HestiaAPIError:
        raise
    except httpx.HTTPError as e:
        raise HestiaAPIError(f"HestiaCP API connection error: {e}") from e
    except ValueError as e:  # JSON decode
        raise HestiaAPIError(f"HestiaCP API returned non-JSON: {e}") from e


async def _cached(key: tuple, fetcher):
    """Generic cache wrapper with TTL.

    Two concurrent callers can both miss + both invoke fetcher (deliberate
    trade-off — single-flight would need a Future-in-cache pattern). For
    HestiaCP at admin-UI traffic this is acceptable: at worst one extra
    upstream call per TTL window.
    """
    now = time.time()
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and (now - entry[0]) < CACHE_TTL_SEC:
            return entry[1]
    # Fetch outside lock so concurrent callers don't serialize on slow upstream
    value = await fetcher()
    async with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def _parse_quota_mb(raw) -> Optional[int]:
    """Parse HestiaCP QUOTA field. Returns None for 'unlimited', int otherwise.

    Garbage / unparseable values also return None (more honest than a 0
    sentinel — caller can distinguish "unlimited / unknown" from an
    explicit 0 MB quota).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "unlimited":
        return None
    if s.isdigit():
        return int(s)
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _parse_used_mb(raw) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


async def list_mail_domains() -> list[str]:
    """Return list of mail domain names managed by HESTIA_USER."""

    async def _fetch():
        data = await _api_get("v-list-mail-domains", HESTIA_USER, "json")
        return list(data.keys()) if isinstance(data, dict) else []

    return await _cached(("list_mail_domains",), _fetch)


async def list_mailboxes(domain: str) -> list[Mailbox]:
    """Return mailboxes in a domain (light columns only — no last_login/disk)."""
    if not RE_DOMAIN.match(domain):
        raise HestiaAPIError(f"invalid domain format: {domain!r}")

    async def _fetch():
        data = await _api_get("v-list-mail-accounts", HESTIA_USER, domain, "json")
        boxes: list[Mailbox] = []
        if not isinstance(data, dict):
            return boxes
        for user, info in data.items():
            if not isinstance(info, dict):
                continue
            quota_mb = _parse_quota_mb(info.get("QUOTA"))
            used_mb = _parse_used_mb(info.get("U_DISK"))
            status = (
                "suspended"
                if str(info.get("SUSPENDED", "no")).lower() == "yes"
                else "active"
            )
            date = info.get("DATE", "")
            tt = info.get("TIME", "")
            created = f"{date}T{tt}Z" if date else ""
            boxes.append(
                Mailbox(
                    email=f"{user}@{domain}",
                    domain=domain,
                    user=user,
                    quota_mb=quota_mb,
                    used_mb=used_mb,
                    status=status,
                    created_at=created,
                )
            )
        return boxes

    return await _cached(("list_mailboxes", domain), _fetch)


async def list_aliases(domain: str, user: str) -> list[str]:
    """Return aliases for a mailbox (local parts pointing to user@domain)."""
    if not RE_DOMAIN.match(domain) or not RE_LOCAL.match(user):
        raise HestiaAPIError(f"invalid domain/user: {domain!r}/{user!r}")

    async def _fetch():
        data = await _api_get(
            "v-list-mail-account", HESTIA_USER, domain, user, "json"
        )
        if not isinstance(data, dict):
            return []
        rec = data.get(user, {})
        if not isinstance(rec, dict):
            return []
        raw = rec.get("ALIAS", "")
        if not raw:
            return []
        return [x.strip() for x in str(raw).split(",") if x.strip()]

    return await _cached(("list_aliases", domain, user), _fetch)
