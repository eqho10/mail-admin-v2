"""Mail provider (SMTP relay) status — Faz 6.U (2026-05-01).

Reads /etc/exim4/domains/<X>/smtp_relay.conf for each domain to surface the
configured primary + fallback hosts. If MAILJET_API_KEY + MAILJET_API_SECRET
env are set, fetches Mailjet account status. Brevo account info is reused
from the existing reputation pipeline.

Pure helper — no audit/CSRF here. Router layer does that (K5)."""
from __future__ import annotations

import asyncio
import logging
import os
from base64 import b64encode
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EXIM_DOMAINS_DIR = Path("/etc/exim4/domains")
RELAY_CONF_NAME = "smtp_relay.conf"

MAILJET_API_KEY = os.getenv("MAILJET_API_KEY", "")
MAILJET_API_SECRET = os.getenv("MAILJET_API_SECRET", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")

PROVIDER_LABELS = {
    "smtp-relay.brevo.com": "Brevo",
    "in-v3.mailjet.com": "Mailjet",
}


def _label_for(host: str) -> str:
    return PROVIDER_LABELS.get((host or "").strip(), host or "—")


def _parse_relay_conf(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip()
    except Exception as e:
        logger.warning("relay conf parse %s: %s", path, e)
    return out


def list_relay_configs() -> list[dict[str, Any]]:
    """Per-domain primary + fallback configuration."""
    rows: list[dict[str, Any]] = []
    if not EXIM_DOMAINS_DIR.exists():
        return rows
    for entry in sorted(EXIM_DOMAINS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        conf = entry / RELAY_CONF_NAME
        if not conf.exists():
            continue
        cfg = _parse_relay_conf(conf)
        primary_host = cfg.get("host", "")
        fallback_host = cfg.get("fallback_host", "")
        rows.append({
            "domain": entry.name,
            "primary": {
                "label": _label_for(primary_host),
                "host": primary_host,
                "port": cfg.get("port", ""),
                "user": cfg.get("user", ""),
            },
            "fallback": {
                "label": _label_for(fallback_host),
                "host": fallback_host,
                "port": cfg.get("fallback_port", ""),
                "user": cfg.get("fallback_user", ""),
            } if fallback_host else None,
        })
    return rows


# ============================================================
# Live API status — degrades gracefully when keys absent
# ============================================================
async def get_mailjet_status(timeout: float = 6.0) -> dict[str, Any]:
    """Fetch Mailjet account /v3/REST/myprofile + balance."""
    if not (MAILJET_API_KEY and MAILJET_API_SECRET):
        return {
            "configured": False, "reachable": False,
            "message": "MAILJET_API_KEY + MAILJET_API_SECRET env değişkenleri tanımlı değil.",
        }
    auth = b64encode(f"{MAILJET_API_KEY}:{MAILJET_API_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get("https://api.mailjet.com/v3/REST/myprofile", headers=headers)
        if r.status_code == 401:
            return {"configured": True, "reachable": False, "message": "Mailjet 401 — API key/secret yanlış."}
        if r.status_code != 200:
            return {"configured": True, "reachable": False, "message": f"Mailjet HTTP {r.status_code}"}
        data = r.json().get("Data", [])
        prof = data[0] if data else {}
        return {
            "configured": True, "reachable": True,
            "account_email": prof.get("Email") or prof.get("ContactDetails", {}).get("Email", ""),
            "company": prof.get("CompanyName", ""),
            "country": prof.get("AddressCountry", ""),
            "fetched_at": r.headers.get("Date", ""),
        }
    except Exception as e:
        logger.warning("Mailjet API err: %s", e)
        return {"configured": True, "reachable": False, "message": f"Mailjet hata: {e!s}"}


async def get_brevo_status(timeout: float = 6.0) -> dict[str, Any]:
    """Fetch Brevo /v3/account for plan + sender info."""
    if not BREVO_API_KEY:
        return {
            "configured": False, "reachable": False,
            "message": "BREVO_API_KEY env değişkeni tanımlı değil.",
        }
    headers = {"api-key": BREVO_API_KEY, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get("https://api.brevo.com/v3/account", headers=headers)
        if r.status_code == 401:
            return {"configured": True, "reachable": False, "message": "Brevo 401 — API key yanlış."}
        if r.status_code != 200:
            return {"configured": True, "reachable": False, "message": f"Brevo HTTP {r.status_code}"}
        data = r.json()
        plan = (data.get("plan") or [{}])[0]
        return {
            "configured": True, "reachable": True,
            "account_email": data.get("email", ""),
            "company": data.get("companyName", ""),
            "plan_type": plan.get("type", ""),
            "credits_left": plan.get("credits", None),
        }
    except Exception as e:
        logger.warning("Brevo API err: %s", e)
        return {"configured": True, "reachable": False, "message": f"Brevo hata: {e!s}"}


def summarize(rows: list[dict]) -> dict[str, Any]:
    """Aggregate domain counts per primary/fallback role."""
    brevo_primary = sum(1 for r in rows if r["primary"]["label"] == "Brevo")
    mailjet_primary = sum(1 for r in rows if r["primary"]["label"] == "Mailjet")
    with_fallback = sum(1 for r in rows if r.get("fallback"))
    return {
        "total": len(rows),
        "brevo_primary": brevo_primary,
        "mailjet_primary": mailjet_primary,
        "with_fallback": with_fallback,
    }
