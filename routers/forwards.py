"""Cross-domain mail forward & alias özet sayfası.

Tüm Hestia mail-domain'lerini gez, her hesabın ALIAS/FWD/FWD_ONLY alanlarını
topla, tek listede göster (örn. info@bilgestore.com → ekrem.mutlu@hotmail.com.tr).

Authoritative source: Hestia `v-list-mail-accounts USER DOMAIN json`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from services.templates import _ctx

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/yonlendirmeler", response_class=HTMLResponse)
async def page_forwards(request: Request):
    from app import require_auth
    require_auth(request)
    return templates.TemplateResponse(request, "pages/forwards.html", _ctx(request))


@router.get("/api/forwards/all")
async def api_forwards_all(request: Request) -> dict[str, Any]:
    """Tüm domain'lerdeki forward + alias kayıtlarını tek listede dön."""
    from app import require_auth, hestia_list_mail_domains, hestia_list_mail_accounts
    require_auth(request)

    domains = await asyncio.to_thread(hestia_list_mail_domains)
    rows: list[dict] = []

    async def _collect(domain: str) -> list[dict]:
        accounts = await asyncio.to_thread(hestia_list_mail_accounts, domain)
        out = []
        for user, rec in accounts.items():
            fwd_raw = (rec.get("FWD") or "").strip()
            alias_raw = (rec.get("ALIAS") or "").strip()
            if not fwd_raw and not alias_raw:
                continue
            fwd_list = [x.strip() for x in fwd_raw.split(",") if x.strip()] if fwd_raw else []
            alias_list = [x.strip() for x in alias_raw.split(",") if x.strip()] if alias_raw else []
            out.append({
                "domain": domain,
                "user": user,
                "email": f"{user}@{domain}",
                "forward_to": fwd_list,
                "fwd_only": (rec.get("FWD_ONLY") or "no").lower() == "yes",
                "aliases": alias_list,
            })
        return out

    coros = [_collect(d) for d in domains]
    per_domain_results = await asyncio.gather(*coros, return_exceptions=True)
    for res in per_domain_results:
        if isinstance(res, Exception):
            logger.warning("forwards.all collect error: %s", res)
            continue
        rows.extend(res)

    rows.sort(key=lambda r: (r["domain"], r["user"]))

    accs_with_fwd = sum(1 for r in rows if r["forward_to"])
    accs_with_alias = sum(1 for r in rows if r["aliases"])

    return {
        "forwards": rows,
        "summary": {
            "domains": len(domains),
            "accounts_with_forward": accs_with_fwd,
            "accounts_with_alias": accs_with_alias,
            "total_records": len(rows),
        },
    }
