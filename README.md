# Mail Admin v2

FastAPI + Jinja2 tabanlı mail yönetim paneli (yeniden tasarım, Faz 1 — Foundation).
Mevcut `mail-admin` (port 8790) paralel çalışmaya devam eder; bu klasör (`mail-admin-v2`, port 8791) yeni geliştirme.

> **Faz 2 sonu durum:** v2'nin **dış erişimi YOKTUR** (sadece `127.0.0.1:8791`). Mevcut `https://mail-admin.bilgeworld.com/` eski servise gider. v2 → mevcut hostname geçişi Faz 5 sonu nginx upstream switch (`8790 → 8791`) + revert script ile yapılacak.

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
- **v2 local:** http://127.0.0.1:8791
- **v2 dış erişim:** Faz 5 sonu nginx switch ile açılacak

## Yapı

- `app.py` — FastAPI app + middleware + global exception handler + 8 admin sayfa router'ları
- `routers/` — endpoint grupları (`activity.py` Faz 2'de extract edildi)
- `services/` — iş mantığı (`error_translator`, `exim.py`, `audit.py` Faz 2'de extract edildi)
- `templates/` — Jinja2 (base + login + verify + 17 component partial + 9 page template)
- `static/` — bundled fonts (Inter, JetBrains Mono), lucide icons (v1.11.0), app.css, 6 JS modül
- `config/` — `error_dictionary.json` (15 entry), ileride dnsbl_zones.json
- `data/` — runtime state (audit.log, rate_limit.json, otp_store.json) — gitignored, **v2 izole** (eski `/root/mail-admin/` ile karışmaz)
- `tests/` — pytest (54 test: smoke, activity, cmdk, components, exim, sse, error_translator, auth_flow, static, error_handler, template_render)

## Hata Mesajı Sözlüğü

`config/error_dictionary.json` — runtime'da `services.error_translator.translate()` raw mesajı çevirir.

15 Day-1 entry: exim_no_input_file, brevo_421, brevo_quota, ssh_publickey, dkim_invalid, spf_fail, dmarc_reject, tls_handshake, mailbox_quota, exim_retry, exim_relay_denied, dns_servfail, blacklist_listed, spam_threshold, attachment_size.

Yeni entry eklemek için JSON dosyaya entry ekle, servisi restart et (auto-reload yok).

`translate()` thread-safe (double-checked lock), regex pre-compile cache.

## Tema

Light + dark CSS variables (`static/css/app.css`), `static/js/theme.js` head'de blocking yüklenir (FOUC önler), localStorage key `mail-admin-theme`. Toggle: `window.toggleTheme()`.

## Sistem

- systemd: `/etc/systemd/system/mail-admin-v2.service` (port 8791, parite eski unit'le: `User=root`, `Restart=always`, `RestartSec=3`, SESSION_SECRET 64-char)
- nginx: Faz 5 sonuna kadar YOK (loopback only). Faz 5 sonu: HestiaCP CLI ile mevcut `mail-admin.bilgeworld.com` upstream switch.
- Backup: `/root/backups/mail-admin-v2-*.tar.gz` (deploy.sh ile, 30 gün retention). **Backup = rollback artifact**: restore için `tar xzf <backup>.tar.gz -C /`.
- Baseline backup (Faz 1 başı): `/root/backups/mail-admin-20260428-182844-faz1-baseline.tar.gz`

## Geliştirme Notları (Faz 5 polish için debt listesi)

- **`error_dictionary.json` lint** — schema check (severity ∈ {info,warning,error}, regex compile, ID unique) tooling/lint script
- **deploy.sh rollback contract** — header comment ekle: backup = rollback artifact, restore: tar xzf ... -C /
- **`AUDIT_LOG.parent.mkdir(exist_ok=True)`** redundant (services/audit.py'de import-time mkdir var); app.py'da kalan kullanım yoksa cleanup
- **lucide.min.js version manifest** — `static/icons/.lucide-version` dosyası ekle (greppable)

## Faz İlerlemesi

- [x] **Faz 1 — Foundation** (`faz-1-foundation` tag): templates Jinja2'ye, static bundled, error translator + sözlük, global exception handler, auth flow integration test, deploy.sh, paralel servis port 8791
- [x] **Faz 2 — Core UX** (`faz-2-core-ux` tag): 8 sayfa nav iskeleti, 17 komponent + dev showcase, Cmd+K palette + registry, Activity tam impl (table + filter + SSE tail + drawer Detay sekmesi), routers/activity.py + services/{exim,audit}.py extract, debt 5 madde temizlendi (1, 3, 5, 7, 8)
- [ ] Faz 3 — Mesaj viewer detayı (Maildir parser) + Reputation gauge + Send-as test + DNS doctor
- [ ] Faz 4 — Suppression list + Blacklist check + Quarantine + Mailbox CRUD
- [ ] Faz 5 — Polish + Settings sayfası + nginx upstream switch (8790→8791) + canary flip + revert script

## Faz 1 Kapanış Metriği

- **Commits:** 15+
- **Tests:** 17 pass (`.venv/bin/pytest`, ~0.15s)
- **app.py:** 1798 satır (Faz 1 başı 1921, refactor sonrası -123)
- **Yeni dosyalar:** 4 template, 3 static dir (5 font + 2 JS + 1 CSS), error_translator + dictionary, 5 test dosyası, deploy.sh
- **v1 izolasyon:** `/root/mail-admin/` dosyaları v2 tarafından yazılmıyor (mtime ile ispatlandı)
- **Eski servis:** active, /healthz=ok (hiç dokunulmadı)

## Faz 2 Kapanış Metriği

- **Commits:** 18 (faz-1-foundation → faz-2-core-ux)
- **Tests:** 54 pass (`.venv/bin/pytest`, ~1.2s)
- **app.py:** 1798 → 724 satır (Activity + audit + exim extract; monolith dağıtıldı)
- **app.css:** ~127 → 410 satır (17 komponent stili)
- **Yeni dosyalar:** 1 router (activity.py), 2 service (exim.py, audit.py), 17 component partial, 9 page template, 6 JS modül (nav, cmdk, drawer, sse, toast, theme), 14 test dosyası toplam (9 yeni Faz 2'de)
- **v2 dış erişim:** YOK (loopback 8791, Faz 5 sonu nginx switch)
- **Eski servis:** active, /healthz=ok, dokunulmadı
- **Faz 1 debt:** 5/9 madde temizlendi (fallback JSON 1, sıkı smoke 3, datetime.UTC 5, malformed JSON defensive 7, alert-danger contrast 8); kalan 4 (lint script 2, deploy header 4, mkdir cleanup 6, lucide manifest 9) Faz 5 polish'e bırakıldı

## Faz 3 — Cron / Timer Infrastructure

- `mail-admin-v2-reputation.timer` — hourly reputation snapshot (OnCalendar=hourly, Persistent=true)
- `mail-admin-v2-reputation.service` — oneshot, calls `/usr/local/bin/mail-admin-v2-reputation-snapshot`
- Script POSTs to `http://127.0.0.1:8791/api/reputation/snapshot` with `X-Cron-Token` HMAC header
- Token in systemd unit env: `REPUTATION_CRON_TOKEN`
- Log: `/var/log/mail-admin-v2-reputation.log` (overwritten each fire)

Manual trigger: `systemctl start mail-admin-v2-reputation.service`
View timers: `systemctl list-timers | grep reputation`
