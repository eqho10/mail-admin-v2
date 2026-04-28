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

# ======================= CONFIG =======================
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "ekrem.mutlu@hotmail.com.tr")
ADMIN_PASS = os.getenv("ADMIN_PASS", "VkCngJrPL9Bspcmdg5rBIfRS")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")
SESSION_TTL = 12 * 3600
OTP_TTL = 300
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "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")
HESTIA_USER = "ekrem"
HESTIA_BIN = "/usr/local/hestia/bin"
EXIM_LOG = "/var/log/exim4/mainlog"
DOMAINS_DIR = "/etc/exim4/domains"
OTP_STORE = Path("/root/mail-admin/otp_store.json")
RATE_STORE = Path("/root/mail-admin/rate_limit.json")
AUDIT_LOG = Path("/root/mail-admin/audit.log")
VPS_IP = "153.92.1.179"

FROM_SENDER = os.getenv("FROM_SENDER", "noreply@bilgestore.com")
FROM_NAME = "Mail Admin"

signer = TimestampSigner(SESSION_SECRET)
app = FastAPI(title="Mail Admin v2")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


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


# ======================= HELPERS =======================
def audit(event: str, **kwargs):
    line = json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "event": event, **kwargs})
    try:
        AUDIT_LOG.parent.mkdir(exist_ok=True)
        with AUDIT_LOG.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass

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

# ======================= EXIM LOG PARSER =======================
# 2026-04-20 11:32:37 1wEk3Z-00000001imn-1WqL <= noreply@bilgestore.com U=root P=local S=731
# 2026-04-20 11:32:37 1wEk3Z-00000001imn-1WqL => ekrem.mutlu@hotmail.com.tr R=send_via_smtp_relay T=smtp_relay_smtp H=smtp-relay.brevo.com ... C="250 OK..."
# 2026-04-20 11:32:37 1wEk3Z-00000001imn-1WqL Completed
# 2026-04-20 11:15:22 1xxx == user@example.com ... (deferred)
# 2026-04-20 11:15:22 1xxx ** user@example.com ... (bounced)

LOG_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"(?P<msgid>[\w\-]+) "
    r"(?P<sym><=|=>|->|==|\*\*|Completed)"
    r"(?: (?P<rest>.*))?$"
)

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

def aggregate_messages(lines: List[str]) -> List[dict]:
    """Merge multiple log lines per msgid into one event."""
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
    # direction heuristic: from ends with any of our domains? → outgoing. otherwise → incoming
    local_domains = set()
    try:
        if os.path.isdir(DOMAINS_DIR):
            local_domains = set(os.listdir(DOMAINS_DIR))
    except Exception:
        pass
    # Also include HestiaCP mail domains
    hestia_domains = hestia_list_mail_domains()
    local_domains.update(hestia_domains)
    out = []
    for m in msgs.values():
        frm = (m["from"] or "").lower()
        frm_domain = frm.split("@")[-1] if "@" in frm else ""
        # outgoing if from a local domain AND at least one external recipient? we'll just mark by from-domain
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

# ======================= EXIM QUEUE =======================
def exim_queue_count() -> int:
    try:
        r = sh(["exim", "-bpc"], timeout=5).strip()
        return int(r) if r.isdigit() else 0
    except Exception:
        return 0

def exim_queue_list() -> List[dict]:
    """Parse `exim -bp` output."""
    raw = sh(["exim", "-bp"], timeout=10)
    items = []
    cur = None
    for ln in raw.splitlines():
        if re.match(r"^\s*\d+[hdm]?\s+", ln):
            # header line: "27h  2.3K  1wEk... <from@x>"
            parts = ln.strip().split(None, 3)
            if len(parts) >= 3:
                cur = {"age": parts[0], "size": parts[1], "msgid": parts[2], "from": parts[3].strip("<>") if len(parts) > 3 else "", "to": []}
                items.append(cur)
        elif cur and ln.strip():
            cur["to"].append(ln.strip())
    return items

def exim_retry_all() -> str:
    return sh(["exim", "-qff"], timeout=30)

def exim_delete_msg(msgid: str) -> tuple:
    # sanitize
    if not re.match(r"^[A-Za-z0-9\-]+$", msgid):
        return (1, "", "bad msgid")
    return sh_code(["exim", "-Mrm", msgid], timeout=10)

# ======================= ENDPOINTS: AUTH =======================
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    resp = templates.TemplateResponse(request, "login.html", {"error": error})
    return security_headers(resp)

@app.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    if not rate_check(ip):
        return templates.TemplateResponse(request, "login.html", {"error": "Çok fazla deneme. 10 dk sonra tekrar dene."}, status_code=429)
    if email != ADMIN_EMAIL or password != ADMIN_PASS:
        audit("login_fail", ip=ip, email=email)
        return templates.TemplateResponse(request, "login.html", {"error": "E-posta veya şifre hatalı."}, status_code=401)
    # send OTP
    code = gen_otp()
    save_json(OTP_STORE, {"email": email, "code": code, "exp": time.time() + OTP_TTL})
    await send_mail(email, f"Mail Admin giriş kodu: {code}", f"Mail Admin paneli için 2FA kodunuz: {code}\n\nBu kod 5 dakika geçerlidir. Talep etmediyseniz görmezden gelin.\n\nIP: {ip}\nZaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    audit("login_otp_sent", ip=ip, email=email)
    return RedirectResponse("/verify", status_code=303)

@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "verify.html", {"error": error})

