"""Mailbox stats refresher — collects 8 columns per mailbox into JSON file.

Run by systemd timer every 5 min. Write ops touch trigger file to wake
cron. Atomic write (tmp + rename). Per-mailbox errors collected, don't
fail entire refresh.
"""
import asyncio
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

from services.hestia import (
    Mailbox, list_mail_domains, list_mailboxes, list_aliases,
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
    """grep '<email>.*Login:' DOVECOT_LOG_PATH (newest first) → ISO8601 ts or None.

    Real dovecot log format (verified 2026-04-29):
        "Apr 26 00:05:29 imap-login: Info: Login: user=<ekrem.mutlu@raporoku.com>, ..."
    Also matches "Logged in" wording for robustness across dovecot versions.
    """
    try:
        if not Path(DOVECOT_LOG_PATH).exists():
            return None
        # Defense-in-depth: shell-quote the email even though Mailbox.email
        # comes from validated user@domain. A future caller might pass
        # user-input here, and a single-quote would break out of the bash
        # f-string interpolation.
        safe_email = shlex.quote(email)
        # tac + grep -m 1 = stop at newest hit. Match 'Login:' or 'Logged in'
        # so we cover both Hestia/dovecot syslog phrasings.
        r = subprocess.run(
            ["bash", "-c",
             f"tac {DOVECOT_LOG_PATH} 2>/dev/null | "
             f"grep -m 1 -F {safe_email} | "
             f"grep -m 1 -E 'Login:|Logged in' || true"],
            capture_output=True, text=True, timeout=5,
        )
        line = r.stdout.strip()
        if not line:
            return None
        # syslog format: "Apr 29 12:34:56 ..." (3-letter month, day, HH:MM:SS)
        m = re.match(r"^(\w+\s+\d+\s+\d+:\d+:\d+)", line)
        if m:
            try:
                # Assume current year (good enough; on Jan 1 a Dec line could
                # be misdated by 1 year but that's an acceptable approximation
                # — see Task 6 concern #5 in the plan).
                ts = datetime.strptime(
                    f"{datetime.now(UTC).year} {m.group(1)}",
                    "%Y %b %d %H:%M:%S",
                )
                return ts.replace(tzinfo=UTC).isoformat()
            except ValueError:
                return None
        return None
    except (subprocess.TimeoutExpired, OSError):
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
