"""
Mail Admin Panel v2 — Professional Dashboard
FastAPI + Tailwind + Alpine.js (single-file, no build step)
"""
import os, json, re, time, subprocess, secrets, asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Response, HTTPException, Form, Cookie, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
import httpx
from services.error_translator import translate
from services.audit import audit, AUDIT_LOG
from services.csrf import issue_token, verify_token
from services.templates import _ctx
from services.exim import (
    parse_line, read_tail, aggregate_messages, count_by_day,
    exim_queue_count, exim_queue_list, exim_retry_all, exim_delete_msg,
    EXIM_MAINLOG,
)

# ======================= CONFIG =======================
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "ekrem.mutlu@hotmail.com.tr")
ADMIN_PASS = os.getenv("ADMIN_PASS", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")
SESSION_TTL = 12 * 3600
OTP_TTL = 300
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
HESTIA_USER = "ekrem"
HESTIA_BIN = "/usr/local/hestia/bin"
DOMAINS_DIR = "/etc/exim4/domains"
_DATA_DIR = Path(__file__).resolve().parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
OTP_STORE = _DATA_DIR / "otp_store.json"
RATE_STORE = _DATA_DIR / "rate_limit.json"
VPS_IP = "153.92.1.179"

FROM_SENDER = os.getenv("FROM_SENDER", "noreply@bilgestore.com")
REPUTATION_CRON_TOKEN = os.getenv("REPUTATION_CRON_TOKEN", "")
FROM_NAME = "Mail Admin"

signer = TimestampSigner(SESSION_SECRET)
app = FastAPI(title="Mail Admin v2")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ======================= CSRF MIDDLEWARE =======================
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {
    "/login",        # bootstraps session — chicken/egg
    "/verify",       # bootstraps session
    "/healthz",
    "/api/reputation/snapshot",  # legacy: HMAC token
}
CSRF_EXEMPT_PREFIXES = (
    "/cron/",        # cron endpoints use HMAC token instead
)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    if request.method in SAFE_METHODS:
        return await call_next(request)
    path = request.url.path
    if path in CSRF_EXEMPT_PATHS or any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES):
        return await call_next(request)
    sess_cookie = request.cookies.get("ma_sess", "")
    if not sess_cookie:
        # No session = anonymous; let downstream auth dep return 401.
        return await call_next(request)
    submitted = request.headers.get("X-CSRF-Token", "")
    if not submitted:
        # Fallback: form field
        try:
            form = await request.form()
            submitted = str(form.get("csrf_token", ""))
            request._form = form  # type: ignore[attr-defined]
        except Exception:
            submitted = ""
    if not verify_token(sess_cookie, submitted):
        return JSONResponse(status_code=403, content={"error": "csrf token missing or invalid"})
    return await call_next(request)


from routers.activity import router as activity_router
app.include_router(activity_router)
from routers.reputation import router as reputation_router
app.include_router(reputation_router)
from routers.sendas import router as sendas_router
app.include_router(sendas_router)
from routers.cron import router as cron_router
app.include_router(cron_router)
from routers.mailboxes import router as mailboxes_router
app.include_router(mailboxes_router)
from routers.suppression import router as suppression_router
app.include_router(suppression_router)


# DB singleton init at startup, close at shutdown
@app.on_event("startup")
async def _db_startup():
    from services.db import get_conn
    get_conn()  # trigger schema migrate

@app.on_event("shutdown")
async def _db_shutdown():
    from services.db import close
    close()




# ======================= GLOBAL EXCEPTION HANDLER =======================
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # str(exc) bazi custom exception'larda bos/whitespace olabilir;
    # strip + repr fallback ile guvene al.
    raw = (str(exc) or "").strip() or repr(exc)
    translated = translate(raw)
    return JSONResponse(
        status_code=500,
        content={"error": translated},
    )

