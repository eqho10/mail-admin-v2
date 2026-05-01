"""Cross-domain mail forward & alias yönetim sayfası — Faz 6.P (2026-05-01).

Sayfa: `/yonlendirmeler` (Office 365 cmdbar pattern, K19).
- Üstte domain pivot (tek seçim, varsayılan boş = "domain seç")
- Per-domain forward & alias listesi
- CRUD: forward ekle/sil, fwd-only toggle, alias ekle/sil
- Tüm mutation endpoint'leri JSON + X-CSRF-Token header korumalı (K3)

Authoritative source: Hestia `v-list-mail-accounts USER DOMAIN json`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from services import hestia
from services.audit import audit
from services.templates import _ctx

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ============================================================
# PAGE
# ============================================================
@router.get("/yonlendirmeler", response_class=HTMLResponse)
async def page_forwards(request: Request):
    from app import require_auth
    require_auth(request)
    return templates.TemplateResponse(request, "pages/forwards.html", _ctx(request))


# ============================================================
# READ
# ============================================================
async def _collect_domain(domain: str) -> list[dict]:
    """Bir domain'in tüm hesaplarındaki forward + alias kayıtlarını dön."""
    from app import hestia_list_mail_accounts
    accounts = await asyncio.to_thread(hestia_list_mail_accounts, domain)
    out: list[dict] = []
    for user, rec in accounts.items():
        fwd_raw = (rec.get("FWD") or "").strip()
        alias_raw = (rec.get("ALIAS") or "").strip()
        fwd_list = [x.strip() for x in fwd_raw.split(",") if x.strip()] if fwd_raw else []
        alias_list = [x.strip() for x in alias_raw.split(",") if x.strip()] if alias_raw else []
        if not fwd_list and not alias_list:
            continue
        out.append({
            "domain": domain,
            "user": user,
            "email": f"{user}@{domain}",
            "forward_to": fwd_list,
            "fwd_only": (rec.get("FWD_ONLY") or "no").lower() == "yes",
            "aliases": alias_list,
        })
    return out


@router.get("/api/forwards/domains")
async def api_forwards_domains(request: Request) -> dict[str, Any]:
    """Pivot için domain listesi + her domain'in özet sayıları."""
    from app import require_auth, hestia_list_mail_domains
    require_auth(request)
    domains = await asyncio.to_thread(hestia_list_mail_domains)

    coros = [_collect_domain(d) for d in domains]
    results = await asyncio.gather(*coros, return_exceptions=True)

    items: list[dict] = []
    for d, res in zip(domains, results):
        if isinstance(res, Exception):
            logger.warning("forwards.domains collect %s error: %s", d, res)
            items.append({"name": d, "total": 0, "forwards": 0, "aliases": 0, "fwd_only": 0, "error": True})
            continue
        fwd_count = sum(1 for r in res if r["forward_to"])
        alias_count = sum(1 for r in res if r["aliases"])
        only_count = sum(1 for r in res if r["fwd_only"])
        items.append({
            "name": d,
            "total": len(res),
            "forwards": fwd_count,
            "aliases": alias_count,
            "fwd_only": only_count,
        })

    items.sort(key=lambda x: (-x["total"], x["name"]))
    return {"domains": items}


@router.get("/api/forwards/by-domain/{domain}")
async def api_forwards_by_domain(domain: str, request: Request) -> dict[str, Any]:
    """Tek bir domain'in tüm forward + alias kayıtları + boş hesap listesi."""
    from app import require_auth, hestia_list_mail_domains, hestia_list_mail_accounts
    require_auth(request)

    domains = await asyncio.to_thread(hestia_list_mail_domains)
    if domain not in domains:
        raise HTTPException(status_code=404, detail="domain not found")

    rows = await _collect_domain(domain)

    # "Yeni yönlendirme" modal'ı için: o domain'in TÜM hesapları (boş olanlar dahil)
    accounts = await asyncio.to_thread(hestia_list_mail_accounts, domain)
    all_users = sorted(accounts.keys())

    return {
        "domain": domain,
        "rows": rows,
        "users": all_users,
        "summary": {
            "accounts": len(accounts),
            "with_forward": sum(1 for r in rows if r["forward_to"]),
            "with_alias": sum(1 for r in rows if r["aliases"]),
            "fwd_only": sum(1 for r in rows if r["fwd_only"]),
        },
    }


