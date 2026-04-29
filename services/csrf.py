"""CSRF token helpers — double-submit cookie pattern.

Token = HMAC-SHA256(SESSION_SECRET:csrf, session_cookie_value).
Issued per-session, deterministic, bound to session — leaking token alone
without the session cookie does not enable CSRF (attacker would need both).
"""
import hmac
import hashlib
import os


class CSRFError(Exception):
    """Raised when CSRF check fails (used by app.py middleware)."""


def _key() -> bytes:
    """Use a CSRF-specific suffix on SESSION_SECRET so token domain is
    separate from session signing."""
    secret = os.getenv("SESSION_SECRET", "dev-only-change-me")
    return (secret + ":csrf").encode()


def issue_token(session_value: str) -> str:
    """Compute deterministic CSRF token for a given session cookie value.
    Returns hex digest (64 chars)."""
    return hmac.new(_key(), session_value.encode(), hashlib.sha256).hexdigest()


def verify_token(session_value: str, submitted_token: str) -> bool:
    """Constant-time comparison of expected token vs submitted token."""
    if not session_value or not submitted_token:
        return False
    expected = issue_token(session_value)
    try:
        return hmac.compare_digest(expected, submitted_token)
    except Exception:
        return False