if os.getenv("DEBUG_TEST_ENDPOINTS") == "1":
    @app.get("/api/_test/raise", include_in_schema=False)
    async def _test_raise(raw: str = "test error"):
        raise RuntimeError(raw)

    @app.post("/api/_test/csrf-protected", include_in_schema=False)
    async def _test_csrf_protected(request: Request):
        require_auth(request)
        return {"ok": True}


# ======================= HELPERS =======================
def sh(cmd: List[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception as e:
        return ""

def sh_code(cmd: List[str], timeout: int = 15) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)

def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default

def save_json(p: Path, data):
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(data))

def security_headers(resp: Response):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

# ======================= AUTH =======================
def rate_check(ip: str, limit: int = 5, window: int = 600):
    data = load_json(RATE_STORE, {})
    now = time.time()
    entries = [t for t in data.get(ip, []) if now - t < window]
    if len(entries) >= limit:
        return False
    entries.append(now)
    data[ip] = entries
    save_json(RATE_STORE, data)
    return True

def gen_otp():
    return str(secrets.randbelow(900000) + 100000)

async def send_mail(to: str, subject: str, body: str):
    """Send via local exim (routes through Brevo relay)."""
    hdr = f"From: {FROM_NAME} <{FROM_SENDER}>\r\nTo: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
    full = hdr + body
    try:
        subprocess.run(["exim", "-f", FROM_SENDER, to], input=full, text=True, timeout=15, check=True)
        return True
    except Exception as e:
        audit("mail_send_fail", to=to, err=str(e))
        return False

def get_session(request: Request) -> Optional[str]:
    tok = request.cookies.get("ma_sess")
    if not tok:
        return None
    try:
        email = signer.unsign(tok, max_age=SESSION_TTL).decode()
        return email
    except (BadSignature, SignatureExpired):
        return None

def require_auth(request: Request):
    if not get_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

# ======================= HESTIA CLI =======================
def hestia_list_mail_domains() -> List[str]:
    out = sh([f"{HESTIA_BIN}/v-list-mail-domains", HESTIA_USER, "json"])
    try:
        data = json.loads(out)
        return list(data.keys())
    except Exception:
        return []

def hestia_domain_info() -> Dict[str, dict]:
    out = sh([f"{HESTIA_BIN}/v-list-mail-domains", HESTIA_USER, "json"])
    try:
        return json.loads(out)
    except Exception:
        return {}

def hestia_list_mail_accounts(domain: str) -> Dict[str, dict]:
    out = sh([f"{HESTIA_BIN}/v-list-mail-accounts", HESTIA_USER, domain, "json"])
    try:
        return json.loads(out)
    except Exception:
        return {}

def hestia_add_mail_account(domain: str, account: str, password: str, quota: str = "0") -> tuple:
    return sh_code([f"{HESTIA_BIN}/v-add-mail-account", HESTIA_USER, domain, account, password, quota], timeout=20)

def hestia_change_password(domain: str, account: str, new_password: str) -> tuple:
    return sh_code([f"{HESTIA_BIN}/v-change-mail-account-password", HESTIA_USER, domain, account, new_password], timeout=20)

def hestia_delete_mail_account(domain: str, account: str) -> tuple:
    return sh_code([f"{HESTIA_BIN}/v-delete-mail-account", HESTIA_USER, domain, account], timeout=20)

# ======================= RELAY STATUS =======================
def relay_status() -> List[dict]:
    """Per-domain relay config presence."""
    out = []
    try:
        for d in sorted(os.listdir(DOMAINS_DIR)):
            conf = os.path.join(DOMAINS_DIR, d, "smtp_relay.conf")
            has = os.path.isfile(conf)
            user = host = port = ""
            if has:
                try:
                    for ln in open(conf):
                        if ln.startswith("host:"): host = ln.split(":", 1)[1].strip()
                        elif ln.startswith("port:"): port = ln.split(":", 1)[1].strip()
                        elif ln.startswith("user:"): user = ln.split(":", 1)[1].strip()
                except Exception:
                    pass
            out.append({"domain": d, "relay": has, "host": host, "port": port, "user": user})
    except FileNotFoundError:
        pass
    return out

