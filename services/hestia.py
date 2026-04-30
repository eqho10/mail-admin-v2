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
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from services.error_translator import translate


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


async def _cli_get_json(cmd: str, *args: str) -> dict:
    """Read-side fallback: invoke v-list-* via local CLI subprocess, parse JSON.

    HestiaCP's `/api/` HTTP endpoint isn't reachable in this deployment
    (port 8083 closed), so we fall back to the same CLI commands the v1
    panel uses. Wrapped in asyncio.to_thread so the FastAPI loop isn't
    blocked while subprocess runs.
    """
    def _run():
        argv = [f"{HESTIA_BIN}/{cmd}", *args]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise HestiaAPIError(
                f"CLI {cmd} rc={result.returncode}: {(result.stderr or '')[:200]}"
            )
        out = (result.stdout or "").strip()
        if not out:
            return {}
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError) as e:
            raise HestiaAPIError(f"CLI {cmd} returned non-JSON: {e}") from e
    return await asyncio.to_thread(_run)


async def _api_get(cmd: str, *args: str) -> dict:
    """HTTP-or-CLI shim for HestiaCP read commands.

    Tries the HTTP API first when configured; on any failure (or when
    HESTIA_API_URL/HESTIA_API_KEY are unset) falls through to a local
    CLI subprocess. The CLI path is the only one that actually ships
    today because HestiaCP's API port is closed in this deployment;
    the HTTP path is kept as a future-proof option.
    """
    if HESTIA_API_URL and HESTIA_API_KEY:
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
        except (httpx.HTTPError, HestiaAPIError, ValueError) as e:
            logging.getLogger(__name__).warning(
                "HestiaCP HTTP API failed (%s); falling back to CLI", e
            )
    return await _cli_get_json(cmd, *args)


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


async def list_forwarders(domain: str, user: str) -> dict:
    """Return {forwarders: [...], fwd_only: bool} for a mailbox.
    Forwarders are full addresses (user@example.com). FWD_ONLY=yes means local delivery is disabled."""
    if not RE_DOMAIN.match(domain) or not RE_LOCAL.match(user):
        raise HestiaAPIError(f"invalid domain/user: {domain!r}/{user!r}")

    async def _fetch():
        data = await _api_get(
            "v-list-mail-account", HESTIA_USER, domain, user, "json"
        )
        if not isinstance(data, dict):
            return {"forwarders": [], "fwd_only": False}
        rec = data.get(user, {})
        if not isinstance(rec, dict):
            return {"forwarders": [], "fwd_only": False}
        raw_fwd = rec.get("FWD", "")
        fwd_only = str(rec.get("FWD_ONLY", "")).lower() in ("yes", "true", "1")
        forwarders = [x.strip() for x in str(raw_fwd).split(",") if x.strip()] if raw_fwd else []
        return {"forwarders": forwarders, "fwd_only": fwd_only}

    return await _cached(("list_forwarders", domain, user), _fetch)


async def get_autoreply(domain: str, user: str) -> dict:
    """Return {enabled: bool, message: str} — autoreply state for a mailbox.
    Uses v-list-mail-account for AUTOREPLY flag and v-list-mail-account-autoreply for body."""
    if not RE_DOMAIN.match(domain) or not RE_LOCAL.match(user):
        raise HestiaAPIError(f"invalid domain/user: {domain!r}/{user!r}")

    async def _fetch():
        info = await _api_get(
            "v-list-mail-account", HESTIA_USER, domain, user, "json"
        )
        enabled = False
        if isinstance(info, dict):
            rec = info.get(user, {})
            if isinstance(rec, dict):
                enabled = str(rec.get("AUTOREPLY", "")).lower() in ("yes", "true", "1")

        msg = ""
        if enabled:
            body = await _api_get(
                "v-list-mail-account-autoreply", HESTIA_USER, domain, user, "json"
            )
            if isinstance(body, dict):
                rec2 = body.get("info", {})
                if isinstance(rec2, dict):
                    msg = str(rec2.get("MSG", "")).strip()
        return {"enabled": enabled, "message": msg}

    return await _cached(("get_autoreply", domain, user), _fetch)


# ====================== Write CLI ======================

