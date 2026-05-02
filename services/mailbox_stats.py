"""Mailbox stats refresher — collects 8 columns per mailbox into JSON file.

Run by systemd timer every 5 min. Write ops touch trigger file to wake
cron. Atomic write (tmp + rename). Per-mailbox errors collected, don't
fail entire refresh.
"""
import asyncio
import json
import os
import re
import subprocess
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

from services.hestia import (
    Mailbox, list_mail_domains, list_mailboxes, list_aliases, list_forwarders, get_autoreply,
)


# Verified 2026-04-29 against live VPS: /var/log/dovecot.log exists and
# contains lines like "Apr 26 00:05:29 imap-login: Info: Login: user=<...>".
# HestiaCP rsyslog routes dovecot to its own file (NOT /var/log/mail.log).
DOVECOT_LOG_PATH = "/var/log/dovecot.log"
HESTIA_USER_HOME = "/home"
# HestiaCP maildir layout (verified 2026-04-29):
#   /home/<sysuser>/mail/<domain>/<user>/{cur,new,tmp}
# i.e. the user directory IS the Maildir — there is no extra "Maildir"
# subfolder. Earlier plan code assumed a "/Maildir" suffix; corrected here.

STATS_JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "mailbox_stats.json"
TRIGGER_FILE = Path(__file__).resolve().parent.parent / "data" / "mailbox_stats.trigger"

_refresh_lock = asyncio.Lock()


def _cache_clear_lock():
    """Test helper — release any held lock state. Reinitialize lock."""
    global _refresh_lock
    _refresh_lock = asyncio.Lock()


def _grep_dovecot_last_login(email: str) -> Optional[str]:
    """Find the newest 'Login:' or 'Logged in' line for `email` in DOVECOT_LOG_PATH.

    Reads the file once and scans backwards. No subprocess fork — replaces the
    earlier bash+tac+grep pipeline. Year-rollover guard: if parsed timestamp
    is in the future, subtract 1 year (handles Jan 1 cron tick processing
    Dec 31 lines).
    """
    try:
        with open(DOVECOT_LOG_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    now = datetime.now(UTC)
    for line in reversed(lines):
        if email not in line:
            continue
        if "Login:" not in line and "Logged in" not in line:
            continue
        m = re.match(r"^(\w+\s+\d+\s+\d+:\d+:\d+)", line)
        if not m:
            continue
        try:
            ts = datetime.strptime(f"{now.year} {m.group(1)}", "%Y %b %d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        if ts > now:  # Year-rollover: log line is from previous year
            ts = ts.replace(year=ts.year - 1)
        return ts.isoformat()
    return None


def _du_maildir(email: str, domain: str, user: str) -> Optional[int]:
    """`du -sk /home/<sysuser>/mail/<domain>/<user>` → MB.

    HestiaCP maildir is the user dir itself (cur/new/tmp inside). No
    "Maildir" subfolder.
    """
    sysuser = os.getenv("HESTIA_USER", "ekrem")
    path = f"{HESTIA_USER_HOME}/{sysuser}/mail/{domain}/{user}"
    if not Path(path).exists():
        return None
    try:
        r = subprocess.run(
            ["du", "-sk", path], capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return None
        kb = int(r.stdout.split()[0])
        return kb // 1024
    except (subprocess.TimeoutExpired, ValueError, IndexError, OSError):
        return None


def _atomic_write(path: Path, content: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


async def refresh() -> dict:
    """Collect 8 columns for all mailboxes across all domains. Atomic JSON write.

    Returns the dict written to disk, OR {"status": "in_progress"} if another
    refresh is currently running.
    """
    if _refresh_lock.locked():
        return {"status": "in_progress"}
    async with _refresh_lock:
        started = time.time()
        result: dict = {
            "refreshed_at": datetime.now(UTC).isoformat(),
            "duration_sec": 0.0,
            "domain_count": 0,
            "mailbox_count": 0,
            "errors": [],
            "domains": {},
        }
        try:
            domains = await list_mail_domains()
        except Exception as e:
            result["errors"].append({
                "domain": None, "mailbox": None,
                "step": "list_domains", "msg": str(e),
            })
            domains = []
        result["domain_count"] = len(domains)

        for domain in domains:
            domain_entry = {"mailboxes": []}
            try:
                boxes = await list_mailboxes(domain)
            except Exception as e:
                result["errors"].append({
                    "domain": domain, "mailbox": None,
                    "step": "list_mailboxes", "msg": str(e),
                })
                boxes = []

            for box in boxes:
                result["mailbox_count"] += 1
                # Aliases
                try:
                    aliases = await list_aliases(domain, box.user)
                    alias_count = len(aliases)
                except Exception as e:
                    result["errors"].append({
                        "domain": domain, "mailbox": box.email,
                        "step": "aliases", "msg": str(e),
                    })
                    alias_count = None
                # Last login (sync, off-thread)
                try:
                    last_login = await asyncio.to_thread(
                        _grep_dovecot_last_login, box.email,
                    )
                except Exception as e:
                    result["errors"].append({
                        "domain": domain, "mailbox": box.email,
                        "step": "last_login", "msg": str(e),
                    })
                    last_login = None
                # Disk size (sync, off-thread)
                try:
                    disk_mb = await asyncio.to_thread(
                        _du_maildir, box.email, domain, box.user,
                    )
                except Exception as e:
                    result["errors"].append({
                        "domain": domain, "mailbox": box.email,
                        "step": "du", "msg": str(e),
                    })
                    disk_mb = None

                # Forwarders (Faz Y.1)
                try:
                    fwd_data = await list_forwarders(domain, box.user)
                    forwarder_count = len(fwd_data.get("forwarders", []))
                    fwd_only = bool(fwd_data.get("fwd_only", False))
                except Exception as e:
                    result["errors"].append({
                        "domain": domain, "mailbox": box.email,
                        "step": "forwarders", "msg": str(e),
                    })
                    forwarder_count = None
                    fwd_only = None
                # Autoreply (Faz Y.1)
                try:
                    ar_data = await get_autoreply(domain, box.user)
                    autoreply_enabled = bool(ar_data.get("enabled", False))
                except Exception as e:
                    result["errors"].append({
                        "domain": domain, "mailbox": box.email,
                        "step": "autoreply", "msg": str(e),
                    })
                    autoreply_enabled = None
                domain_entry["mailboxes"].append({
                    "email": box.email,
                    "user": box.user,
                    "quota_mb": box.quota_mb,
                    "used_mb": box.used_mb,
                    "status": box.status,
                    "created_at": box.created_at,
                    "alias_count": alias_count,
                    "last_login": last_login,
                    "disk_size_mb": disk_mb,
                    "forwarder_count": forwarder_count,
                    "fwd_only": fwd_only,
                    "autoreply_enabled": autoreply_enabled,
                })
            result["domains"][domain] = domain_entry

        result["duration_sec"] = round(time.time() - started, 2)
        _atomic_write(
            STATS_JSON_PATH,
            json.dumps(result, ensure_ascii=False, indent=2),
        )
        # Consume trigger
        try:
            TRIGGER_FILE.unlink()
        except FileNotFoundError:
            pass
        return result
