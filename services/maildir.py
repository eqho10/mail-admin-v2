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
