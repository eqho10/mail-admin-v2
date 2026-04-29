"""DNSBL outbound check — parallel async lookup + 60s in-mem cache + history snapshot.

Task 2 (Faz 4b) defines: zone list, ZoneResult/Snapshot/AlertEvent dataclasses,
check_zone() with 4-state outcome, get_status() with asyncio.Lock + 60s cache.

Task 3 will append: refresh_and_persist(), diff_for_alert(), file persistence
helpers. To avoid 'from services.dnsbl import *' blowing up before Task 3 lands,
__all__ exports only the names actually defined here.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import dns.asyncresolver
import dns.exception
import dns.resolver

__all__ = [
    "DNSBL_ZONES",
    "ZoneResult",
    "Snapshot",
    "AlertEvent",
    "check_zone",
    "get_status",
]

# 20 free + active zones, hardcoded.
DNSBL_ZONES: tuple[str, ...] = (
    "zen.spamhaus.org",
    "b.barracudacentral.org",
    "bl.spamcop.net",
    "dnsbl.sorbs.net",
    "cbl.abuseat.org",
    "dnsbl-1.uceprotect.net",
    "dnsbl-2.uceprotect.net",
    "dnsbl-3.uceprotect.net",
    "bl.mailspike.net",
    "z.mailspike.net",
    "psbl.surriel.com",
    "ubl.unsubscore.com",
    "bl.0spam.org",
    "truncate.gbudb.net",
    "dnsbl.dronebl.org",
    "ips.backscatterer.org",
    "db.wpbl.info",
    "spam.dnsbl.anonmails.de",
    "bl.blocklist.de",
    "all.s5h.net",
)

CACHE_TTL_S = 60.0
HISTORY_MAX = 120  # 4×/day × 30 days
ALERTS_MAX = 100

DATA_DIR = Path(os.environ.get("MAIL_ADMIN_DATA_DIR", "data"))
LATEST_PATH = DATA_DIR / "dnsbl_latest.json"
HISTORY_PATH = DATA_DIR / "dnsbl_history.json"
ALERTS_PATH = DATA_DIR / "blacklist_alerts.json"


@dataclass
class ZoneResult:
    zone: str
    status: str  # "clean" | "listed" | "timeout" | "error"
    return_code: Optional[str]  # e.g. "127.0.0.4"
    return_text: Optional[str]
    duration_ms: int


@dataclass
class Snapshot:
    ts: str
    ip: str
    results: list[ZoneResult]


@dataclass
class AlertEvent:
    ts: str
    zone: str
    return_code: Optional[str]
    ip: str


def _reverse_ipv4(ip: str) -> str:
    return ".".join(reversed(ip.split(".")))


async def check_zone(ip: str, zone: str, timeout: float = 3.0) -> ZoneResult:
    qname = f"{_reverse_ipv4(ip)}.{zone}"
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = timeout
    started = time.monotonic()
    try:
        answer = await resolver.resolve(qname, "A")
        rc = str(answer[0])
        return ZoneResult(zone, "listed", rc, None, int((time.monotonic() - started) * 1000))
    except dns.resolver.NXDOMAIN:
        return ZoneResult(zone, "clean", None, None, int((time.monotonic() - started) * 1000))
    except dns.exception.Timeout:
        return ZoneResult(zone, "timeout", None, None, int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ZoneResult(zone, "error", None, str(e), int((time.monotonic() - started) * 1000))


# ---------------------------------------------------------------------------
# get_status: parallel lookup + 60s in-mem cache.
# ---------------------------------------------------------------------------
_cache: Optional[Snapshot] = None
_cache_at: float = 0.0
_lock = asyncio.Lock()


async def _perform_lookup() -> Snapshot:
    ip = os.environ.get("DNSBL_CHECK_IP", "153.92.1.179")
    coros = [check_zone(ip, z) for z in DNSBL_ZONES]
    results = await asyncio.gather(*coros)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return Snapshot(ts=ts, ip=ip, results=list(results))


async def get_status() -> Snapshot:
    """Cache-aware read. Returns cached if within TTL, else performs lookup."""
    global _cache, _cache_at
    async with _lock:
        now = time.time()
        if _cache and (now - _cache_at) < CACHE_TTL_S:
            return _cache
        snap = await _perform_lookup()
        _cache = snap
        _cache_at = now
        return snap