# ======================= DNS CHECK =======================
def dig_txt(name: str) -> List[str]:
    out = sh(["dig", "TXT", name, "+short", "@1.1.1.1"], timeout=6)
    return [l.strip('"') for l in out.strip().split("\n") if l.strip()]

def check_spf(domain: str) -> dict:
    recs = dig_txt(domain)
    spfs = [r for r in recs if r.startswith("v=spf1")]
    if not spfs:
        return {"status": "missing", "value": None}
    if len(spfs) > 1:
        return {"status": "multiple", "value": spfs}
    val = spfs[0]
    # Real check: since mail relays via Brevo, SPF MUST include spf.brevo.com (or sendinblue.com legacy)
    has_brevo = "brevo" in val.lower() or "sendinblue" in val.lower()
    return {"status": "ok" if has_brevo else "weak", "value": val}

def check_dmarc(domain: str) -> dict:
    recs = dig_txt(f"_dmarc.{domain}")
    dmarcs = [r for r in recs if r.startswith("v=DMARC1")]
    if not dmarcs:
        return {"status": "missing", "value": None}
    return {"status": "ok" if len(dmarcs) == 1 else "multiple", "value": dmarcs[0] if dmarcs else None}

def check_dkim_brevo(domain: str) -> dict:
    """Brevo domain authentication CNAMEs."""
    out = sh(["dig", "CNAME", f"brevo1._domainkey.{domain}", "+short", "@1.1.1.1"], timeout=6).strip()
    return {"status": "ok" if out else "missing", "value": out or None}

def check_ptr() -> dict:
    out = sh(["dig", "-x", VPS_IP, "+short", "@8.8.8.8"], timeout=6).strip().rstrip(".")
    expected = "atlas.bilgeworld.com"
    return {"status": "ok" if out == expected else "mismatch", "value": out, "expected": expected}

# ======================= BREVO API =======================
async def brevo_account():
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get("https://api.brevo.com/v3/account", headers={"api-key": BREVO_API_KEY})
        if r.status_code == 200: return r.json()
        return {"error": r.text[:200]}

async def brevo_events(limit: int = 25, email: str = None):
    params = {"limit": limit, "sort": "desc"}
    if email: params["email"] = email
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get("https://api.brevo.com/v3/smtp/statistics/events", headers={"api-key": BREVO_API_KEY}, params=params)
        if r.status_code == 200: return r.json().get("events", [])
        return []

# ======================= ENDPOINTS: AUTH =======================
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    resp = templates.TemplateResponse(request, "login.html", _ctx(request, error=error))
    return security_headers(resp)

@app.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    if not rate_check(ip):
        return templates.TemplateResponse(request, "login.html", _ctx(request, error="Çok fazla deneme. 10 dk sonra tekrar dene."), status_code=429)
    if email != ADMIN_EMAIL or password != ADMIN_PASS:
        audit("login_fail", ip=ip, email=email)
        return templates.TemplateResponse(request, "login.html", _ctx(request, error="E-posta veya şifre hatalı."), status_code=401)
    # send OTP
    code = gen_otp()
    save_json(OTP_STORE, {"email": email, "code": code, "exp": time.time() + OTP_TTL})
    await send_mail(email, f"Mail Admin giriş kodu: {code}", f"Mail Admin paneli için 2FA kodunuz: {code}\n\nBu kod 5 dakika geçerlidir. Talep etmediyseniz görmezden gelin.\n\nIP: {ip}\nZaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    audit("login_otp_sent", ip=ip, email=email)
    return RedirectResponse("/verify", status_code=303)

@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "verify.html", _ctx(request, error=error))

