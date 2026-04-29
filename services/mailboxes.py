"""Hestia mailbox enumeration. Wraps v-list-mail-domains + v-list-mail-accounts.

Used by:
- Faz 3 send-as test (whitelist + dropdown source)
- Faz 4 mailbox CRUD (planned)

Pattern follows services/exim.py — small, testable, subprocess wrapped.
"""
import json
import subprocess
from typing import List

HESTIA_BIN = "/usr/local/hestia/bin"
HESTIA_USER = "ekrem"


def _sh(cmd: List[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def list_domains() -> List[str]:
    out = _sh([f"{HESTIA_BIN}/v-list-mail-domains", HESTIA_USER, "json"])
    if not out.strip():
        return []
    try:
        return list(json.loads(out).keys())
    except json.JSONDecodeError:
        return []


def list_accounts(domain: str) -> List[str]:
    out = _sh([f"{HESTIA_BIN}/v-list-mail-accounts", HESTIA_USER, domain, "json"])
    if not out.strip():
        return []
    try:
        return list(json.loads(out).keys())
    except json.JSONDecodeError:
        return []


def list_all() -> List[str]:
    """Return all `account@domain` addresses across all Hestia mail domains."""
    result = []
    for domain in list_domains():
        for account in list_accounts(domain):
            result.append(f"{account}@{domain}")
    return result