@app.post("/verify")
async def verify_submit(request: Request, code: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    data = load_json(OTP_STORE, {})
    if not data or time.time() > data.get("exp", 0):
        return templates.TemplateResponse(request, "verify.html", {"error": "Kod süresi doldu. Tekrar giriş yap."}, status_code=401)
    if code.strip() != data.get("code"):
        audit("verify_fail", ip=ip)
        return templates.TemplateResponse(request, "verify.html", {"error": "Kod yanlış."}, status_code=401)
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

@app.get("/api/overview")
async def api_overview(request: Request):
    require_auth(request)
    lines = read_tail(EXIM_LOG, 5000)
    msgs = aggregate_messages(lines)
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
    lines = read_tail(EXIM_LOG, 20000)
    msgs = aggregate_messages(lines)
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
    lines = read_tail(EXIM_LOG, 20000)
    msgs = aggregate_messages(lines)
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

@app.get("/api/activity")
async def api_activity(
    request: Request,
    direction: str = Query("all"),
    status: str = Query("all"),
    domain: str = Query(""),
    q: str = Query(""),
    limit: int = Query(200),
):
    require_auth(request)
    lines = read_tail(EXIM_LOG, 5000)
    msgs = aggregate_messages(lines)
    if direction in ("in", "out"):
        msgs = [m for m in msgs if m["direction"] == direction]
    if status != "all":
        msgs = [m for m in msgs if m["status"] == status]
    if domain:
        msgs = [m for m in msgs if domain in (m["from"] or "") or any(domain in t for t in m["to"])]
    if q:
        ql = q.lower()
        msgs = [m for m in msgs if ql in (m["from"] or "").lower() or any(ql in t.lower() for t in m["to"]) or ql in m["msgid"].lower()]
    return {"events": msgs[:limit], "total": len(msgs)}

@app.get("/api/message/{msgid}")
async def api_message_detail(request: Request, msgid: str):
    require_auth(request)
    if not re.match(r"^[A-Za-z0-9\-]+$", msgid): raise HTTPException(400)
    # full log trace
    lines = sh(["grep", "-F", msgid, EXIM_LOG], timeout=5).splitlines()
    # headers from -Mvh (requires exim privilege; if fails, skip)
    headers = sh(["exim", "-Mvh", msgid], timeout=5)
    body_preview = sh(["exim", "-Mvb", msgid], timeout=5)[:2000]
    return {"msgid": msgid, "trace": lines[-50:], "headers": headers[:3000], "body": body_preview}

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

@app.get("/api/events/stream")
async def sse_events(request: Request):
    require_auth(request)
    async def gen():
        proc = await asyncio.create_subprocess_exec("tail", "-F", "-n", "0", EXIM_LOG, stdout=asyncio.subprocess.PIPE)
        try:
            while True:
                line = await proc.stdout.readline()
                if not line: break
                e = parse_line(line.decode("utf-8", errors="replace"))
                if e:
                    yield f"data: {json.dumps(e)}\n\n"
        finally:
            proc.terminate()
    return StreamingResponse(gen(), media_type="text/event-stream")

# ======================= HEALTH =======================
@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"

# ======================= SHELL (HTML) =======================
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not get_session(request):
        return RedirectResponse("/login", status_code=303)
    resp = HTMLResponse(APP_HTML.replace("{{EMAIL}}", get_session(request) or ""))
    return security_headers(resp)

# ======================= HTML TEMPLATES =======================
# NOTE: LOGIN_HTML / VERIFY_HTML moved to templates/login.html + templates/verify.html
# (Faz 1 Task 6 — Jinja2 refactor). APP_HTML still inlined; will move in later tasks.

APP_HTML = r"""<!doctype html>
<html lang="tr" class="dark"><head><meta charset="utf-8"><title>Mail Admin</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script defer src="https://unpkg.com/alpinejs@3.13.3/dist/cdn.min.js"></script>
<style>
  :root {
    --bg-0: #09090b; --bg-1: #0c0c0f; --bg-2: #111114; --bg-3: #18181b;
    --border: #232328; --border-hover: #32323a;
    --fg-0: #fafafa; --fg-1: #d4d4d8; --fg-2: #a1a1aa; --fg-3: #71717a; --fg-4: #52525b;
    --accent: #6366f1; --accent-hover: #818cf8;
    --success: #10b981; --warning: #f59e0b; --danger: #ef4444; --info: #06b6d4;
  }
  html, body { font-family: 'Inter', ui-sans-serif, system-ui, sans-serif; font-feature-settings: 'cv11', 'ss01'; letter-spacing: -0.011em; }
  .font-mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-feature-settings: 'cv11'; }
  body { background: var(--bg-0); color: var(--fg-0); }
  [x-cloak] { display: none !important; }
  .sidebar-item { display: flex; align-items: center; gap: 0.625rem; padding: 0.4rem 0.625rem; border-radius: 6px; color: var(--fg-2); font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.12s; }
  .sidebar-item:hover { background: var(--bg-3); color: var(--fg-0); }
  .sidebar-item.active { background: var(--bg-3); color: var(--fg-0); }
  .sidebar-item.active::before { content: ''; position: absolute; left: 0; width: 2px; height: 16px; background: var(--accent); border-radius: 0 2px 2px 0; }
  .sidebar-item i { width: 15px; height: 15px; stroke-width: 1.75; }
  .kbd { font-family: 'JetBrains Mono', monospace; font-size: 10px; padding: 1px 5px; background: var(--bg-2); border: 1px solid var(--border); border-radius: 3px; color: var(--fg-3); margin-left: auto; line-height: 14px; }
  .card { background: var(--bg-1); border: 1px solid var(--border); border-radius: 10px; }
  .card-hover:hover { border-color: var(--border-hover); }
  .pill { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; font-family: 'JetBrains Mono', monospace; }
  .pill-success { background: rgba(16,185,129,0.08); color: #6ee7b7; }
  .pill-warning { background: rgba(245,158,11,0.08); color: #fcd34d; }
  .pill-danger { background: rgba(239,68,68,0.08); color: #fca5a5; }
  .pill-info { background: rgba(99,102,241,0.08); color: #a5b4fc; }
  .pill-muted { background: rgba(113,113,122,0.08); color: var(--fg-3); }
  .dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
  .dot-ok { background: var(--success); } .dot-warn { background: var(--warning); } .dot-err { background: var(--danger); } .dot-mute { background: var(--fg-4); }
  .stat-number { font-size: 28px; font-weight: 600; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; line-height: 1; }
  .stat-label { font-size: 11px; font-weight: 500; color: var(--fg-3); text-transform: uppercase; letter-spacing: 0.04em; }
  .stat-delta { font-size: 11px; font-family: 'JetBrains Mono', monospace; }
  .delivery-bar { display: flex; width: 100%; height: 6px; background: var(--bg-3); border-radius: 3px; overflow: hidden; }
  .delivery-bar > div { transition: width 0.3s; }
  .table-row { border-bottom: 1px solid var(--border); }
  .table-row:last-child { border-bottom: none; }
  .table-row:hover { background: rgba(255,255,255,0.02); }
  .sparkline { width: 80px; height: 24px; }
  .activity-row { padding: 8px 12px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; font-size: 12px; }
  .activity-row:hover { background: rgba(255,255,255,0.02); }
  .activity-row:last-child { border-bottom: none; }
  .btn { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 6px; font-size: 12px; font-weight: 500; transition: all 0.12s; border: 1px solid transparent; }
  .btn-primary { background: var(--accent); color: white; }
  .btn-primary:hover { background: var(--accent-hover); }
  .btn-ghost { background: transparent; border-color: var(--border); color: var(--fg-1); }
  .btn-ghost:hover { background: var(--bg-3); border-color: var(--border-hover); }
  .btn i { width: 13px; height: 13px; stroke-width: 2; }
  .input { background: var(--bg-2); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 12px; color: var(--fg-0); }
  .input:focus { outline: none; border-color: var(--accent); }
  .section-title { font-size: 11px; font-weight: 600; color: var(--fg-3); text-transform: uppercase; letter-spacing: 0.06em; padding: 0 10px; margin-bottom: 6px; }
  .divider { height: 1px; background: var(--border); margin: 12px 0; }
  .scrollbar-thin::-webkit-scrollbar { width: 6px; height: 6px; }
  .scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
  .scrollbar-thin::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  .scrollbar-thin::-webkit-scrollbar-thumb:hover { background: var(--border-hover); }
  @keyframes pulse-glow { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .pulse { animation: pulse-glow 2s infinite; }
</style>
</head>
<body x-data="app()" x-init="init()" x-cloak style="background: var(--bg-0)">

<!-- CMD+K PALETTE -->
<div x-show="cmdK" x-transition.opacity @keydown.escape.window="cmdK=false" @keydown.window.cmd.k.prevent="cmdK=!cmdK" @keydown.window.ctrl.k.prevent="cmdK=!cmdK"
     class="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]" @click.self="cmdK=false" style="background: rgba(0,0,0,0.6); backdrop-filter: blur(4px)">
  <div class="w-full max-w-xl card" style="background: var(--bg-1)">
    <div class="flex items-center gap-2 px-3 py-2.5 border-b" style="border-color: var(--border)">
      <i data-lucide="search" class="w-4 h-4" style="color: var(--fg-3)"></i>
      <input x-model="cmdQuery" x-ref="cmdInput" placeholder="Ara: domain, mailbox, view..." class="flex-1 bg-transparent outline-none text-sm" style="color: var(--fg-0)">
      <span class="kbd">ESC</span>
    </div>
    <div class="max-h-80 overflow-auto scrollbar-thin py-1">
      <template x-for="c in cmdResults()" :key="c.key">
        <div @click="cmdRun(c)" class="px-3 py-2 flex items-center gap-2.5 cursor-pointer hover:bg-zinc-900 text-sm">
          <i :data-lucide="c.icon" class="w-3.5 h-3.5" style="color: var(--fg-3)"></i>
          <span x-text="c.title" style="color: var(--fg-0)"></span>
          <span x-show="c.hint" x-text="c.hint" class="text-xs font-mono ml-auto" style="color: var(--fg-3)"></span>
        </div>
      </template>
      <div x-show="cmdResults().length===0" class="px-3 py-6 text-center text-xs" style="color: var(--fg-3)">Eşleşme yok</div>
    </div>
  </div>
</div>

<!-- LAYOUT -->
<div class="flex h-screen">

  <!-- SIDEBAR -->
  <aside class="w-[220px] flex-shrink-0 flex flex-col" style="background: var(--bg-1); border-right: 1px solid var(--border)">
    <div class="px-3 py-3 flex items-center gap-2 border-b" style="border-color: var(--border)">
      <div class="w-7 h-7 rounded-md flex items-center justify-center" style="background: linear-gradient(135deg, var(--accent) 0%, #a855f7 100%)">
        <i data-lucide="mail" class="w-4 h-4 text-white"></i>
      </div>
      <div>
        <div class="text-sm font-semibold" style="color: var(--fg-0)">Mail Admin</div>
        <div class="text-[10px] font-mono" style="color: var(--fg-3)">atlas.bilgeworld.com</div>
      </div>
    </div>

    <nav class="flex-1 py-3 overflow-auto scrollbar-thin">
      <div class="section-title">Workspace</div>
      <div class="px-2 space-y-0.5 mb-4">
        <template x-for="n in nav" :key="n.key">
          <div @click="view=n.key" :class="'sidebar-item relative '+(view===n.key?'active':'')" :title="n.title">
            <i :data-lucide="n.icon"></i>
            <span x-text="n.label"></span>
            <span class="kbd" x-text="n.k"></span>
          </div>
        </template>
      </div>

      <div class="section-title">Tools</div>
      <div class="px-2 space-y-0.5">
        <div @click="cmdK=true; $nextTick(()=>$refs.cmdInput?.focus())" class="sidebar-item">
          <i data-lucide="search"></i>
          <span>Search</span>
          <span class="kbd">⌘K</span>
        </div>
        <div @click="view='deliverability'" class="sidebar-item">
          <i data-lucide="send"></i>
          <span>Test mail</span>
        </div>
      </div>
    </nav>

    <div class="p-3 border-t" style="border-color: var(--border)">
      <div class="flex items-center gap-2">
        <div class="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-semibold" style="background: var(--bg-3); color: var(--fg-1)" x-text="email.charAt(0).toUpperCase()"></div>
        <div class="flex-1 min-w-0">
          <div class="text-xs font-medium truncate" style="color: var(--fg-1)" x-text="email"></div>
          <div class="text-[10px]" style="color: var(--fg-3)">admin</div>
        </div>
        <form method="post" action="/logout" class="flex">
          <button class="p-1 rounded hover:bg-zinc-800" title="Çıkış"><i data-lucide="log-out" class="w-3.5 h-3.5" style="color: var(--fg-3)"></i></button>
        </form>
      </div>
    </div>
  </aside>

  <!-- MAIN -->
  <main class="flex-1 flex flex-col overflow-hidden">
    <!-- HEADER -->
    <header class="flex-shrink-0 flex items-center px-5 py-3 gap-3" style="background: var(--bg-0); border-bottom: 1px solid var(--border)">
      <h1 class="text-[15px] font-semibold" style="color: var(--fg-0)" x-text="currentTitle()"></h1>
      <span class="text-xs" style="color: var(--fg-3)" x-text="currentSubtitle()"></span>
      <div class="ml-auto flex items-center gap-2">
        <span class="text-[11px] font-mono" style="color: var(--fg-3)" x-text="lastRefresh"></span>
        <button @click="init()" class="btn btn-ghost"><i data-lucide="refresh-cw"></i>Yenile</button>
      </div>
    </header>

    <!-- CONTENT -->
    <div class="flex-1 overflow-auto scrollbar-thin" style="background: var(--bg-0)">

      <!-- OVERVIEW -->
      <div x-show="view==='overview'" class="p-5 space-y-4">
        <!-- HERO KPI GRID -->
        <div class="grid grid-cols-4 gap-3">
          <!-- Sent today -->
          <div class="card p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="stat-label">Bugün Giden</div>
              <i data-lucide="send" class="w-3.5 h-3.5" style="color: var(--fg-3)"></i>
            </div>
            <div class="stat-number" style="color: var(--fg-0)" x-text="overview.today_sent ?? 0"></div>
            <div class="flex items-center gap-2 mt-2">
              <canvas x-ref="spark_today" width="80" height="18" class="sparkline"></canvas>
              <span class="stat-delta" :style="deltaStyle('today')" x-text="deltaFmt('today')"></span>
            </div>
          </div>
          <!-- Delivered (7d) -->
          <div class="card p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="stat-label">7g Teslim Oranı</div>
              <i data-lucide="check-circle-2" class="w-3.5 h-3.5" style="color: var(--success)"></i>
            </div>
            <div class="stat-number" style="color: var(--fg-0)" x-text="deliveryRate()+'%'"></div>
            <div class="flex items-center gap-2 mt-2">
              <div class="delivery-bar flex-1">
                <div :style="'width:'+deliveryRate()+'%; background:var(--success)'"></div>
                <div :style="'width:'+bouncePct()+'%; background:var(--danger)'"></div>
                <div :style="'width:'+deferredPct()+'%; background:var(--warning)'"></div>
              </div>
            </div>
            <div class="text-[10px] font-mono mt-1" style="color: var(--fg-3)">
              <span x-text="overview.week_delivered ?? 0"></span>/<span x-text="overview.week_sent ?? 0"></span>
              · <span style="color:var(--danger)" x-text="overview.week_bounced ?? 0"></span> bounce
            </div>
          </div>
          <!-- Queue -->
          <div class="card p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="stat-label">Kuyruk</div>
              <i data-lucide="clock" class="w-3.5 h-3.5" :style="'color:'+(((overview.queue||0)>5)?'var(--warning)':'var(--fg-3)')"></i>
            </div>
            <div class="stat-number" :style="'color:'+(((overview.queue||0)>5)?'var(--warning)':'var(--fg-0)')" x-text="overview.queue ?? 0"></div>
            <div class="text-[10px] mt-2" style="color: var(--fg-3)">Bekleyen mesaj</div>
          </div>
          <!-- Brevo -->
          <div class="card p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="stat-label">Brevo Kota</div>
              <i data-lucide="gauge" class="w-3.5 h-3.5" style="color: var(--fg-3)"></i>
            </div>
            <div class="stat-number" style="color: var(--fg-0)"><span x-text="overview.brevo_credits ?? 0"></span><span class="text-base font-normal" style="color:var(--fg-3)">/300</span></div>
            <div class="delivery-bar mt-2" style="width: 100%">
              <div :style="'width:'+((overview.brevo_credits??0)/3)+'%; background: var(--accent)'"></div>
            </div>
          </div>
        </div>

        <!-- CHART + STATUS SUMMARY -->
        <div class="grid grid-cols-3 gap-3">
          <div class="card p-4 col-span-2">
            <div class="flex items-center justify-between mb-3">
              <div>
                <div class="text-sm font-semibold" style="color: var(--fg-0)">Gönderim Trendi</div>
                <div class="text-[11px]" style="color: var(--fg-3)">Son 7 gün · günlük</div>
              </div>
              <div class="flex items-center gap-3 text-[11px] font-mono">
                <span class="flex items-center gap-1"><span class="dot" style="background: var(--accent)"></span><span style="color: var(--fg-2)">sent</span></span>
                <span class="flex items-center gap-1"><span class="dot dot-ok"></span><span style="color: var(--fg-2)">delivered</span></span>
                <span class="flex items-center gap-1"><span class="dot dot-warn"></span><span style="color: var(--fg-2)">deferred</span></span>
                <span class="flex items-center gap-1"><span class="dot dot-err"></span><span style="color: var(--fg-2)">bounced</span></span>
              </div>
            </div>
            <div style="height: 200px"><canvas id="weekChart"></canvas></div>
          </div>
          <div class="card p-4">
            <div class="text-sm font-semibold mb-3" style="color: var(--fg-0)">PTR & Sistem</div>
            <div class="space-y-2.5">
              <div class="flex items-center justify-between">
                <span class="text-xs" style="color: var(--fg-3)">PTR Record</span>
                <span class="pill" :class="(overview.ptr?.status==='ok')?'pill-success':'pill-danger'" x-text="overview.ptr?.status==='ok'?'ok':'err'"></span>
              </div>
              <div class="font-mono text-[11px] break-all" style="color: var(--fg-1)" x-text="overview.ptr?.value || '—'"></div>
              <div class="divider"></div>
              <div class="flex items-center justify-between">
                <span class="text-xs" style="color: var(--fg-3)">Relay</span>
                <span class="pill pill-info" x-text="relay.current || '—'"></span>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs" style="color: var(--fg-3)">Aktif domain</span>
                <span class="text-xs font-mono" style="color: var(--fg-1)" x-text="(domains.domains||[]).filter(d=>d.relay).length+'/'+(domains.domains||[]).length"></span>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs" style="color: var(--fg-3)">Mailbox toplam</span>
                <span class="text-xs font-mono" style="color: var(--fg-1)" x-text="(domains.domains||[]).reduce((a,d)=>a+(d.accounts_count||0),0)"></span>
              </div>
            </div>
          </div>
        </div>

        <!-- DOMAIN TABLE + ACTIVITY -->
        <div class="grid grid-cols-5 gap-3">
          <div class="card col-span-3">
            <div class="flex items-center justify-between px-4 py-2.5 border-b" style="border-color: var(--border)">
              <div class="text-sm font-semibold" style="color: var(--fg-0)">Domain · Bugün</div>
              <span class="text-[11px]" style="color: var(--fg-3)" x-text="(overview.by_domain||[]).length+' domain'"></span>
            </div>
            <table class="w-full text-[12px]">
              <thead>
                <tr class="text-[10px] uppercase" style="color: var(--fg-3); letter-spacing: 0.06em">
                  <th class="text-left px-4 py-2 font-medium">Domain</th>
                  <th class="text-right px-2 py-2 font-medium">Sent</th>
                  <th class="text-right px-2 py-2 font-medium">Del%</th>
                  <th class="text-right px-2 py-2 font-medium">Bounce</th>
                  <th class="text-left px-4 py-2 font-medium">7g Trend</th>
                  <th class="text-right px-4 py-2 font-medium">Son</th>
                </tr>
              </thead>
              <tbody>
                <template x-for="(d,i) in (overview.by_domain||[])" :key="d.domain">
                  <tr class="table-row">
                    <td class="px-4 py-2 font-mono text-[12px]" style="color: var(--fg-1)" x-text="d.domain"></td>
                    <td class="text-right px-2 py-2 font-mono" style="color: var(--fg-0)" x-text="d.sent"></td>
                    <td class="text-right px-2 py-2 font-mono"><span :style="'color:'+(d.sent?(d.delivered/d.sent*100>=95?'var(--success)':'var(--warning)'):'var(--fg-4)')" x-text="d.sent ? Math.round(d.delivered/d.sent*100)+'%' : '—'"></span></td>
                    <td class="text-right px-2 py-2 font-mono" :style="'color:'+(d.bounced?'var(--danger)':'var(--fg-4)')" x-text="d.bounced||0"></td>
                    <td class="px-4 py-2"><canvas :id="'dspark_'+i" width="80" height="20" class="sparkline"></canvas></td>
                    <td class="text-right px-4 py-2 font-mono text-[10px]" style="color: var(--fg-3)" x-text="d.last || '—'"></td>
                  </tr>
                </template>
                <tr x-show="!overview.by_domain || overview.by_domain.length===0"><td colspan="6" class="px-4 py-8 text-center text-xs" style="color: var(--fg-3)">Bugün veri yok</td></tr>
              </tbody>
            </table>
          </div>

          <div class="card col-span-2 flex flex-col" style="max-height: 540px">
            <div class="flex items-center justify-between px-4 py-2.5 border-b" style="border-color: var(--border)">
              <div class="text-sm font-semibold" style="color: var(--fg-0)">Son Aktivite</div>
              <span class="text-[11px]" style="color: var(--fg-3)"><span x-text="(overview.recent||[]).length"></span> kayıt</span>
            </div>
            <div class="flex-1 overflow-auto scrollbar-thin">
              <template x-for="ev in (overview.recent||[])" :key="ev.msgid+ev.time+ev.to">
                <div class="activity-row" @click="openMsg(ev.msgid)">
                  <span class="font-mono text-[10px] flex-shrink-0" style="color: var(--fg-4); width: 52px" x-text="(ev.ts||'').slice(11,19)"></span>
                  <i :data-lucide="ev.direction==='in'?'arrow-down-left':'arrow-up-right'" class="w-3 h-3 flex-shrink-0" :style="'color:'+(ev.direction==='in'?'var(--info)':'var(--accent)')"></i>
                  <span class="font-mono truncate" style="color: var(--fg-1); font-size: 11px; max-width: 220px" x-text="ev.direction==='in'?ev.from:(Array.isArray(ev.to)?ev.to[0]:ev.to)"></span>
                  <span class="pill flex-shrink-0 ml-auto" :class="statusPill(ev.status)" x-text="ev.status"></span>
                </div>
              </template>
              <div x-show="(overview.recent||[]).length===0" class="px-4 py-8 text-center text-xs" style="color: var(--fg-3)">Henüz aktivite yok</div>
            </div>
          </div>
        </div>
      </div>

      <!-- DOMAINS -->
      <div x-show="view==='domains'" class="p-5">
        <div class="card">
          <div class="px-4 py-2.5 border-b flex items-center" style="border-color: var(--border)">
            <div class="text-sm font-semibold" style="color: var(--fg-0)">Domains</div>
            <span class="ml-2 text-[11px]" style="color: var(--fg-3)" x-text="(domains.domains||[]).length+' toplam'"></span>
          </div>
          <table class="w-full text-[12px]">
            <thead>
              <tr class="text-[10px] uppercase" style="color: var(--fg-3); letter-spacing: 0.06em">
                <th class="text-left px-4 py-2 font-medium">Domain</th>
                <th class="text-center px-2 py-2 font-medium">Hestia</th>
                <th class="text-center px-2 py-2 font-medium">Relay</th>
                <th class="text-center px-2 py-2 font-medium">SPF</th>
                <th class="text-center px-2 py-2 font-medium">DKIM</th>
                <th class="text-center px-2 py-2 font-medium">DMARC</th>
                <th class="text-center px-2 py-2 font-medium">MX</th>
                <th class="text-right px-2 py-2 font-medium">Hesap</th>
                <th class="text-right px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              <template x-for="d in (domains.domains||[])" :key="d.domain">
                <tr class="table-row cursor-pointer" @click="openDom(d.domain)">
                  <td class="px-4 py-2 font-mono" style="color: var(--fg-0)" x-text="d.domain"></td>
                  <td class="text-center"><span class="dot" :class="d.in_hestia?'dot-ok':'dot-mute'"></span></td>
                  <td class="text-center"><span class="dot" :class="d.relay?'dot-ok':'dot-mute'"></span></td>
                  <td class="text-center"><span class="dot" :class="dnsClass(d.spf)"></span></td>
                  <td class="text-center"><span class="dot" :class="dnsClass(d.dkim)"></span></td>
                  <td class="text-center"><span class="dot" :class="dnsClass(d.dmarc)"></span></td>
                  <td class="text-center"><span class="dot" :class="dnsClass(d.mx)"></span></td>
                  <td class="text-right px-2 py-2 font-mono" style="color: var(--fg-1)" x-text="d.accounts_count||0"></td>
                  <td class="text-right px-4 py-2"><i data-lucide="chevron-right" class="w-3.5 h-3.5 inline" style="color: var(--fg-3)"></i></td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>

      <!-- MAILBOXES -->
      <div x-show="view==='mailboxes'" class="p-5 space-y-3">
        <div class="flex items-center gap-2 flex-wrap">
          <select x-model="mbDomain" @change="loadMailboxes()" class="input">
            <option value="">Domain seç</option>
            <template x-for="d in (domains.domains||[]).filter(x=>x.in_hestia)" :key="d.domain">
              <option :value="d.domain" x-text="d.domain + ' (' + (d.accounts||0) + ')'"></option>
            </template>
          </select>
          <input x-show="mbDomain" x-model="mbFilter" placeholder="Mailbox ara..." class="input flex-1 max-w-xs">
          <div class="flex-1"></div>
          <button x-show="mbDomain" @click="loadMailboxes()" class="btn btn-ghost" title="Yenile"><i data-lucide="refresh-cw"></i></button>
          <button x-show="mbDomain" @click="showAdd=true" class="btn btn-primary"><i data-lucide="plus"></i>Mailbox ekle</button>
        </div>

        <div x-show="!mbDomain" class="card p-12 text-center">
          <div class="mx-auto w-12 h-12 flex items-center justify-center rounded-lg mb-3" style="background: var(--bg-1); color: var(--fg-3)"><i data-lucide="inbox"></i></div>
          <div class="text-sm font-medium mb-1" style="color: var(--fg-1)">Domain seç</div>
          <div class="text-xs" style="color: var(--fg-3)">Üstteki seçiciden bir domain seç — mailbox listesi gelecek</div>
        </div>

        <div x-show="mbDomain" class="grid grid-cols-4 gap-3">
          <div class="card p-3">
            <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Mailbox</div>
            <div class="text-xl font-semibold mt-1" x-text="mailboxes.length"></div>
          </div>
          <div class="card p-3">
            <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Aktif / Askıda</div>
            <div class="text-xl font-semibold mt-1"><span x-text="mailboxes.filter(m=>!m.suspended).length"></span><span style="color: var(--fg-3)"> / </span><span x-text="mailboxes.filter(m=>m.suspended).length" :style="mailboxes.filter(m=>m.suspended).length?'color:var(--warn)':'color:var(--fg-3)'"></span></div>
          </div>
          <div class="card p-3">
            <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Disk</div>
            <div class="text-xl font-semibold mt-1"><span x-text="mailboxes.reduce((s,m)=>s+(m.used_mb||0),0)"></span><span class="text-xs ml-1" style="color: var(--fg-3)">MB</span></div>
          </div>
          <div class="card p-3">
            <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">7g giden</div>
            <div class="text-xl font-semibold mt-1" x-text="mailboxes.reduce((s,m)=>s+(m.sent_7d||0),0)"></div>
          </div>
        </div>

        <div class="card overflow-hidden" x-show="mbDomain">
          <table class="w-full text-[12px]">
            <thead>
              <tr class="text-[10px] uppercase" style="color: var(--fg-3); letter-spacing: 0.06em; border-bottom: 1px solid var(--bd)">
                <th class="text-left px-4 py-2 font-medium">E-posta</th>
                <th class="text-left px-2 py-2 font-medium">Durum</th>
                <th class="text-right px-2 py-2 font-medium">Disk</th>
                <th class="text-right px-2 py-2 font-medium">Kota</th>
                <th class="text-right px-2 py-2 font-medium">7g ↑</th>
                <th class="text-right px-2 py-2 font-medium">7g ↓</th>
                <th class="text-left px-2 py-2 font-medium">Oluşturuldu</th>
                <th class="text-right px-4 py-2 font-medium">İşlem</th>
              </tr>
            </thead>
            <tbody>
              <template x-for="m in filteredMailboxes()" :key="m.email">
                <tr class="table-row cursor-pointer" @click="openMailbox(m)">
                  <td class="px-4 py-2.5 font-mono" style="color: var(--fg-1)">
                    <div class="flex items-center gap-2">
                      <span class="w-6 h-6 flex items-center justify-center rounded shrink-0" style="background: var(--bg-1); color: var(--accent); font-size:10px; font-weight:600" x-text="(m.user||'?').slice(0,1).toUpperCase()"></span>
                      <span x-text="m.email"></span>
                      <span x-show="m.fwd" class="text-[9px] px-1.5 py-0.5 rounded" style="background:rgba(99,102,241,0.12); color:var(--accent)" title="Yönlendirme">FWD</span>
                      <span x-show="m.autoreply" class="text-[9px] px-1.5 py-0.5 rounded" style="background:rgba(245,158,11,0.12); color:var(--warn)" title="Otomatik yanıt">AR</span>
                    </div>
                  </td>
                  <td class="px-2 py-2.5">
                    <span x-show="!m.suspended" class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(16,185,129,0.12); color:var(--ok)">aktif</span>
                    <span x-show="m.suspended" class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(245,158,11,0.12); color:var(--warn)">askıda</span>
                  </td>
                  <td class="text-right px-2 py-2.5 font-mono" style="color: var(--fg-2)"><span x-text="m.used_mb||0"></span><span class="text-[10px] ml-0.5" style="color: var(--fg-3)">MB</span></td>
                  <td class="text-right px-2 py-2.5 font-mono" style="color: var(--fg-3)" x-text="m.quota==='unlimited'?'∞':m.quota"></td>
                  <td class="text-right px-2 py-2.5 font-mono" :style="m.sent_7d?'color:var(--fg-1)':'color:var(--fg-3)'" x-text="m.sent_7d||0"></td>
                  <td class="text-right px-2 py-2.5 font-mono" :style="m.received_7d?'color:var(--fg-1)':'color:var(--fg-3)'" x-text="m.received_7d||0"></td>
                  <td class="px-2 py-2.5 font-mono text-[11px]" style="color: var(--fg-3)" x-text="m.date"></td>
                  <td class="text-right px-4 py-2.5 whitespace-nowrap" @click.stop>
                    <button @click="resetPass(m.user)" title="Şifre sıfırla" class="inline-flex items-center justify-center w-7 h-7 rounded hover:bg-zinc-800" style="color: var(--accent)"><i data-lucide="key" class="w-3.5 h-3.5"></i></button>
                    <button @click="delMb(m.user)" title="Sil" class="inline-flex items-center justify-center w-7 h-7 rounded hover:bg-zinc-800 ml-1" style="color: var(--danger)"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>
                  </td>
                </tr>
              </template>
              <tr x-show="mailboxes.length===0"><td colspan="8" class="px-4 py-10 text-center text-xs" style="color: var(--fg-3)">Bu domain'de mailbox yok</td></tr>
              <tr x-show="mailboxes.length>0 && filteredMailboxes().length===0"><td colspan="8" class="px-4 py-10 text-center text-xs" style="color: var(--fg-3)">Filtreye uyan mailbox yok</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ACTIVITY -->
      <div x-show="view==='activity'" class="p-5 space-y-3">
        <div class="flex items-center gap-2">
          <select x-model="actDir" @change="loadActivity()" class="input">
            <option value="">Tüm yön</option>
            <option value="out">Giden</option>
            <option value="in">Gelen</option>
          </select>
          <select x-model="actStatus" @change="loadActivity()" class="input">
            <option value="">Tüm durum</option>
            <option value="delivered">Delivered</option>
            <option value="deferred">Deferred</option>
            <option value="bounced">Bounced</option>
          </select>
          <input x-model="actQuery" @keyup.enter="loadActivity()" placeholder="e-posta veya ID ara" class="input flex-1">
          <button @click="toggleLive()" :class="liveTail?'btn-primary':'btn-ghost'" class="btn"><i data-lucide="radio"></i><span x-text="liveTail?'Live':'Off'"></span></button>
        </div>
        <div class="card">
          <div class="px-4 py-2.5 border-b flex items-center" style="border-color: var(--border)">
            <div class="text-sm font-semibold" style="color: var(--fg-0)">Mesaj akışı</div>
            <span class="ml-2 text-[11px]" style="color: var(--fg-3)"><span x-text="activity.length"></span> kayıt</span>
          </div>
          <div class="max-h-[70vh] overflow-auto scrollbar-thin">
            <template x-for="ev in activity" :key="ev.msgid+ev.time+ev.to">
              <div class="activity-row" @click="openMsg(ev.msgid)">
                <span class="font-mono text-[10px]" style="color: var(--fg-4); width: 72px" x-text="(ev.ts||'').slice(11,19)"></span>
                <i :data-lucide="ev.direction==='in'?'arrow-down-left':'arrow-up-right'" class="w-3 h-3" :style="'color:'+(ev.direction==='in'?'var(--info)':'var(--accent)')"></i>
                <span class="font-mono truncate" style="color: var(--fg-1); font-size: 11px; max-width: 200px" x-text="ev.from||'-'"></span>
                <i data-lucide="arrow-right" class="w-3 h-3" style="color: var(--fg-4)"></i>
                <span class="font-mono truncate" style="color: var(--fg-1); font-size: 11px; max-width: 200px" x-text="Array.isArray(ev.to)?ev.to[0]:ev.to"></span>
                <span class="pill ml-auto" :class="statusPill(ev.status)" x-text="ev.status"></span>
                <span class="font-mono text-[9px]" style="color: var(--fg-4); width: 96px; text-align: right" x-text="(ev.msgid||'').slice(0,12)"></span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- QUEUE -->
      <div x-show="view==='queue'" class="p-5">
        <div class="card">
          <div class="px-4 py-2.5 border-b flex items-center" style="border-color: var(--border)">
            <div class="text-sm font-semibold" style="color: var(--fg-0)">Exim Queue</div>
            <span class="ml-2 text-[11px]" style="color: var(--fg-3)" x-text="(queue.count||0)+' mesaj'"></span>
          </div>
          <table class="w-full text-[12px]">
            <thead>
              <tr class="text-[10px] uppercase" style="color: var(--fg-3); letter-spacing: 0.06em">
                <th class="text-left px-4 py-2 font-medium">ID</th>
                <th class="text-left px-2 py-2 font-medium">From</th>
                <th class="text-left px-2 py-2 font-medium">To</th>
                <th class="text-right px-2 py-2 font-medium">Boyut</th>
                <th class="text-right px-4 py-2 font-medium">Yaş</th>
              </tr>
            </thead>
            <tbody>
              <template x-for="q in (queue.items||[])" :key="q.id">
                <tr class="table-row">
                  <td class="px-4 py-2 font-mono text-[11px]" style="color: var(--fg-1)" x-text="q.id"></td>
                  <td class="px-2 py-2 font-mono text-[11px]" style="color: var(--fg-2)" x-text="q.from"></td>
                  <td class="px-2 py-2 font-mono text-[11px]" style="color: var(--fg-2)" x-text="q.to"></td>
                  <td class="text-right px-2 py-2 font-mono" style="color: var(--fg-3)" x-text="q.size"></td>
                  <td class="text-right px-4 py-2 font-mono text-[11px]" style="color: var(--warning)" x-text="q.age"></td>
                </tr>
              </template>
              <tr x-show="!queue.items || queue.items.length===0"><td colspan="5" class="px-4 py-12 text-center text-xs" style="color: var(--fg-3)">Kuyruk boş · Tüm mailler gidiyor</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- DELIVERABILITY -->
      <div x-show="view==='deliverability'" class="p-5 space-y-3">
        <div class="card">
          <div class="px-4 py-2.5 border-b" style="border-color: var(--border)">
            <div class="text-sm font-semibold" style="color: var(--fg-0)">Deliverability Matrix</div>
            <div class="text-[11px]" style="color: var(--fg-3)">SPF/DKIM/DMARC/MX durumu + relay sağlığı</div>
          </div>
          <table class="w-full text-[12px]">
            <thead>
              <tr class="text-[10px] uppercase" style="color: var(--fg-3); letter-spacing: 0.06em">
                <th class="text-left px-4 py-2 font-medium">Domain</th>
                <th class="text-center px-2 py-2 font-medium">SPF</th>
                <th class="text-center px-2 py-2 font-medium">DKIM</th>
                <th class="text-center px-2 py-2 font-medium">DMARC</th>
                <th class="text-center px-2 py-2 font-medium">MX</th>
                <th class="text-center px-2 py-2 font-medium">Relay</th>
              </tr>
            </thead>
            <tbody>
              <template x-for="d in (domains.domains||[])" :key="d.domain">
                <tr class="table-row">
                  <td class="px-4 py-2 font-mono" style="color: var(--fg-1)" x-text="d.domain"></td>
                  <td class="text-center"><span class="pill" :class="dnsPill(d.spf)" x-text="d.spf||'—'"></span></td>
                  <td class="text-center"><span class="pill" :class="dnsPill(d.dkim)" x-text="d.dkim||'—'"></span></td>
                  <td class="text-center"><span class="pill" :class="dnsPill(d.dmarc)" x-text="d.dmarc||'—'"></span></td>
                  <td class="text-center"><span class="pill" :class="dnsPill(d.mx)" x-text="d.mx||'—'"></span></td>
                  <td class="text-center"><span class="pill" :class="d.relay?'pill-success':'pill-muted'" x-text="d.relay?'ok':'—'"></span></td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <div class="card p-4">
          <div class="text-sm font-semibold mb-2" style="color: var(--fg-0)">Test Mail Gönder</div>
          <form @submit.prevent="sendTestMail()" class="flex gap-2">
            <input x-model="testTo" placeholder="test-xxxx@srv1.mail-tester.com" class="input flex-1">
            <select x-model="testFrom" class="input">
              <template x-for="d in (domains.domains||[]).filter(x=>x.relay)" :key="d.domain"><option :value="d.domain" x-text="'noreply@'+d.domain"></option></template>
            </select>
            <button class="btn btn-primary"><i data-lucide="send"></i>Gönder</button>
          </form>
          <p x-show="testResult" x-text="testResult" class="mt-2 text-[11px] font-mono" style="color: var(--fg-2)"></p>
        </div>
      </div>

      <!-- SETTINGS -->
      <div x-show="view==='settings'" x-init="loadRelay()" class="p-5 max-w-3xl">
        <div class="card p-4">
          <div class="flex items-center mb-3">
            <div>
              <div class="text-sm font-semibold" style="color: var(--fg-0)">SMTP Relay</div>
              <div class="text-[11px]" style="color: var(--fg-3)">Fallback switch — 8 domain'i farklı provider'a yönlendir</div>
            </div>
            <span class="ml-auto pill pill-info">aktif: <span x-text="relay.current || '—'"></span></span>
          </div>
          <div class="space-y-1.5">
            <template x-for="p in (relay.providers||[])" :key="p.id">
              <div class="flex items-center gap-3 p-3 rounded-lg border" style="border-color: var(--border); background: var(--bg-2)">
                <i :data-lucide="p.id==='brevo'?'cloud':(p.id==='mailjet'?'zap':'blend')" class="w-4 h-4" style="color: var(--fg-2)"></i>
                <div class="flex-1">
                  <div class="text-sm" style="color: var(--fg-0)" x-text="p.label"></div>
                  <div class="text-[10px] font-mono" style="color: var(--fg-3)" x-text="p.desc||p.host"></div>
                </div>
                <span x-show="p.id===relay.current" class="pill pill-success">● aktif</span>
                <button @click="switchRelay(p.id)" :disabled="p.id===relay.current || !p.configured" class="btn btn-primary disabled:opacity-30 disabled:cursor-not-allowed">Switch</button>
              </div>
            </template>
          </div>
          <div x-show="(relay.per_domain||[]).length" class="mt-4 p-3 rounded-lg" style="background: var(--bg-2); border: 1px solid var(--border)">
            <div class="text-[11px] font-medium mb-2" style="color: var(--fg-2)">Domain → Provider eşleşmesi</div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] font-mono">
              <template x-for="pd in relay.per_domain" :key="pd.domain">
                <div class="flex items-center">
                  <span x-text="pd.domain" class="flex-1" style="color: var(--fg-2)"></span>
                  <span :class="pd.provider==='mailjet' ? 'pill pill-warning' : (pd.provider==='brevo' ? 'pill pill-success' : 'pill pill-muted')" x-text="pd.provider"></span>
                </div>
              </template>
            </div>
          </div>
          <p class="text-[10px] mt-3 font-mono" style="color: var(--fg-4)">Script: /root/switch-smtp-relay.sh · chattr korumalı · systemctl reload exim4</p>
        </div>
        <div class="card p-4 mt-3">
          <div class="text-sm font-semibold mb-2" style="color: var(--fg-0)">Hesap</div>
          <div class="text-xs font-mono" style="color: var(--fg-2)" x-text="email"></div>
          <div class="text-[11px] mt-1" style="color: var(--fg-3)">Session: 12h, 2FA: mail OTP</div>
        </div>
      </div>

    </div>
  </main>
</div>

<!-- MSG MODAL -->
<div x-show="msgModal.open" x-cloak class="fixed inset-0 z-40 flex items-center justify-center p-4" style="background: rgba(0,0,0,0.6); backdrop-filter: blur(4px)" @click.self="msgModal.open=false">
  <div class="card max-w-2xl w-full max-h-[80vh] overflow-auto scrollbar-thin">
    <div class="px-4 py-3 border-b flex items-center" style="border-color: var(--border)">
      <div class="font-semibold text-sm" style="color: var(--fg-0)">Mesaj detayı</div>
      <span class="ml-2 text-[11px] font-mono" style="color: var(--fg-3)" x-text="msgModal.msgid"></span>
      <button @click="msgModal.open=false" class="ml-auto p-1 rounded hover:bg-zinc-800"><i data-lucide="x" class="w-4 h-4" style="color: var(--fg-3)"></i></button>
    </div>
    <div class="p-4 space-y-3">
      <div>
        <div class="text-[11px] uppercase font-medium mb-1" style="color: var(--fg-3)">Trace</div>
        <pre class="font-mono text-[11px] p-3 rounded" style="background: var(--bg-2); border: 1px solid var(--border); color: var(--fg-1); white-space: pre-wrap" x-text="(msgModal.data.trace||[]).join('\n')"></pre>
      </div>
      <div x-show="msgModal.data.headers">
        <div class="text-[11px] uppercase font-medium mb-1" style="color: var(--fg-3)">Headers</div>
        <pre class="font-mono text-[11px] p-3 rounded" style="background: var(--bg-2); border: 1px solid var(--border); color: var(--fg-1); white-space: pre-wrap; max-height: 200px; overflow: auto" x-text="msgModal.data.headers"></pre>
      </div>
    </div>
  </div>
</div>

<!-- DOMAIN MODAL -->
<div x-show="domModal.open" x-cloak class="fixed inset-0 z-40 flex items-center justify-center p-4" style="background: rgba(0,0,0,0.6); backdrop-filter: blur(4px)" @click.self="domModal.open=false">
  <div class="card max-w-2xl w-full max-h-[80vh] overflow-auto scrollbar-thin">
    <div class="px-4 py-3 border-b flex items-center" style="border-color: var(--border)">
      <div class="font-semibold text-sm" style="color: var(--fg-0)">Domain</div>
      <span class="ml-2 text-[11px] font-mono" style="color: var(--accent)" x-text="domModal.domain"></span>
      <button @click="domModal.open=false" class="ml-auto p-1 rounded hover:bg-zinc-800"><i data-lucide="x" class="w-4 h-4" style="color: var(--fg-3)"></i></button>
    </div>
    <div class="p-4 grid grid-cols-2 gap-3 text-[11px]">
      <div><div style="color: var(--fg-3)" class="uppercase mb-1">SPF</div><div class="font-mono break-all" style="color: var(--fg-1)" x-text="domModal.data.spf?.value||'—'"></div></div>
      <div><div style="color: var(--fg-3)" class="uppercase mb-1">DMARC</div><div class="font-mono break-all" style="color: var(--fg-1)" x-text="domModal.data.dmarc?.value||'—'"></div></div>
      <div><div style="color: var(--fg-3)" class="uppercase mb-1">DKIM</div><div class="font-mono break-all" style="color: var(--fg-1)" x-text="domModal.data.dkim?.value||'—'"></div></div>
      <div><div style="color: var(--fg-3)" class="uppercase mb-1">MX</div><div class="font-mono" style="color: var(--fg-1)" x-text="domModal.data.mx||'—'"></div></div>
      <div class="col-span-2">
        <div style="color: var(--fg-3)" class="uppercase mb-1">Hesaplar (<span x-text="(domModal.data.accounts||[]).length"></span>)</div>
        <div class="space-y-1">
          <template x-for="a in (domModal.data.accounts||[])" :key="a">
            <div class="font-mono" style="color: var(--fg-1)" x-text="a"></div>
          </template>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ADD MAILBOX MODAL -->
<div x-show="showAdd" x-cloak class="fixed inset-0 z-40 flex items-center justify-center p-4" style="background: rgba(0,0,0,0.6); backdrop-filter: blur(4px)" @click.self="showAdd=false">
  <div class="card max-w-md w-full p-4">
    <div class="text-sm font-semibold mb-3" style="color: var(--fg-0)">Mailbox ekle · <span class="font-mono" style="color: var(--accent)" x-text="mbDomain"></span></div>
    <form @submit.prevent="addMb()" class="space-y-2">
      <input x-model="addForm.user" placeholder="kullanıcı (noreply)" class="input w-full" required>
      <div class="flex gap-2">
        <input x-model="addForm.pass" placeholder="şifre" class="input flex-1">
        <button type="button" @click="addForm.pass=genPass()" class="btn btn-ghost"><i data-lucide="key"></i>Üret</button>
      </div>
      <div class="flex gap-2 justify-end pt-2">
        <button type="button" @click="showAdd=false" class="btn btn-ghost">İptal</button>
        <button class="btn btn-primary">Ekle</button>
      </div>
    </form>
  </div>
</div>

<!-- MAILBOX DETAIL DRAWER -->
<div x-show="mbDetailOpen" x-cloak x-transition.opacity class="fixed inset-0 z-50 flex" style="background: rgba(0,0,0,0.55); backdrop-filter: blur(3px)" @click.self="mbDetailOpen=false" @keydown.escape.window="mbDetailOpen=false">
  <div class="ml-auto w-full max-w-2xl h-full overflow-y-auto" style="background: var(--bg-0); border-left: 1px solid var(--bd)">
    <div class="px-5 py-4 sticky top-0 z-10" style="background: var(--bg-0); border-bottom: 1px solid var(--bd)">
      <div class="flex items-center justify-between gap-2">
        <div class="min-w-0">
          <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Mailbox</div>
          <div class="text-base font-semibold font-mono mt-0.5 truncate" style="color: var(--fg-0)" x-text="mbDetail.email||''"></div>
        </div>
        <button @click="mbDetailOpen=false" class="btn btn-ghost shrink-0"><i data-lucide="x"></i></button>
      </div>
    </div>
    <div class="p-5 space-y-4">
      <div class="grid grid-cols-2 gap-3">
        <div class="card p-3">
          <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Durum</div>
          <div class="text-sm mt-1" x-text="(mbDetail.info||{}).suspended?'Askıda':'Aktif'" :style="(mbDetail.info||{}).suspended?'color:var(--warn)':'color:var(--ok)'"></div>
        </div>
        <div class="card p-3">
          <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Disk / Kota</div>
          <div class="text-sm mt-1"><span x-text="((mbDetail.info||{}).used_mb||0)+' MB'"></span><span style="color: var(--fg-3)"> / </span><span x-text="(mbDetail.info||{}).quota==='unlimited'?'∞':((mbDetail.info||{}).quota||'—')"></span></div>
        </div>
        <div class="card p-3">
          <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Yönlendirme (FWD)</div>
          <div class="text-xs mt-1 font-mono break-all" style="color: var(--fg-2)" x-text="(mbDetail.info||{}).fwd||'—'"></div>
        </div>
        <div class="card p-3">
          <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Alias</div>
          <div class="text-xs mt-1 font-mono break-all" style="color: var(--fg-2)" x-text="(mbDetail.info||{}).alias||'—'"></div>
        </div>
        <div class="card p-3">
          <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Otomatik yanıt</div>
          <div class="text-xs mt-1" style="color: var(--fg-2)" x-text="(mbDetail.info||{}).autoreply?'Açık':'Kapalı'"></div>
        </div>
        <div class="card p-3">
          <div class="text-[10px] uppercase tracking-wider" style="color: var(--fg-3)">Oluşturuldu</div>
          <div class="text-xs mt-1 font-mono" style="color: var(--fg-2)"><span x-text="(mbDetail.info||{}).date||'—'"></span> <span style="color: var(--fg-3)" x-text="(mbDetail.info||{}).time||''"></span></div>
        </div>
      </div>

      <div class="flex gap-2 flex-wrap">
        <a :href="mbDetail.webmail_url||'#'" target="_blank" rel="noopener" class="btn btn-ghost"><i data-lucide="external-link"></i>Webmail</a>
        <button @click="resetPass((mbDetail.email||'').split('@')[0])" class="btn btn-ghost"><i data-lucide="key"></i>Şifre sıfırla</button>
        <button @click="delMb((mbDetail.email||'').split('@')[0])" class="btn btn-ghost" style="color: var(--danger)"><i data-lucide="trash-2"></i>Sil</button>
      </div>

      <div>
        <div class="flex items-center justify-between mb-2">
          <div class="text-[11px] uppercase tracking-wider font-medium" style="color: var(--fg-2)">Mesaj aktivitesi</div>
          <div class="text-[10px]" style="color: var(--fg-3)"><span x-text="(mbDetail.messages||[]).length"></span> mesaj · son 7g</div>
        </div>
        <div class="card overflow-hidden">
          <template x-for="ev in (mbDetail.messages||[])" :key="ev.msgid+'-'+ev.ts">
            <div class="px-3 py-2.5 text-[11px] flex items-center gap-2 hover:bg-zinc-900 cursor-pointer" style="border-top: 1px solid var(--bd)" @click="openMsg(ev.msgid)">
              <span class="font-mono text-[10px] shrink-0" style="color: var(--fg-3)" x-text="(ev.ts||'').slice(5,16)"></span>
              <i :data-lucide="ev.direction==='in'?'arrow-down-left':'arrow-up-right'" class="w-3 h-3 shrink-0" :style="ev.direction==='in'?'color:var(--accent)':'color:var(--ok)'"></i>
              <span class="font-mono truncate flex-1" style="color: var(--fg-1)" x-text="ev.direction==='in'?(ev.from||'—'):(Array.isArray(ev.to)?ev.to.join(', '):(ev.to||'—'))"></span>
              <span class="text-[9px] px-1.5 py-0.5 rounded shrink-0" :style="statusPill(ev.status)" x-text="ev.status"></span>
            </div>
          </template>
          <div x-show="(mbDetail.messages||[]).length===0" class="px-3 py-10 text-center text-xs" style="color: var(--fg-3)">Son 7 günde mesaj yok</div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- TOAST -->
<div x-show="toast.msg" x-cloak x-transition class="fixed bottom-4 right-4 z-50 card px-3 py-2 flex items-center gap-2" :class="toast.type==='err'?'border-red-700/60':'border-emerald-700/60'">
  <i :data-lucide="toast.type==='err'?'alert-circle':'check-circle-2'" class="w-4 h-4" :style="'color:'+(toast.type==='err'?'var(--danger)':'var(--success)')"></i>
  <span class="text-xs" style="color: var(--fg-1)" x-text="toast.msg"></span>
</div>

<script>
function app() { return {
  email: "{{EMAIL}}",
  view: localStorage.getItem("mv") || "overview",
  cmdK: false, cmdQuery: "",
  overview: {}, domains: {}, mailboxes: [], activity: [], queue: {}, relay: {},
  mbDomain: "", mbFilter: "", mbDetailOpen: false, mbDetail: {email:"",info:{},messages:[],webmail_url:""},
  actDir: "", actStatus: "", actQuery: "", liveTail: false, es: null,
  testTo: "", testFrom: "bilgestore.com", testResult: "",
  msgModal: {open:false, msgid:"", data:{}},
  domModal: {open:false, domain:"", data:{}},
  showAdd: false, addForm: {user:"", pass:""},
  toast: {msg:"", type:"ok"}, lastRefresh: "", weekChart: null, sparks: {},
  nav: [
    {key:"overview",label:"Genel Bakış",icon:"layout-dashboard",k:"G",title:"Overview"},
    {key:"domains",label:"Domain'ler",icon:"globe",k:"D",title:"Domains"},
    {key:"mailboxes",label:"Mailbox'lar",icon:"inbox",k:"M",title:"Mailboxes"},
    {key:"activity",label:"Aktivite",icon:"activity",k:"A",title:"Activity"},
    {key:"queue",label:"Kuyruk",icon:"list",k:"Q",title:"Queue"},
    {key:"deliverability",label:"Deliverability",icon:"shield-check",k:"V",title:"Deliverability"},
    {key:"settings",label:"Ayarlar",icon:"settings",k:",",title:"Settings"},
  ],
  async init() {
    await Promise.all([this.loadOverview(), this.loadDomains(), this.loadQueue(), this.loadRelay()]);
    this.lastRefresh = new Date().toLocaleTimeString("tr-TR", {hour:"2-digit", minute:"2-digit", second:"2-digit"});
    this.$nextTick(() => { this.drawWeekChart(); this.drawDomainSparks(); if (window.lucide) window.lucide.createIcons(); });
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if ((e.metaKey||e.ctrlKey) && e.key==="k") { e.preventDefault(); this.cmdK=!this.cmdK; this.$nextTick(()=>this.$refs.cmdInput?.focus()); return; }
      const map = {g:"overview", d:"domains", m:"mailboxes", a:"activity", q:"queue", v:"deliverability", ",":"settings"};
      if (map[e.key]) { this.view = map[e.key]; this.$nextTick(()=>{ if(window.lucide) window.lucide.createIcons(); }); }
    });
    this.$watch("view", v => { localStorage.setItem("mv", v); this.$nextTick(()=>{ if(window.lucide) window.lucide.createIcons(); this.drawDomainSparks(); }); if (v==="mailboxes" && this.mbDomain) this.loadMailboxes(); if (v==="activity") this.loadActivity(); });
  },
  currentTitle() { return (this.nav.find(n=>n.key===this.view)||{}).title || "Overview"; },
  currentSubtitle() {
    const t = {overview:"Gerçek zamanlı gönderim · 7 gün özet", domains:"DNS sağlık durumu + relay", mailboxes:"HestiaCP mailbox yönetimi", activity:"Mesaj akışı · Exim log parse", queue:"Exim bekleyen mesaj kuyruğu", deliverability:"SPF/DKIM/DMARC matrix + test gönderimi", settings:"SMTP relay + hesap"};
    return t[this.view] || "";
  },
  deliveryRate() { const s=this.overview.week_sent||0, d=this.overview.week_delivered||0; return s?Math.round(d/s*1000)/10:0; },
  bouncePct() { const s=this.overview.week_sent||0, b=this.overview.week_bounced||0; return s?Math.round(b/s*100):0; },
  deferredPct() { const s=this.overview.week_sent||0, d=this.overview.week_deferred||0; return s?Math.round(d/s*100):0; },
  deltaFmt(key) { return ""; },
  deltaStyle(key) { return "color: var(--fg-3)"; },
  statusPill(s) { return {delivered:"pill-success", deferred:"pill-warning", bounced:"pill-danger", pending:"pill-muted"}[s] || "pill-muted"; },
  dnsClass(s) { return {ok:"dot-ok", missing:"dot-err", multiple:"dot-warn", mismatch:"dot-err", weak:"dot-warn"}[s] || "dot-mute"; },
  dnsPill(s) { return {ok:"pill-success", missing:"pill-danger", multiple:"pill-warning", mismatch:"pill-danger", weak:"pill-warning"}[s] || "pill-muted"; },
  async loadOverview() { try { const r = await fetch("/api/overview"); this.overview = await r.json(); } catch (e) {} },
  async loadDomains() { try { const r = await fetch("/api/domains"); this.domains = await r.json(); } catch (e) {} },
  async loadMailboxes() { if (!this.mbDomain) return; try { const r = await fetch("/api/mailboxes?domain="+this.mbDomain); this.mailboxes = await r.json(); } catch (e) {} },
  async loadActivity() { try { const p = new URLSearchParams({dir:this.actDir,status:this.actStatus,q:this.actQuery}); const r = await fetch("/api/activity?"+p); this.activity = await r.json(); } catch (e) {} },
  async loadQueue() { try { const r = await fetch("/api/queue"); this.queue = await r.json(); } catch (e) {} },
  async loadRelay() { try { const r = await fetch("/api/relay/status"); this.relay = await r.json(); } catch (e) {} },
  async openMsg(id) { this.msgModal = {open:true, msgid:id, data:{}}; try { const r = await fetch("/api/message/"+id); this.msgModal.data = await r.json(); } catch(e){} },
  async openDom(d) { this.domModal = {open:true, domain:d, data:{}}; try { const r = await fetch("/api/domain/"+d); this.domModal.data = await r.json(); } catch(e){} },
  async switchRelay(pid) {
    if (!confirm("SMTP relay " + pid + " moduna çevirsin mi? Tüm domain aktif yapılanmayı değiştirir.")) return;
    const fd = new FormData(); fd.append("provider", pid);
    const r = await fetch("/api/relay/switch", {method:"POST", body:fd});
    if (r.ok) { const j = await r.json(); this.showToast("Switched → " + j.provider); this.loadRelay(); }
    else { const e = await r.text(); this.showToast("Hata: " + e.slice(0,120), "err"); }
  },
  async sendTestMail() {
    if (!this.testTo) return;
    const fd = new FormData(); fd.append("to", this.testTo); fd.append("domain", this.testFrom);
    const r = await fetch("/api/test-mail", {method:"POST", body:fd});
    if (r.ok) { const j = await r.json(); this.testResult = "✓ " + j.sent_from + " → " + j.to; }
    else { this.testResult = "✗ " + await r.text(); }
  },
  filteredMailboxes() {
    if (!this.mbFilter) return this.mailboxes;
    const q = this.mbFilter.toLowerCase();
    return this.mailboxes.filter(m => (m.email||"").toLowerCase().includes(q) || (m.fwd||"").toLowerCase().includes(q) || (m.alias||"").toLowerCase().includes(q));
  },
  statusPill(s) {
    if (s==='delivered') return 'background:rgba(16,185,129,0.12); color:var(--ok)';
    if (s==='deferred') return 'background:rgba(245,158,11,0.12); color:var(--warn)';
    if (s==='bounced') return 'background:rgba(239,68,68,0.12); color:var(--danger)';
    return 'background: var(--bg-1); color: var(--fg-3)';
  },
  async openMailbox(m) {
    if (!m || !m.user) return;
    this.mbDetailOpen = true;
    this.mbDetail = {email:m.email, info:{used_mb:m.used_mb,quota:m.quota,suspended:m.suspended,fwd:m.fwd,alias:m.alias,autoreply:m.autoreply,date:m.date,time:m.time}, messages:[], webmail_url:"https://snappymail."+this.mbDomain};
    try {
      const r = await fetch("/api/mailbox/detail?domain="+encodeURIComponent(this.mbDomain)+"&account="+encodeURIComponent(m.user));
      if (r.ok) this.mbDetail = await r.json();
    } catch(e) {}
    this.$nextTick(() => { if (window.lucide) window.lucide.createIcons(); });
  },
  async resetPass(account) {
    if (!account) return;
    const p = prompt("Yeni şifre (boş = otomatik üret):", "");
    if (p === null) return;
    const pass = p || this.genPass();
    if (pass.length < 10) { this.showToast("Şifre en az 10 karakter", "err"); return; }
    const fd = new FormData(); fd.append("domain", this.mbDomain); fd.append("account", account); fd.append("new_password", pass);
    const r = await fetch("/api/mailbox/reset", {method:"POST", body:fd});
    if (r.ok) this.showToast("Şifre: " + pass);
    else { const e = await r.text(); this.showToast("Hata: " + e.slice(0,100), "err"); }
  },
  async delMb(account) {
    if (!account) return;
    if (!confirm(account + "@" + this.mbDomain + " silinsin mi?")) return;
    const fd = new FormData(); fd.append("domain", this.mbDomain); fd.append("account", account);
    const r = await fetch("/api/mailbox/delete", {method:"POST", body:fd});
    if (r.ok) { this.showToast("Silindi"); this.mbDetailOpen=false; this.loadMailboxes(); }
    else { const e = await r.text(); this.showToast("Hata: " + e.slice(0,100), "err"); }
  },
  async addMb() {
    if (!this.addForm.user) return;
    const pass = this.addForm.pass || this.genPass();
    if (pass.length < 10) { this.showToast("Şifre en az 10 karakter", "err"); return; }
    const fd = new FormData(); fd.append("domain", this.mbDomain); fd.append("account", this.addForm.user); fd.append("password", pass);
    const r = await fetch("/api/mailbox/add", {method:"POST", body:fd});
    if (r.ok) { this.showToast("Eklendi: " + this.addForm.user + "@" + this.mbDomain + " · şifre: " + pass); this.showAdd=false; this.addForm={user:"",pass:""}; this.loadMailboxes(); }
    else { const e = await r.text(); this.showToast("Hata: " + e.slice(0,100), "err"); }
  },
  toggleLive() {
    this.liveTail = !this.liveTail;
    if (this.liveTail) {
      this.es = new EventSource("/api/events/stream");
      this.es.onmessage = ev => { try { JSON.parse(ev.data); this.loadActivity(); } catch(_){} };
    } else if (this.es) { this.es.close(); this.es = null; }
  },
  drawWeekChart() {
    const ctx = document.getElementById("weekChart"); if (!ctx) return;
    const w = this.overview.week || [];
    const labels = w.map(b => b.date.slice(5));
    const mk = (k, c) => ({label:k, data:w.map(b=>b[k]), backgroundColor:c, borderRadius:3, borderSkipped:false});
    if (this.weekChart) this.weekChart.destroy();
    this.weekChart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [
        mk("delivered", "#10b981"),
        mk("deferred", "#f59e0b"),
        mk("bounced", "#ef4444"),
      ]},
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false }, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: "#18181b", borderColor: "#32323a", borderWidth: 1, titleColor: "#fafafa", bodyColor: "#d4d4d8", titleFont: { size: 11 }, bodyFont: { size: 11 }, padding: 10 }
        },
        scales: {
          x: { stacked: true, ticks: { color: "#a1a1aa", font: { size: 11, weight: "500" } }, grid: { display: false }, border: { display: false } },
          y: { stacked: true, ticks: { color: "#71717a", font: { size: 10 }, maxTicksLimit: 5 }, grid: { color: "#27272a", drawTicks: false }, border: { display: false }, beginAtZero: true, suggestedMax: Math.max(5, ...(w.map(b=>(b.sent||0)))) },
        },
      }
    });
  },
  drawDomainSparks() {
    (this.overview.by_domain||[]).forEach((d,i) => {
      const ctx = document.getElementById("dspark_"+i); if (!ctx) return;
      if (this.sparks["d"+i]) this.sparks["d"+i].destroy();
      const data = (d.trend || [0,0,0,0,0,0,0]);
      this.sparks["d"+i] = new Chart(ctx, {
        type: "line",
        data: { labels: data.map((_,i)=>i), datasets: [{ data, borderColor: "#6366f1", backgroundColor: "rgba(99,102,241,0.15)", tension: 0.4, fill: true, borderWidth: 1, pointRadius: 0 }] },
        options: { responsive: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } }
      });
    });
  },
  cmdResults() {
    const q = this.cmdQuery.toLowerCase().trim();
    const items = [
      ...this.nav.map(n=>({key:"view-"+n.key, title:n.title, icon:n.icon, hint:n.k, act:()=>{this.view=n.key}})),
      ...(this.domains.domains||[]).map(d=>({key:"dom-"+d.domain, title:d.domain, icon:"globe", hint:"domain", act:()=>{this.openDom(d.domain)}})),
    ];
    if (!q) return items.slice(0, 8);
    return items.filter(x => x.title.toLowerCase().includes(q)).slice(0, 12);
  },
  cmdRun(c) { this.cmdK = false; this.cmdQuery = ""; c.act(); },
  genPass() { return Array.from(crypto.getRandomValues(new Uint8Array(18))).map(b => "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"[b%62]).join(""); },
  showToast(msg, type="ok") { this.toast = {msg, type}; setTimeout(()=>this.toast.msg="", 3000); },
}}
</script>

</body></html>"""
