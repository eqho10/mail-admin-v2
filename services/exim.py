# /root/mail-admin-v2/services/exim.py
"""Exim mainlog parser + queue ops. Activity sayfasının veri kaynağı."""
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

__all__ = [
    "EXIM_MAINLOG",
    "parse_line",
    "read_tail",
    "aggregate_messages",
    "count_by_day",
    "exim_queue_count",
    "exim_queue_list",
    "exim_retry_all",
    "exim_delete_msg",
]

EXIM_MAINLOG = "/var/log/exim4/mainlog"
_DOMAINS_DIR = "/etc/exim4/domains"

# ======================= PRIVATE SHELL HELPERS =======================
# Copies of app.py sh/sh_code — private here; Faz 3'te services/shell.py olur.

def _sh(cmd: List[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def _sh_code(cmd: List[str], timeout: int = 15) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


# ======================= LOG LINE REGEX =======================

# Note: "Frozen" lines (Exim's mark-message-as-stuck event) are intentionally
# excluded from LOG_LINE â they're not delivery events. For bounced messages
# the preceding "**" line already sets status=bounced in aggregate_messages,
# so dropping the Frozen line preserves status correctly.
LOG_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"(?P<msgid>[\w\-]+) "
    r"(?P<sym><=|=>|->|==|\*\*|Completed)"
    r"(?: (?P<rest>.*))?$"
)


# ======================= PARSER =======================

def parse_line(line: str) -> Optional[dict]:
    m = LOG_LINE.match(line.rstrip("\n"))
    if not m:
        return None
    d = m.groupdict()
    rest = d.get("rest") or ""
    sym = d["sym"]
    # Address = first token, but prefer <email@domain> form if present (e.g. "info <info@mdsgida.com>")
    addr = ""
    if sym != "Completed" and rest:
        ma = re.match(r"^(\S+)(?:\s+<([^>]+@[^>]+)>)?", rest)
        if ma:
            addr = ma.group(2) or ma.group(1)
        else:
            addr = rest.split(" ", 1)[0]
    # extract key=value tokens
    kvs = dict(re.findall(r"(\w+)=(\S+)", rest))
    size = kvs.get("S")
    host = kvs.get("H", "").strip("[]")
    # completion status between quotes
    cstatus = ""
    cm = re.search(r'C="([^"]+)"', rest)
    if cm:
        cstatus = cm.group(1)[:200]
    return {
        "ts": d["ts"],
        "msgid": d["msgid"],
        "sym": sym,
        "addr": addr,
        "size": size,
        "host": host,
        "cstatus": cstatus,
        "raw": line.rstrip("\n"),
    }


def read_tail(path: str, n_lines: int = 2000) -> List[str]:
    """Read last n_lines from a log file efficiently."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n_lines:
                read_size = min(block, size)
                size -= read_size
                f.seek(size)
                data = f.read(read_size) + data
            lines = data.decode("utf-8", errors="replace").splitlines()
            return lines[-n_lines:]
    except Exception:
        return []


def aggregate_messages(
    lines: List[str],
    extra_local_domains: Optional[List[str]] = None,
) -> List[dict]:
    """Merge multiple log lines per msgid into one event.

    extra_local_domains: additional domain names to treat as local/outgoing
    (e.g. from hestia_list_mail_domains()). Avoids circular import — callers
    fetch and pass the list themselves.
    """
    msgs: Dict[str, dict] = {}
    for ln in lines:
        e = parse_line(ln)
        if not e:
            continue
        mid = e["msgid"]
        if mid not in msgs:
            msgs[mid] = {
                "msgid": mid,
                "ts": e["ts"],
                "from": "",
                "to": [],
                "size": "",
                "status": "pending",
                "cstatus": "",
                "host": "",
                "direction": "out",
                "raw": [],
                "deferred": 0,
                "bounced": 0,
            }
        m = msgs[mid]
        m["raw"].append(e["raw"])
        if e["sym"] == "<=":
            m["from"] = e["addr"]
            m["size"] = e["size"] or ""
            m["ts"] = e["ts"]
        elif e["sym"] == "=>":
            m["to"].append(e["addr"])
            m["host"] = e["host"] or m["host"]
            m["cstatus"] = e["cstatus"] or m["cstatus"]
            m["status"] = "delivered" if m["status"] != "bounced" else m["status"]
        elif e["sym"] == "->":
            m["to"].append(e["addr"])
        elif e["sym"] == "==":
            m["deferred"] += 1
            if m["status"] == "pending":
                m["status"] = "deferred"
        elif e["sym"] == "**":
            m["bounced"] += 1
            m["status"] = "bounced"
        elif e["sym"] == "Completed":
            if m["status"] == "pending":
                m["status"] = "delivered"
    # direction heuristic: from domain in local_domains → outgoing, otherwise → incoming
    local_domains: set = set()
    try:
        if os.path.isdir(_DOMAINS_DIR):
            local_domains = set(os.listdir(_DOMAINS_DIR))
    except Exception:
        pass
    if extra_local_domains:
        local_domains.update(extra_local_domains)
    out = []
    for m in msgs.values():
        frm = (m["from"] or "").lower()
        frm_domain = frm.split("@")[-1] if "@" in frm else ""
        if frm_domain and frm_domain in local_domains:
            m["direction"] = "out"
        else:
            m["direction"] = "in"
        out.append(m)
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def count_by_day(msgs: List[dict], days: int = 7) -> List[dict]:
    """Return per-day counts for last N days."""
    now = datetime.now()
    buckets = {}
    for i in range(days):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        buckets[d] = {"date": d, "sent": 0, "delivered": 0, "deferred": 0, "bounced": 0, "in": 0}
    for m in msgs:
        d = m["ts"][:10]
        if d not in buckets:
            continue
        b = buckets[d]
        if m["direction"] == "out":
            b["sent"] += 1
            if m["status"] == "delivered":
                b["delivered"] += 1
            elif m["status"] == "deferred":
                b["deferred"] += 1
            elif m["status"] == "bounced":
                b["bounced"] += 1
        else:
            b["in"] += 1
    return sorted(buckets.values(), key=lambda x: x["date"])


# ======================= EXIM QUEUE OPS =======================

def exim_queue_count() -> int:
    try:
        r = _sh(["exim", "-bpc"], timeout=5).strip()
        return int(r) if r.isdigit() else 0
    except Exception:
        return 0


def exim_queue_list() -> List[dict]:
    """Parse `exim -bp` output. Sets `frozen: bool` per item by detecting
    the `*** frozen ***` marker that exim emits beneath frozen messages.
    """
    raw = _sh(["exim", "-bp"], timeout=10)
    items = []
    cur = None
    for ln in raw.splitlines():
        if re.match(r"^\s*\d+[hdm]?\s+", ln):
            # header line: "27h  2.3K  1wEk... <from@x>"
            parts = ln.strip().split(None, 3)
            if len(parts) >= 3:
                cur = {
                    "age": parts[0],
                    "size": parts[1],
                    "msgid": parts[2],
                    "from": parts[3].strip("<>") if len(parts) > 3 else "",
                    "to": [],
                    "frozen": False,
                }
                items.append(cur)
        elif cur and ln.strip():
            stripped = ln.strip()
            if stripped == "*** frozen ***":
                cur["frozen"] = True
            else:
                cur["to"].append(stripped)
    return items


def exim_retry_all() -> str:
    return _sh(["exim", "-qff"], timeout=30)


def exim_delete_msg(msgid: str) -> tuple:
    # sanitize
    if not re.match(r"^[A-Za-z0-9\-]+$", msgid):
        return (1, "", "bad msgid")
    return _sh_code(["exim", "-Mrm", msgid], timeout=10)


# ======================= QUARANTINE EXTENSIONS (Faz 4b Task 7) =======================

MSG_BODY_MAX_BYTES = 1024 * 1024  # 1 MB cutoff for view_msg

__all__ = __all__ + ["exim_release_msg", "exim_view_msg", "MSG_BODY_MAX_BYTES"]


def exim_release_msg(msgid: str) -> tuple:
    """Release a frozen message — `exim -Mt msgid` (try delivery now)."""
    if not re.match(r"^[A-Za-z0-9\-]+$", msgid):
        return (1, "", "bad msgid")
    return _sh_code(["exim", "-Mt", msgid], timeout=15)


def exim_view_msg(msgid: str) -> dict:
    """Return {headers, body, truncated, not_found?} for a frozen msg.

    Body capped at MSG_BODY_MAX_BYTES (1 MB). Truncated body still returned with
    truncated=True so admin can see a slice. Returns {'not_found': True} if exim
    cannot locate the spool file.
    """
    if not re.match(r"^[A-Za-z0-9\-]+$", msgid):
        return {"invalid": True, "error": "bad msgid"}
    h_rc, h_out, h_err = _sh_code(["exim", "-Mvh", msgid], timeout=10)
    if h_rc != 0 and ("not found" in (h_err or "").lower() or "no such" in (h_err or "").lower()):
        return {"not_found": True, "error": h_err.strip()}
    if h_rc != 0:
        return {"error": h_err or "view headers failed"}
    b_rc, b_out, b_err = _sh_code(["exim", "-Mvb", msgid], timeout=10)
    truncated = False
    body = b_out or ""
    body_bytes = body.encode("utf-8", errors="replace")
    if len(body_bytes) > MSG_BODY_MAX_BYTES:
        body = body_bytes[:MSG_BODY_MAX_BYTES].decode("utf-8", errors="replace")
        truncated = True
    return {"headers": h_out or "", "body": body, "truncated": truncated}
