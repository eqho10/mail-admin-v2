# services/sendas.py
"""Send-as test: dispatch + roundtrip polling.

Pipeline:
1. dispatch() → sendmail subprocess → write run state JSON → return {run_id, msgid, is_local_to}
2. poll_arrival() → 2s loop until find_by_msgid succeeds or timeout (Task 14)
3. parse_authentication_results() → extract DKIM/SPF/DMARC from arrived message (Task 14)

Run state files: data/sendas_runs/<run_id>.json
"""
import json
import re
import subprocess
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional, Dict, Any

import services.mailboxes
from services.audit import audit

_RUNS_DIR = Path(__file__).resolve().parent.parent / 'data' / 'sendas_runs'

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


class DispatchError(Exception):
    pass


def _extract_msgid(stdout: str) -> Optional[str]:
    """Parse Exim sendmail output for assigned msgid (format: 1xxxxx-yyyyy-zz)."""
    m = re.search(r'(\d[A-Za-z0-9]{5}-[A-Za-z0-9]{6}-[A-Za-z0-9]{2})', stdout or '')
    return m.group(1) if m else None


def _is_local_recipient(to_email: str) -> bool:
    """True if recipient's domain is in our local mailbox list."""
    if '@' not in to_email:
        return False
    domain = to_email.rsplit('@', 1)[1]
    locals_list = services.mailboxes.list_all()
    return any(addr.endswith(f'@{domain}') for addr in locals_list)


def _build_message(from_addr: str, to_addr: str, subject: str, body: str) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"To: {to_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {datetime.now(UTC).strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode('utf-8')


def dispatch(
    from_mailbox: str,
    to_email: str,
    subject: Optional[str] = None,
    body: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a test email via sendmail. Write run state. Return immediately."""
    if from_mailbox not in services.mailboxes.list_all():
        raise DispatchError(f"{from_mailbox} is not a local mailbox")
    if not EMAIL_RE.match(to_email):
        raise DispatchError(f"{to_email} is not a valid email")

    run_id = uuid.uuid4().hex
    sent_at = datetime.now(UTC).isoformat()
    subject = subject or f"[mail-admin-v2] send-as test {run_id[:8]}"
    body = body or f"Mail Admin v2 send-as test — {sent_at}"

    msg_bytes = _build_message(from_mailbox, to_email, subject, body)
    proc = subprocess.run(
        ['sendmail', '-f', from_mailbox, to_email],
        input=msg_bytes, capture_output=True, timeout=30,
    )
    stdout_str = proc.stdout.decode('utf-8', errors='replace') if isinstance(proc.stdout, bytes) else (proc.stdout or '')
    msgid = _extract_msgid(stdout_str)
    if proc.returncode != 0:
        raise DispatchError(f"sendmail returned {proc.returncode}: {proc.stderr}")

    is_local_to = _is_local_recipient(to_email)
    state = {
        'run_id': run_id,
        'from': from_mailbox,
        'to': to_email,
        'subject': subject,
        'sent_at': sent_at,
        'msgid': msgid,
        'status': 'sent',
        'is_local_to': is_local_to,
    }
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (_RUNS_DIR / f"{run_id}.json").write_text(json.dumps(state, indent=2))

    # Exclude run_id and subject from the spread to avoid duplicate kwargs
    audit_extra = {k: v for k, v in state.items() if k not in ('subject', 'run_id')}
    audit("sendas_dispatch", run_id=run_id, **audit_extra)
    return state


def get_run_state(run_id: str) -> Optional[Dict[str, Any]]:
    f = _RUNS_DIR / f"{run_id}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


def update_run_state(run_id: str, **updates) -> None:
    state = get_run_state(run_id) or {}
    state.update(updates)
    (_RUNS_DIR / f"{run_id}.json").write_text(json.dumps(state, indent=2))
