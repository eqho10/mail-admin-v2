"""Quarantine view layer — merges 3 sources (frozen exim + reject log + Brevo blocked count)."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict

from services import exim as exim_svc
from services import brevo_suppression


# ============= Frozen messages =============

def get_frozen_messages() -> list[dict]:
    """Return messages flagged as frozen in exim queue."""
    return [m for m in exim_svc.exim_queue_list() if m.get("frozen")]


# ============= Reject log parser =============

# Example exim mainlog reject lines:
#   2026-04-29 14:00:00 H=evil.example [1.2.3.4] F=<spam@x.com> rejected SMTP from <user@us.com>: blocked by spamhaus
#   2026-04-29 14:00:00 H=mailer.bad [5.6.7.8] F=<x@y> rejected RCPT <victim@us.com>: relay not permitted
#   2026-04-29 14:00:00 H=src [9.9.9.9] F=<a@b> rejected after DATA: message rejected as spam
#
# Some exim builds also emit an internal short-id between the timestamp and the
# H= block (e.g. `1wEkRY-0008QF-7K`). The optional `[A-Za-z0-9-]+` handles it.
RE_REJECT_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"(?:[A-Za-z0-9-]+ )?"
    r"(?:H=(?P<host>\S+) +)?"
    r"(?:\[(?P<ip>[\d\.:a-fA-F]+)\] +)?"
    r"(?:F=<(?P<sender>[^>]*)> +)?"
    r"(?:rejected (?P<phase>SMTP|RCPT|MAIL|EHLO|connect|after DATA))[^:]*"
    r"(?:: (?P<reason>.+))?$"
)

# Numbers + bracketed IPs are stripped before grouping so that
# "rate limit 5 attempts" and "rate limit 12 attempts" land in the same bucket.
# `\d+` (not `\d{2,}`) is required so single-digit counters collapse with
# multi-digit ones — otherwise "5" and "12" would land in different groups.
RE_NORMALIZE_REASON = re.compile(r"(?:\[\d+\.\d+\.\d+\.\d+\]|\d+)")


def _normalize_reason(reason: str) -> str:
    return RE_NORMALIZE_REASON.sub("X", reason or "").strip().lower()


def _group_key(ip: str, sender: str, recipient: str, reason: str) -> str:
    payload = f"{ip or ''}|{sender or ''}|{recipient or ''}|{_normalize_reason(reason)}"
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def parse_rejected_lines(log_lines: list[str]) -> list[dict]:
    """Parse + group exim rejected lines.

    Returns list of group dicts:
      {key, ip, sender, recipient, reason, count, first_seen, last_seen, raw_lines}
    Sorted by last_seen desc. Lines without "rejected" are skipped silently;
    malformed lines (don't match RE_REJECT_LINE) are also skipped silently.
    """
    groups: dict[str, dict] = OrderedDict()
    for ln in log_lines:
        if "rejected" not in ln:
            continue
        m = RE_REJECT_LINE.match(ln.rstrip("\n"))
        if not m:
            continue
        ts = m.group("ts")
        ip = m.group("ip") or ""
        sender = m.group("sender") or ""
        reason = m.group("reason") or m.group("phase") or "rejected"
        recipient = ""
        # Recipient appears as a <addr@host> token in the line. The first such
        # token is usually the F=<sender>, so walk all matches and pick the
        # first one that isn't the sender. RCPT-phase rejects put the
        # recipient *inside* the regex match (consumed by `[^:]*`), so the
        # tail-only scan misses them — we always search the whole line.
        for cand_m in re.finditer(r"<([^>]+@[^>]+)>", ln):
            cand = cand_m.group(1)
            if cand and cand != sender:
                recipient = cand
                break
        key = _group_key(ip, sender, recipient, reason)
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "key": key, "ip": ip, "sender": sender, "recipient": recipient,
                "reason": reason, "count": 1,
                "first_seen": ts, "last_seen": ts, "raw_lines": [ln.rstrip("\n")],
            }
        else:
            g["count"] += 1
            g["last_seen"] = ts
            if len(g["raw_lines"]) < 50:
                g["raw_lines"].append(ln.rstrip("\n"))
    return sorted(groups.values(), key=lambda g: g["last_seen"], reverse=True)


def get_rejected_groups(n_lines: int = 2000) -> list[dict]:
    """Read last N lines of mainlog and parse rejected groups."""
    lines = exim_svc.read_tail(exim_svc.EXIM_MAINLOG, n_lines)
    return parse_rejected_lines(lines)


# ============= Brevo blocked count =============

async def get_brevo_blocked_count() -> int:
    """Fetch Brevo blocked count via Faz 4a `brevo_suppression.list_blocked`.

    Returns 0 on any error (network, auth, upstream) so the quarantine summary
    never crashes when Brevo is down or unconfigured.
    """
    try:
        items = await brevo_suppression.list_blocked()
        if isinstance(items, list):
            return len(items)
        if isinstance(items, dict):
            return int(items.get("count", 0))
        return 0
    except Exception:
        return 0


# ============= Combined summary =============

async def get_summary() -> dict:
    """Combine all 3 sources for the quarantine KPI strip."""
    frozen = get_frozen_messages()
    rejected = get_rejected_groups()
    brevo_n = await get_brevo_blocked_count()
    return {
        "frozen_count": len(frozen),
        "rejected_count": sum(g["count"] for g in rejected),
        "rejected_groups": len(rejected),
        "brevo_blocked_count": brevo_n,
    }