def _run_cli(argv: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Sync subprocess wrapper. argv-list (no shell), capture stdout/stderr.
    Returns (returncode, stdout, stderr). Translates timeout to a sentinel
    that the caller can detect via stderr content."""
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "HestiaCP CLI timeout after %ds" % timeout


def _check_password(password: str) -> None:
    if not password or len(password) < 12:
        raise HestiaCLIError(
            translated={"id": "hestia_password_policy", "title": "Şifre kısa",
                        "body": "Şifre en az 12 karakter olmalı.", "severity": "warning"},
        )
    if not re.search(r"\d", password):
        raise HestiaCLIError(
            translated={"id": "hestia_password_policy", "title": "Şifre rakam içermiyor",
                        "body": "Şifre en az 1 rakam içermeli.", "severity": "warning"},
        )
    if not re.search(r"[^a-zA-Z0-9]", password):
        raise HestiaCLIError(
            translated={"id": "hestia_password_policy", "title": "Şifre sembol içermiyor",
                        "body": "Şifre en az 1 sembol içermeli.", "severity": "warning"},
        )


def _check_local(name: str, label: str = "user") -> None:
    if not RE_LOCAL.match(name or ""):
        raise HestiaCLIError(
            translated={"id": "hestia_invalid_local", "title": f"Geçersiz {label}",
                        "body": f"{label} sadece a-z, 0-9, ., _, - içerebilir (1-64 karakter).",
                        "severity": "warning"},
        )


def _check_domain(domain: str) -> None:
    if not RE_DOMAIN.match(domain or ""):
        raise HestiaCLIError(
            translated={"id": "hestia_invalid_domain_format", "title": "Geçersiz domain",
                        "body": f"Domain formatı geçersiz: {domain!r}", "severity": "error"},
        )


def _check_quota(quota_mb: int) -> None:
    if not isinstance(quota_mb, int) or quota_mb < 1 or quota_mb > 1_000_000:
        raise HestiaCLIError(
            translated={"id": "hestia_invalid_quota", "title": "Quota aralık dışı",
                        "body": "Quota MB cinsinden 1-1.000.000 aralığında olmalı.",
                        "severity": "error"},
        )


def _check_email(email: str) -> None:
    if not RE_EMAIL.match(email or ""):
        raise HestiaCLIError(
            translated={"id": "hestia_invalid_email_format", "title": "Geçersiz e-posta",
                        "body": f"E-posta formatı hatalı: {email!r}", "severity": "warning"},
        )


def _post_write(domain: str) -> None:
    """Cache invalidation + trigger file touch (called after successful write)."""
    _cache_invalidate_domain(domain)
    try:
        TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRIGGER_FILE.touch()
    except OSError:
        pass


def _cli_or_raise(argv: list[str], domain: str, timeout: int = 10) -> None:
    """Common write path: run CLI, translate stderr on failure, post-write hook on success.

    AUDIT NOTE: Write functions here do NOT call audit() — that responsibility
    is delegated to router callers (Tasks 9-12). This keeps the service layer
    pure and avoids duplicate audit entries when multiple call sites converge.
    """
    rc, _out, err = _run_cli(argv, timeout=timeout)
    if rc != 0:
        translated = translate(err.strip() or "HestiaCP CLI failed with no output")
        raise HestiaCLIError(translated=translated, raw_stderr=err)
    _post_write(domain)


def add_mailbox(domain: str, user: str, password: str, quota_mb: int) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _check_password(password)
    _check_quota(quota_mb)
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-add-mail-account", HESTIA_USER, domain, user, password, str(quota_mb)],
        domain, timeout=20,
    )


def delete_mailbox(domain: str, user: str) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-delete-mail-account", HESTIA_USER, domain, user],
        domain, timeout=20,
    )


def change_password(domain: str, user: str, password: str) -> None:
    """Admin-override password reset. Caller must enforce permission/old-password gate."""
    _check_domain(domain)
    _check_local(user, "user")
    _check_password(password)
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-change-mail-account-password", HESTIA_USER, domain, user, password],
        domain, timeout=20,
    )


def change_quota(domain: str, user: str, quota_mb: int) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _check_quota(quota_mb)
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-change-mail-account-quota", HESTIA_USER, domain, user, str(quota_mb)],
        domain, timeout=15,
    )


def add_alias(domain: str, user: str, alias_local: str) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _check_local(alias_local, "alias")
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-add-mail-account-alias", HESTIA_USER, domain, user, alias_local],
        domain, timeout=15,
    )


def delete_alias(domain: str, user: str, alias_local: str) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _check_local(alias_local, "alias")
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-delete-mail-account-alias", HESTIA_USER, domain, user, alias_local],
        domain, timeout=15,
    )


def set_forward(domain: str, user: str, forward_to: str) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _check_email(forward_to)
    # Real HestiaCP CLI is `v-add-mail-account-forward` (not `-fwd`); verified
    # 2026-04-29 against /usr/local/hestia/bin on the live VPS.
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-add-mail-account-forward", HESTIA_USER, domain, user, forward_to],
        domain, timeout=15,
    )


def delete_forward(domain: str, user: str, forward_to: str) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _check_email(forward_to)
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-delete-mail-account-forward", HESTIA_USER, domain, user, forward_to],
        domain, timeout=15,
    )


def set_fwd_only(domain: str, user: str, on: bool) -> None:
    """Toggle FWD_ONLY mode: when True, local delivery is disabled — only forwards receive mail."""
    _check_domain(domain)
    _check_local(user, "user")
    cmd = "v-add-mail-account-fwd-only" if on else "v-delete-mail-account-fwd-only"
    _cli_or_raise(
        [f"{HESTIA_BIN}/{cmd}", HESTIA_USER, domain, user],
        domain, timeout=15,
    )


def set_autoreply(domain: str, user: str, body: str) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    if not body or len(body) > 4000:
        raise HestiaCLIError(
            translated={"id": "hestia_invalid_autoreply", "title": "Auto-reply gövdesi geçersiz",
                        "body": "Boş olamaz, max 4000 karakter.", "severity": "warning"},
        )
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-add-mail-account-autoreply", HESTIA_USER, domain, user, body],
        domain, timeout=15,
    )


def clear_autoreply(domain: str, user: str) -> None:
    _check_domain(domain)
    _check_local(user, "user")
    _cli_or_raise(
        [f"{HESTIA_BIN}/v-delete-mail-account-autoreply", HESTIA_USER, domain, user],
        domain, timeout=15,
    )