@app.post("/verify")
async def verify_submit(request: Request, code: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    data = load_json(OTP_STORE, {})
    if not data or time.time() > data.get("exp", 0):
        return templates.TemplateResponse(request, "verify.html", _ctx(request, error="Kod süresi doldu. Tekrar giriş yap."), status_code=401)
    if code.strip() != data.get("code"):
        audit("verify_fail", ip=ip)
        return templates.TemplateResponse(request, "verify.html", _ctx(request, error="Kod yanlış."), status_code=401)
    OTP_STORE.unlink(missing_ok=True)
    token = signer.sign(data["email"].encode()).decode()
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("ma_sess", token, max_age=SESSION_TTL, httponly=True, secure=True, samesite="strict")
    audit("login_ok", ip=ip, email=data["email"])
    return resp

@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("ma_sess")
    return resp

# ======================= ENDPOINTS: API =======================
@app.get("/api/whoami")
async def whoami(request: Request):
    email = get_session(request)
    if not email: raise HTTPException(401)
    return {"email": email}

@app.get("/api/cmdk/actions")
async def api_cmdk_actions(request: Request):
    """Cmd+K palette server-side aksiyon listesi — registry test'i için.
    Faz 2'de sadece sabit aksiyonlar; dynamic gruplar JS tarafında /api/domains
    ve /api/mailboxes'tan fetch ediliyor."""
    require_auth(request)
    return {
        "actions": [
            {"id":"theme.toggle","label":"Tema değiştir","group":"Aksiyon"},
            {"id":"logout","label":"Çıkış yap","group":"Aksiyon"},
            {"id":"test-mail","label":"Test mail at","group":"Aksiyon"},
            {"id":"sse.toggle","label":"Real-time tail aç/kapa","group":"Aksiyon","when_page":"activity"},
            {"id":"nav.overview","label":"Genel Bakış'a git","group":"Gezinme"},
            {"id":"nav.activity","label":"Aktivite'ye git","group":"Gezinme"},
            {"id":"nav.queue","label":"Kuyruk'a git","group":"Gezinme"},
            {"id":"nav.domains","label":"Domain'lere git","group":"Gezinme"},
            {"id":"nav.mailboxes","label":"Mailbox'lara git","group":"Gezinme"},
            {"id":"nav.deliverability","label":"Deliverability'e git","group":"Gezinme"},
            {"id":"nav.quarantine","label":"Quarantine'e git","group":"Gezinme"},
            {"id":"nav.settings","label":"Ayarlar'a git","group":"Gezinme"},
            {"id":"dict.add","label":"Sözlüğe çeviri ekle","group":"Aksiyon","disabled":True},
        ]
    }

@app.get("/api/overview")
async def api_overview(request: Request):
    require_auth(request)
    lines = read_tail(EXIM_MAINLOG, 5000)
    msgs = aggregate_messages(lines, extra_local_domains=hestia_list_mail_domains())
    today = datetime.now().strftime("%Y-%m-%d")
    today_msgs = [m for m in msgs if m["ts"].startswith(today)]
    today_sent = sum(1 for m in today_msgs if m["direction"] == "out")
    today_in = sum(1 for m in today_msgs if m["direction"] == "in")
    week = count_by_day(msgs, 7)
    total_week = sum(b["sent"] for b in week)
    bounced_week = sum(b["bounced"] for b in week)
    deferred_week = sum(b["deferred"] for b in week)
    delivered_week = sum(b["delivered"] for b in week)
    bounce_rate = round(100 * bounced_week / total_week, 2) if total_week else 0.0

    # per-domain today breakdown
    by_dom: Dict[str, dict] = {}
    for m in today_msgs:
        if m["direction"] != "out":
            continue
        dom = (m["from"] or "").split("@")[-1].lower() or "?"
        if dom not in by_dom:
            by_dom[dom] = {"domain": dom, "sent": 0, "delivered": 0, "deferred": 0, "bounced": 0, "last": ""}
        b = by_dom[dom]
        b["sent"] += 1
        if m["status"] == "delivered": b["delivered"] += 1
        elif m["status"] == "bounced": b["bounced"] += 1
        elif m["status"] == "deferred": b["deferred"] += 1
        if m["ts"] > b["last"]: b["last"] = m["ts"]
    by_domain = sorted(by_dom.values(), key=lambda x: x["sent"], reverse=True)

    # per-domain incoming today
    in_dom: Dict[str, int] = {}
    for m in today_msgs:
        if m["direction"] != "in":
            continue
        tos = m.get("to", [])
        for t in tos:
            dom = t.split("@")[-1].lower()
            in_dom[dom] = in_dom.get(dom, 0) + 1
    by_domain_in = sorted([{"domain": d, "in": c} for d, c in in_dom.items()], key=lambda x: x["in"], reverse=True)

    queue = exim_queue_count()
    brevo = await brevo_account()
    brevo_credits = 0
    if isinstance(brevo, dict) and "plan" in brevo:
        for p in brevo.get("plan", []):
            if p.get("type") == "free":
                brevo_credits = p.get("credits", 0) or 0
    ptr = check_ptr()
    recent = msgs[:30]
    return {
        "today_sent": today_sent,
        "today_in": today_in,
        "week_sent": total_week,
        "week_delivered": delivered_week,
        "week_deferred": deferred_week,
        "week_bounced": bounced_week,
        "bounce_rate": bounce_rate,
        "queue": queue,
        "brevo_credits": brevo_credits,
        "week": week,
        "recent": recent,
        "by_domain": by_domain,
        "by_domain_in": by_domain_in,
        "ptr": ptr,
    }

@app.get("/api/domains")
async def api_domains(request: Request):
    require_auth(request)
    relay = {r["domain"]: r for r in relay_status()}
    hestia = hestia_domain_info()
    all_domains = sorted(set(relay.keys()) | set(hestia.keys()))
    out = []
    for d in all_domains:
        spf = check_spf(d)
        dmarc = check_dmarc(d)
        dkim = check_dkim_brevo(d)
        r = relay.get(d, {})
        h = hestia.get(d, {})
        out.append({
            "domain": d,
            "relay": r.get("relay", False),
            "relay_host": r.get("host", ""),
            "accounts": int(h.get("ACCOUNTS", 0)) if h else 0,
            "hestia_dkim": h.get("DKIM", "no"),
            "ssl": h.get("SSL", "no"),
            "spf": spf,
            "dmarc": dmarc,
            "dkim": dkim,
            "in_hestia": bool(h),
        })
    return {"domains": out, "ptr": check_ptr()}

@app.get("/api/domain/{domain}")
async def api_domain_detail(request: Request, domain: str):
    require_auth(request)
    if not re.match(r"^[a-z0-9.\-]+$", domain): raise HTTPException(400)
    accounts = hestia_list_mail_accounts(domain)
    spf = check_spf(domain)
    dmarc = check_dmarc(domain)
    dkim = check_dkim_brevo(domain)
    mx = sh(["dig", "MX", domain, "+short", "@1.1.1.1"], timeout=6).strip()
    return {
        "domain": domain,
        "accounts": [{"user": u, **v} for u, v in accounts.items()],
        "spf": spf,
        "dmarc": dmarc,
        "dkim": dkim,
        "mx": mx,
    }

@app.post("/api/mailbox/reset")
async def api_mailbox_reset(request: Request, domain: str = Form(...), account: str = Form(...), new_password: str = Form(...)):
    require_auth(request)
    if not re.match(r"^[a-z0-9.\-]+$", domain): raise HTTPException(400)
    if not re.match(r"^[a-z0-9._\-]+$", account): raise HTTPException(400)
    if len(new_password) < 10: raise HTTPException(400, "Şifre en az 10 karakter olmalı")
    rc, out, err = hestia_change_password(domain, account, new_password)
    audit("mailbox_reset", domain=domain, account=account, rc=rc, by=get_session(request))
    if rc != 0: raise HTTPException(500, err or "reset failed")
    return {"ok": True}

@app.post("/api/mailbox/add")
async def api_mailbox_add(request: Request, domain: str = Form(...), account: str = Form(...), password: str = Form(...)):
    require_auth(request)
    if not re.match(r"^[a-z0-9.\-]+$", domain): raise HTTPException(400)
    if not re.match(r"^[a-z0-9._\-]+$", account): raise HTTPException(400)
    # legacy v1 mailbox-add path; uses 10-char gate. v2 path (services/hestia.add_mailbox) enforces 12+digit+symbol.
    if len(password) < 10: raise HTTPException(400, "Şifre en az 10 karakter olmalı")
    rc, out, err = hestia_add_mail_account(domain, account, password)
    audit("mailbox_add", domain=domain, account=account, rc=rc, by=get_session(request))
    if rc != 0: raise HTTPException(500, err or "add failed")
    return {"ok": True}

@app.post("/api/mailbox/delete")
async def api_mailbox_delete(request: Request, domain: str = Form(...), account: str = Form(...)):
    require_auth(request)
    if not re.match(r"^[a-z0-9.\-]+$", domain): raise HTTPException(400)
    if not re.match(r"^[a-z0-9._\-]+$", account): raise HTTPException(400)
    rc, out, err = hestia_delete_mail_account(domain, account)
    audit("mailbox_delete", domain=domain, account=account, rc=rc, by=get_session(request))
    if rc != 0: raise HTTPException(500, err or "delete failed")
    return {"ok": True}

@app.get("/api/mailboxes")
async def api_mailboxes(request: Request, domain: str = Query(...)):
    require_auth(request)
    if not re.match(r"^[a-z0-9.\-]+$", domain): raise HTTPException(400)
    accounts = hestia_list_mail_accounts(domain)
    lines = read_tail(EXIM_MAINLOG, 20000)
    msgs = aggregate_messages(lines, extra_local_domains=hestia_list_mail_domains())
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    out = []
    for user, v in accounts.items():
        email = f"{user}@{domain}".lower()
        sent = received = 0
        last_sent = last_recv = ""
        for m in msgs:
            if m["ts"][:10] < cutoff: continue
            frm = (m["from"] or "").lower()
            to_list = [(t or "").lower() for t in m["to"]]
            if frm == email:
                sent += 1
                if m["ts"] > last_sent: last_sent = m["ts"]
            if email in to_list:
                received += 1
                if m["ts"] > last_recv: last_recv = m["ts"]
        out.append({
            "email": email,
            "user": user,
            "quota": v.get("QUOTA", "unlimited"),
            "used_mb": int(v.get("U_DISK", "0") or 0),
            "suspended": v.get("SUSPENDED", "no") == "yes",
            "alias": v.get("ALIAS", ""),
            "fwd": v.get("FWD", ""),
            "fwd_only": v.get("FWD_ONLY", "no") == "yes",
            "autoreply": v.get("AUTOREPLY", "no") == "yes",
            "date": v.get("DATE", ""),
            "time": v.get("TIME", ""),
            "sent_7d": sent,
            "received_7d": received,
            "last_sent": last_sent,
            "last_received": last_recv,
        })
    out.sort(key=lambda x: x["user"])
    return out

@app.get("/api/mailboxes/all")
async def api_mailboxes_all(request: Request):
    require_auth(request)
    from services.mailboxes import list_all
    return {"mailboxes": list_all()}

@app.get("/api/mailbox/detail")
async def api_mailbox_detail(request: Request, domain: str = Query(...), account: str = Query(...)):
    require_auth(request)
    if not re.match(r"^[a-z0-9.\-]+$", domain): raise HTTPException(400)
    if not re.match(r"^[a-z0-9._\-]+$", account): raise HTTPException(400)
    accounts = hestia_list_mail_accounts(domain)
    info = accounts.get(account)
    if not info:
        raise HTTPException(404, "Mailbox bulunamadı")
    email = f"{account}@{domain}".lower()
    lines = read_tail(EXIM_MAINLOG, 20000)
    msgs = aggregate_messages(lines, extra_local_domains=hestia_list_mail_domains())
    rel = []
    for m in msgs:
        frm = (m["from"] or "").lower()
        to_list = [(t or "").lower() for t in m["to"]]
        if frm == email:
            direction = "out"
        elif email in to_list:
            direction = "in"
        else:
            continue
        rel.append({
            "ts": m["ts"],
            "direction": direction,
            "from": m["from"],
            "to": m["to"],
            "status": m["status"],
            "size": m["size"],
            "host": m["host"],
            "msgid": m["msgid"],
        })
    rel.sort(key=lambda x: x["ts"], reverse=True)
    return {
        "email": email,
        "info": {
            "quota": info.get("QUOTA", "unlimited"),
            "used_mb": int(info.get("U_DISK", "0") or 0),
            "suspended": info.get("SUSPENDED", "no") == "yes",
            "alias": info.get("ALIAS", ""),
            "fwd": info.get("FWD", ""),
            "fwd_only": info.get("FWD_ONLY", "no") == "yes",
            "autoreply": info.get("AUTOREPLY", "no") == "yes",
            "date": info.get("DATE", ""),
            "time": info.get("TIME", ""),
        },
        "webmail_url": f"https://snappymail.{domain}",
        "messages": rel[:100],
    }



@app.get("/api/queue")
async def api_queue(request: Request):
    require_auth(request)
    return {"count": exim_queue_count(), "items": exim_queue_list()[:100]}

@app.post("/api/queue/retry")
async def api_queue_retry(request: Request):
    require_auth(request)
    out = exim_retry_all()
    audit("queue_retry_all", by=get_session(request))
    return {"ok": True, "output": out[:500]}

@app.post("/api/queue/delete")
async def api_queue_delete(request: Request, msgid: str = Form(...)):
    require_auth(request)
    rc, out, err = exim_delete_msg(msgid)
    audit("queue_delete", msgid=msgid, rc=rc, by=get_session(request))
    if rc != 0: raise HTTPException(500, err or "delete failed")
    return {"ok": True}

@app.get("/api/brevo/events")
async def api_brevo_events(request: Request, limit: int = 25, email: str = None):
    require_auth(request)
    return {"events": await brevo_events(limit, email)}

@app.get("/api/brevo/account")
async def api_brevo_account(request: Request):
    require_auth(request)
    return await brevo_account()

RELAY_SCRIPT = "/root/switch-smtp-relay.sh"

def _classify_host(h):
    h = (h or "").lower()
    if "mailjet" in h: return "mailjet"
    if "brevo" in h or "sendinblue" in h: return "brevo"
    if "amazonaws" in h: return "ses"
    return "unknown"

@app.get("/api/relay/status")
async def api_relay_status(request: Request):
    require_auth(request)
    per_domain = []
    provs = set()
    try:
        for d in sorted(os.listdir(DOMAINS_DIR)):
            conf = os.path.join(DOMAINS_DIR, d, "smtp_relay.conf")
            if not os.path.isfile(conf): continue
            host = ""
            for ln in open(conf):
                if ln.startswith("host:"): host = ln.split(":", 1)[1].strip()
            prov = _classify_host(host)
            per_domain.append({"domain": d, "host": host, "provider": prov})
            provs.add(prov)
    except Exception:
        pass
    if len(provs) == 1:
        current = next(iter(provs))
    elif provs <= {"brevo","mailjet"}:
        current = "hybrid"
    else:
        current = "mixed"
    state = ""
    try: state = open("/etc/exim4/current-smtp-relay.txt").read().strip()
    except Exception: pass
    providers = [
        {"id": "brevo", "label": "Brevo — tümü", "host": "smtp-relay.brevo.com", "configured": True, "desc": "8 domain tamamen Brevo"},
        {"id": "mailjet", "label": "Mailjet — tümü", "host": "in-v3.mailjet.com", "configured": True, "desc": "Acil fallback (Brevo çöktüğünde)"},
        {"id": "hybrid", "label": "Hybrid (önerilen)", "host": "mixed", "configured": True, "desc": "bilgeworld+eqhoids → Mailjet (warming), kalan 6 → Brevo"},
    ]
    host_summary = per_domain[0]["host"] if per_domain else ""
    user_summary = "mixed" if current == "hybrid" else ("a33283001@smtp-brevo.com" if current == "brevo" else "mailjet-api")
    return {"current": current, "state": state, "per_domain": per_domain, "providers": providers, "current_host": host_summary, "current_user": user_summary}

@app.post("/api/relay/switch")
async def api_relay_switch(request: Request, provider: str = Form(...)):
    require_auth(request)
    if provider not in ("brevo", "mailjet", "hybrid"):
        raise HTTPException(400, "unknown provider")
    if not os.path.isfile(RELAY_SCRIPT):
        raise HTTPException(500, f"{RELAY_SCRIPT} yok — önce oluştur")
    rc, out, err = sh_code(["/bin/bash", RELAY_SCRIPT, provider], timeout=60)
    audit("relay_switch", provider=provider, rc=rc, by=get_session(request))
    if rc != 0:
        raise HTTPException(500, (err or out)[:500] or "switch failed")
    return {"ok": True, "provider": provider, "output": out[:800]}

@app.post("/api/test-mail")
async def api_test_mail(request: Request, to: str = Form(...), domain: str = Form("bilgestore.com")):
    require_auth(request)
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+$", to): raise HTTPException(400)
    frm = f"noreply@{domain}"
    body = f"Subject: Mail Admin test\r\nFrom: {frm}\r\nTo: {to}\r\n\r\nTest mail - {datetime.now()}"
    try:
        subprocess.run(["exim", "-f", frm, to], input=body, text=True, timeout=10, check=True)
        audit("test_mail", to=to, from_=frm, by=get_session(request))
        return {"ok": True, "sent_from": frm, "to": to}
    except Exception as e:
        raise HTTPException(500, str(e))


# ======================= HEALTH =======================
@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


# ======================= PAGE ROUTES =======================

def _render_page(request: Request, template: str, current_page: str, page_title: str, breadcrumb: list):
    require_auth(request)
    return templates.TemplateResponse(request, template, _ctx(
        request,
        current_page=current_page,
        page_title=page_title,
        breadcrumb=breadcrumb,
        user_email=get_session(request),
    ))


@app.get("/", response_class=HTMLResponse)
async def page_overview(request: Request):
    return _render_page(request, "pages/overview.html", "overview", "Genel Bakış",
                        [{"label": "Genel Bakış", "href": None}])


@app.get("/kuyruk", response_class=HTMLResponse)
async def page_queue(request: Request):
    return _render_page(request, "pages/queue.html", "queue", "Kuyruk",
                        [{"label": "Kuyruk", "href": None}])

@app.get("/domain", response_class=HTMLResponse)
async def page_domains(request: Request):
    return _render_page(request, "pages/domains.html", "domains", "Domain'ler",
                        [{"label": "Domain'ler", "href": None}])

@app.get("/mailbox", response_class=HTMLResponse)
async def page_mailboxes(request: Request):
    return _render_page(request, "pages/mailboxes.html", "mailboxes", "Mailbox'lar",
                        [{"label": "Mailbox'lar", "href": None}])

@app.get("/deliverability", response_class=HTMLResponse)
async def page_deliverability(request: Request):
    return _render_page(request, "pages/deliverability.html", "deliverability", "Deliverability",
                        [{"label": "Deliverability", "href": None}])

@app.get("/quarantine", response_class=HTMLResponse)
async def page_quarantine(request: Request):
    return _render_page(request, "pages/quarantine.html", "quarantine", "Quarantine",
                        [{"label": "Quarantine", "href": None}])

@app.get("/ayarlar", response_class=HTMLResponse)
async def page_settings(request: Request):
    return _render_page(request, "pages/settings.html", "settings", "Ayarlar",
                        [{"label": "Ayarlar", "href": None}])


if os.getenv("DEBUG_TEST_ENDPOINTS") == "1":
    @app.get("/dev/components", response_class=HTMLResponse, include_in_schema=False)
    async def dev_components(request: Request):
        return _render_page(request, "pages/_components.html", "_components",
                            "Components Showcase",
                            [{"label": "Dev", "href": None}, {"label": "Components", "href": None}])