# Backward-compat: tüm domainleri tek listede dön (önceki forwards.html bunu çağırıyordu)
@router.get("/api/forwards/all")
async def api_forwards_all(request: Request) -> dict[str, Any]:
    from app import require_auth, hestia_list_mail_domains
    require_auth(request)

    domains = await asyncio.to_thread(hestia_list_mail_domains)
    rows: list[dict] = []
    coros = [_collect_domain(d) for d in domains]
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


# ============================================================
# WRITE — JSON + X-CSRF-Token (K3)
# ============================================================
_EMAIL_RX = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"


class ForwardMutate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=253)
    user: str = Field(..., min_length=1, max_length=64)
    forward_to: str = Field(..., min_length=3, max_length=320, pattern=_EMAIL_RX)


class FwdOnlyMutate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=253)
    user: str = Field(..., min_length=1, max_length=64)
    on: bool


class AliasMutate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=253)
    user: str = Field(..., min_length=1, max_length=64)
    alias: str = Field(..., min_length=1, max_length=64)


def _err(e: Exception) -> HTTPException:
    """HestiaCLIError veya generic Exception → HTTPException 400 + translated body."""
    translated = getattr(e, "translated", None)
    if translated:
        payload = translated
    else:
        payload = {
            "id": "hestia_unknown", "title": "İşlem başarısız",
            "body": str(e) or "Bilinmeyen hata.", "severity": "error",
        }
    return HTTPException(status_code=400, detail=payload)


@router.post("/api/forwards/forward/add")
async def api_forward_add(payload: ForwardMutate, request: Request) -> dict[str, Any]:
    from app import require_auth
    require_auth(request)
    try:
        await asyncio.to_thread(hestia.set_forward, payload.domain, payload.user, payload.forward_to)
    except Exception as e:
        logger.warning("forward.add failed: %s", e)
        raise _err(e)
    audit("forwards.forward_add", domain=payload.domain, user=payload.user, forward=payload.forward_to)
    return {"ok": True, "action": "forward_add", "domain": payload.domain, "user": payload.user, "forward_to": payload.forward_to}


@router.post("/api/forwards/forward/delete")
async def api_forward_delete(payload: ForwardMutate, request: Request) -> dict[str, Any]:
    from app import require_auth
    require_auth(request)
    try:
        await asyncio.to_thread(hestia.delete_forward, payload.domain, payload.user, payload.forward_to)
    except Exception as e:
        logger.warning("forward.delete failed: %s", e)
        raise _err(e)
    audit("forwards.forward_delete", domain=payload.domain, user=payload.user, forward=payload.forward_to)
    return {"ok": True, "action": "forward_delete", "domain": payload.domain, "user": payload.user, "forward_to": payload.forward_to}


@router.post("/api/forwards/fwd-only")
async def api_forward_fwd_only(payload: FwdOnlyMutate, request: Request) -> dict[str, Any]:
    from app import require_auth
    require_auth(request)
    try:
        await asyncio.to_thread(hestia.set_fwd_only, payload.domain, payload.user, payload.on)
    except Exception as e:
        logger.warning("fwd_only set failed: %s", e)
        raise _err(e)
    audit("forwards.fwd_only_set", domain=payload.domain, user=payload.user, on=payload.on)
    return {"ok": True, "action": "fwd_only_set", "domain": payload.domain, "user": payload.user, "on": payload.on}


@router.post("/api/forwards/alias/add")
async def api_alias_add(payload: AliasMutate, request: Request) -> dict[str, Any]:
    from app import require_auth
    require_auth(request)
    try:
        await asyncio.to_thread(hestia.add_alias, payload.domain, payload.user, payload.alias)
    except Exception as e:
        logger.warning("alias.add failed: %s", e)
        raise _err(e)
    audit("forwards.alias_add", domain=payload.domain, user=payload.user, alias=payload.alias)
    return {"ok": True, "action": "alias_add", "domain": payload.domain, "user": payload.user, "alias": payload.alias}


@router.post("/api/forwards/alias/delete")
async def api_alias_delete(payload: AliasMutate, request: Request) -> dict[str, Any]:
    from app import require_auth
    require_auth(request)
    try:
        await asyncio.to_thread(hestia.delete_alias, payload.domain, payload.user, payload.alias)
    except Exception as e:
        logger.warning("alias.delete failed: %s", e)
        raise _err(e)
    audit("forwards.alias_delete", domain=payload.domain, user=payload.user, alias=payload.alias)
    return {"ok": True, "action": "alias_delete", "domain": payload.domain, "user": payload.user, "alias": payload.alias}
