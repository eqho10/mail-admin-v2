# services/maildir.py
"""Maildir message parser + finder.

parse_message: extract HTML/plain bodies, attachments, Authentication-Results
find_by_msgid: locate file via recipient → Hestia user → Maildir scan
get_attachment: retrieve raw payload for the Nth attachment by index
"""
import email
import re
from email import policy
from typing import Optional, List, Dict, Any


def parse_message(file_bytes: bytes) -> Dict[str, Any]:
    """Return dict with html, plain, raw, auth_results, attachments."""
    msg = email.message_from_bytes(file_bytes, policy=policy.default)
    html_part: Optional[str] = None
    plain_part: Optional[str] = None
    attachments: List[Dict[str, Any]] = []
    att_idx = 0

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            disposition = (part.get('Content-Disposition') or '').lower()
            filename = part.get_filename()

            if 'attachment' in disposition or (filename and ctype.startswith('application/')):
                payload = part.get_payload(decode=True) or b''
                attachments.append({
                    'idx': att_idx,
                    'filename': filename or f'attachment-{att_idx}',
                    'content_type': ctype,
                    'size': len(payload),
                })
                att_idx += 1
                continue

            if ctype == 'text/html' and html_part is None:
                try:
                    html_part = part.get_content()
                except (LookupError, UnicodeDecodeError):
                    html_part = (part.get_payload(decode=True) or b'').decode('utf-8', errors='replace')
            elif ctype == 'text/plain' and plain_part is None:
                try:
                    plain_part = part.get_content()
                except (LookupError, UnicodeDecodeError):
                    plain_part = (part.get_payload(decode=True) or b'').decode('utf-8', errors='replace')
    else:
        ctype = msg.get_content_type()
        try:
            content = msg.get_content()
        except (LookupError, UnicodeDecodeError):
            content = (msg.get_payload(decode=True) or b'').decode('utf-8', errors='replace')
        if ctype == 'text/html':
            html_part = content
        else:
            plain_part = content

    return {
        'html': html_part,
        'plain': plain_part,
        'raw': file_bytes.decode('utf-8', errors='replace'),
        'auth_results': _parse_auth_header(msg),
        'attachments': attachments,
    }


def _parse_auth_header(msg) -> Dict[str, Optional[str]]:
    ar = msg.get('Authentication-Results', '') or ''
    return {
        'dkim':  _extract_result(ar, 'dkim'),
        'spf':   _extract_result(ar, 'spf'),
        'dmarc': _extract_result(ar, 'dmarc'),
    }


def _extract_result(header: str, key: str) -> Optional[str]:
    m = re.search(rf'\b{key}=(\w+)', header, re.IGNORECASE)
    return m.group(1).lower() if m else None


def get_attachment(file_bytes: bytes, idx: int) -> Optional[Dict[str, Any]]:
    """Return {filename, content_type, payload} for the Nth attachment, or None."""
    msg = email.message_from_bytes(file_bytes, policy=policy.default)
    att_idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = (part.get('Content-Disposition') or '').lower()
        filename = part.get_filename()
        ctype = part.get_content_type()
        if 'attachment' in disposition or (filename and ctype.startswith('application/')):
            if att_idx == idx:
                return {
                    'filename': filename or f'attachment-{att_idx}',
                    'content_type': ctype,
                    'payload': part.get_payload(decode=True) or b'',
                }
            att_idx += 1
    return None


# ---------------------------------------------------------------------------
# Finder — appended in Task 10
# ---------------------------------------------------------------------------
import json
import pwd
from pathlib import Path

_MAIL_ROOT = '/home'
_USER_CACHE_PATH = Path(__file__).resolve().parent.parent / 'data' / 'maildir_user_cache.json'
_user_cache: Optional[Dict[str, str]] = None


def _load_user_cache() -> Dict[str, str]:
    global _user_cache
    if _user_cache is not None:
        return _user_cache
    if _USER_CACHE_PATH.exists():
        try:
            _user_cache = json.loads(_USER_CACHE_PATH.read_text())
            return _user_cache
        except (json.JSONDecodeError, OSError):
            pass
    _user_cache = {}
    return _user_cache


def _save_user_cache():
    if _user_cache is not None:
        try:
            _USER_CACHE_PATH.parent.mkdir(exist_ok=True)
            _USER_CACHE_PATH.write_text(json.dumps(_user_cache))
        except OSError:
            pass


def _resolve_hestia_user(domain: str) -> Optional[str]:
    """Find Hestia user owning a given mail domain via /home/*/mail/<domain>/ probe.

    Cached in data/maildir_user_cache.json. Cache invalidated on process restart.
    """
    cache = _load_user_cache()
    if domain in cache:
        return cache[domain]
    # Probe filesystem
    for entry in pwd.getpwall():
        if entry.pw_uid < 1000:
            continue
        candidate = Path(_MAIL_ROOT) / entry.pw_name / 'mail' / domain
        if candidate.is_dir():
            cache[domain] = entry.pw_name
            _save_user_cache()
            return entry.pw_name
    return None


def find_by_msgid(msgid: str, recipient: str) -> Optional[str]:
    """Locate Maildir file containing given Exim msgid, scoped to recipient's mailbox.

    Strategy:
    1. Parse recipient → local@domain
    2. Resolve hestia user for domain (cached)
    3. Build path /home/<user>/mail/<domain>/<local>/Maildir/{cur,new}/
    4. Scan files in those dirs for 'id <msgid>'

    Returns absolute file path or None.
    """
    if '@' not in recipient:
        return None
    local, domain = recipient.rsplit('@', 1)
    user = _resolve_hestia_user(domain)
    if not user:
        return None
    base = Path(_MAIL_ROOT) / user / 'mail' / domain / local / 'Maildir'
    if not base.is_dir():
        return None
    for sub in ('cur', 'new'):
        d = base / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            try:
                content = f.read_bytes()
                if f"id {msgid}".encode() in content:
                    return str(f)
            except (OSError, PermissionError):
                continue
    return None
