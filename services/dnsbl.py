"""DNSBL outbound check — parallel async lookup + 60s in-mem cache + history snapshot.

Task 2 (Faz 4b) defines: zone list, ZoneResult/Snapshot/AlertEvent dataclasses,
check_zone() with 4-state outcome, get_status() with asyncio.Lock + 60s cache.

Task 3 (Faz 4b) appends: refresh_and_persist() (cache-bypass + atomic write +
history rolling + alert fan-out), diff_for_alert() (clean/timeout/error → listed
transition detector), and file persistence helpers (atomic JSON, snapshot
serialization).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import asdict, dataclass
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
    "refresh_and_persist",
    "diff_for_alert",
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


# Used by diff_for_alert() / refresh_and_persist().
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
    try:
        socket.inet_aton(ip)
    except OSError:
        raise ValueError(f"DNSBL_CHECK_IP is not a valid IPv4 address: {ip!r}")
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


# ---------------------------------------------------------------------------
# Task 3: refresh_and_persist + history rolling + alert diff.
# ---------------------------------------------------------------------------
def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _snapshot_to_dict(snap: Snapshot) -> dict:
    return {
        "ts": snap.ts,
        "ip": snap.ip,
        "results": [asdict(r) for r in snap.results],
    }


def _dict_to_snapshot(d: dict) -> Snapshot:
    results = [ZoneResult(**r) for r in d.get("results", [])]
    return Snapshot(ts=d["ts"], ip=d["ip"], results=results)


def diff_for_alert(prev: Optional[Snapshot], curr: Snapshot) -> list[AlertEvent]:
    """Emit AlertEvent for each zone that transitioned from clean/timeout/error → listed.

    If prev is None (first snapshot), treats all prior states as 'clean' for diff
    purposes — so a brand-new install with already-listed zones still alerts once.
    Delisting (listed → clean) is intentionally NOT alerted.
    """
    prev_map: dict[str, str] = {}
    if prev is not None:
        prev_map = {r.zone: r.status for r in prev.results}
    events: list[AlertEvent] = []
    for r in curr.results:
        if r.status != "listed":
            continue
        prev_status = prev_map.get(r.zone, "clean")  # missing → clean
        if prev_status == "listed":
            continue
        events.append(AlertEvent(ts=curr.ts, zone=r.zone, return_code=r.return_code, ip=curr.ip))
    return events


def _append_alert_events(events: list[AlertEvent]) -> None:
    if not events:
        return
    existing = _read_json(ALERTS_PATH, [])
    new = [asdict(e) for e in events]
    combined = existing + new
    if len(combined) > ALERTS_MAX:
        combined = combined[-ALERTS_MAX:]
    _atomic_write_json(ALERTS_PATH, combined)


async def refresh_and_persist() -> Snapshot:
    """Cache-bypass refresh. Writes latest, appends history (rolling), fires alerts."""
    global _cache, _cache_at
    async with _lock:
        # Read prior snapshot for diff
        prev_dict = _read_json(LATEST_PATH, None)
        prev = _dict_to_snapshot(prev_dict) if prev_dict else None

        snap = await _perform_lookup()

        # Write latest atomically
        _atomic_write_json(LATEST_PATH, _snapshot_to_dict(snap))

        # Append to history (rolling 120)
        history = _read_json(HISTORY_PATH, [])
        history.append(_snapshot_to_dict(snap))
        if len(history) > HISTORY_MAX:
            history = history[-HISTORY_MAX:]
        _atomic_write_json(HISTORY_PATH, history)

        # Diff + alert
        events = diff_for_alert(prev, snap)
        _append_alert_events(events)

        # Update cache
        _cache = snap
        _cache_at = time.time()
        return snap
