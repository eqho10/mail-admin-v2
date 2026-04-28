# Mail Admin v2

FastAPI + Jinja2 tabanlı mail yönetim paneli (yeniden tasarım, Faz 1 — Foundation).
Mevcut `mail-admin` (port 8790) paralel çalışmaya devam eder; bu klasör (`mail-admin-v2`, port 8791) yeni geliştirme.

> **Faz 1 sonu durum:** v2'nin **dış erişimi YOKTUR** (sadece `127.0.0.1:8791`). Mevcut `https://mail-admin.bilgeworld.com/` eski servise gider. v2 → mevcut hostname geçişi Faz 2 sonu nginx upstream switch (`8790 → 8791`) + revert script ile yapılacak.

## Hızlı Başlangıç

```bash
# Bağımlılıklar
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# Test
.venv/bin/pytest

# Servis (systemd)
systemctl restart mail-admin-v2
journalctl -u mail-admin-v2 -f

# Deploy (test + backup + restart + smoke + 30g retention)
./deploy.sh
```

## URL'ler

- **Prod (eski, dokunulmaz):** https://mail-admin.bilgeworld.com (port 8790)
- **v2 local (Faz 1):** http://127.0.0.1:8791
- **v2 dış erişim:** Faz 2 sonu açılacak (yukarıdaki not)

## Yapı

- `app.py` — FastAPI app + middleware + global exception handler (legacy monolith, Faz 2-3'te routers/'a bölünecek)
- `routers/` — endpoint grupları (Faz 2'de doldurulacak)
- `services/` — iş mantığı (`error_translator`, ileride exim/maildir/dnsbl/...)
- `templates/` — Jinja2 (base + login + verify + components/error_toast)
- `static/` — bundled fonts (Inter, JetBrains Mono), lucide icons (v1.11.0), app.css, theme.js
- `config/` — `error_dictionary.json` (15 entry), ileride dnsbl_zones.json
- `data/` — runtime state (audit.log, rate_limit.json, otp_store.json) — gitignored, **v2 izole** (eski `/root/mail-admin/` ile karışmaz)
- `tests/` — pytest (17 test: smoke, error_translator, auth_flow, static, error_handler, template_render)

## Hata Mesajı Sözlüğü

`config/error_dictionary.json` — runtime'da `services.error_translator.translate()` raw mesajı çevirir.

15 Day-1 entry: exim_no_input_file, brevo_421, brevo_quota, ssh_publickey, dkim_invalid, spf_fail, dmarc_reject, tls_handshake, mailbox_quota, exim_retry, exim_relay_denied, dns_servfail, blacklist_listed, spam_threshold, attachment_size.

Yeni entry eklemek için JSON dosyaya entry ekle, servisi restart et (auto-reload yok).

`translate()` thread-safe (double-checked lock), regex pre-compile cache.

## Tema

Light + dark CSS variables (`static/css/app.css`), `static/js/theme.js` head'de blocking yüklenir (FOUC önler), localStorage key `mail-admin-theme`. Toggle: `window.toggleTheme()`.

## Sistem

- systemd: `/etc/systemd/system/mail-admin-v2.service` (port 8791, parite eski unit'le: `User=root`, `Restart=always`, `RestartSec=3`, SESSION_SECRET 64-char)
- nginx: Faz 1'de YOK (loopback only). Faz 2 sonu: HestiaCP CLI ile mevcut `mail-admin.bilgeworld.com` upstream switch.
- Backup: `/root/backups/mail-admin-v2-*.tar.gz` (deploy.sh ile, 30 gün retention). **Backup = rollback artifact**: restore için `tar xzf <backup>.tar.gz -C /`.
- Baseline backup (Faz 1 başı): `/root/backups/mail-admin-20260428-182844-faz1-baseline.tar.gz`

## Geliştirme Notları (Faz 2-3 için debt listesi)

Code review'lardan ertelenen, Faz 1 scope dışı bırakılan iyileştirmeler:

- **`error_translator` fallback** ("unknown" entry) hâlâ kod içinde hardcoded; JSON'a `fallback` key olarak taşı (admin endpoint editable olur)
- **`error_dictionary.json` lint** — schema check (severity ∈ {info,warning,error}, regex compile, ID unique) tooling/lint script
- **`/login` smoke** çok gevşek (sadece "Mail Admin" stringini kontrol ediyor); tighten: `name="email"` veya `<form` arayan grep
- **deploy.sh rollback contract** — header comment ekle: "backup = rollback artifact, restore: tar xzf ... -C /"
- **`audit()` `datetime.utcnow()`** deprecated (Python 3.13'te kaldırılacak); `datetime.now(datetime.UTC)` kullan
- **`AUDIT_LOG.parent.mkdir(exist_ok=True)`** redundant (import-time mkdir var); temizle
- **`error_translator` fallback for missing/malformed JSON** — şu an FileNotFoundError propagate ediyor; defensive load with empty entries fallback
- **`alert-danger` light mode contrast** — WCAG AA borderline (~4.13:1, normal=4.5 hedef); Faz 2 polish'te `#b91c1c` (red-700) yap
- **lucide.min.js version manifest** — `static/icons/.lucide-version` dosyası ekle (greppable)

## Faz İlerlemesi

- [x] **Faz 1 — Foundation** (`faz-1-foundation` tag): templates Jinja2'ye, static bundled, error translator + sözlük, global exception handler, auth flow integration test, deploy.sh, paralel servis port 8791
- [ ] Faz 2 — Core UX (8 sekme IA, 16 komponent, dashboard scaffold, Cmd+K, Activity drawer, DNS doctor, message viewer)
- [ ] Faz 3 — Mesaj viewer detayı + Reputation gauge + Send-as test
- [ ] Faz 4 — Suppression list + Blacklist check + Quarantine + Mailbox CRUD
- [ ] Faz 5 — Polish + nginx upstream switch (8790→8791) + canary flip + revert script

## Faz 1 Kapanış Metriği

- **Commits:** 15+
- **Tests:** 17 pass (`.venv/bin/pytest`, ~0.15s)
- **app.py:** 1798 satır (Faz 1 başı 1921, refactor sonrası -123)
- **Yeni dosyalar:** 4 template, 3 static dir (5 font + 2 JS + 1 CSS), error_translator + dictionary, 5 test dosyası, deploy.sh
- **v1 izolasyon:** `/root/mail-admin/` dosyaları v2 tarafından yazılmıyor (mtime ile ispatlandı)
- **Eski servis:** active, /healthz=ok (hiç dokunulmadı)
