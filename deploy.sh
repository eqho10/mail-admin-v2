#!/bin/bash
set -euo pipefail

ROOT="/root/mail-admin-v2"
SERVICE="mail-admin-v2"
BACKUP_DIR="/root/backups"
TS=$(date +%Y-%m-%d-%H%M%S)

cd "$ROOT"

# Auto-install/refresh systemd units if changed
if [ -d "$ROOT/systemd" ]; then
  for unit in mail-admin-v2-mailbox-stats.timer mail-admin-v2-mailbox-stats.service \
              mail-admin-v2-dnsbl-snapshot.timer mail-admin-v2-dnsbl-snapshot.service; do
    if [ -f "$ROOT/systemd/$unit" ]; then
      if ! cmp -s "$ROOT/systemd/$unit" "/etc/systemd/system/$unit" 2>/dev/null; then
        echo "[deploy] systemd unit changed: $unit, syncing"
        sudo cp "$ROOT/systemd/$unit" "/etc/systemd/system/$unit"
        SYSTEMD_RELOAD=1
      fi
    fi
  done
  if [ "${SYSTEMD_RELOAD:-0}" = "1" ]; then
    sudo systemctl daemon-reload
    sudo systemctl enable --now mail-admin-v2-mailbox-stats.timer
    sudo systemctl enable --now mail-admin-v2-dnsbl-snapshot.timer \
      || echo "[deploy] WARN: dnsbl-snapshot timer enable failed (DNSBL_SNAPSHOT_CRON_TOKEN may be unset)"
  fi
fi

# Faz 4b — sudoers check (warn-only). Service runs as root currently, so
# `sudo /usr/sbin/exim4 -bV` works without an entry; this check is forward-
# looking for the eventual non-root service user.
SVC_USER=$(systemctl show -p User --value mail-admin-v2.service 2>/dev/null || echo "root")
if [ "$SVC_USER" != "root" ] && [ -n "$SVC_USER" ]; then
  if ! sudo -n -u "$SVC_USER" /usr/sbin/exim4 -bV >/dev/null 2>&1; then
    echo "[deploy] WARN: sudoers entry missing for $SVC_USER. Install:"
    echo "  sudo install -m 0440 deploy/sudoers.mail-admin-v2 /etc/sudoers.d/mail-admin-v2"
    echo "  sudo visudo -c -f /etc/sudoers.d/mail-admin-v2"
  fi
fi

echo "[1/5] Test suite..."
.venv/bin/pytest -q tests/ || { echo 'Tests failed, aborting'; exit 1; }

echo "[2/5] Backup..."
mkdir -p "$BACKUP_DIR"
tar czf "$BACKUP_DIR/mail-admin-v2-$TS.tar.gz" \
  --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='data/audit.log' --exclude='data/rate_limit.json' --exclude='data/otp_store.json' \
  -C / root/mail-admin-v2

echo "[3/5] Restart service..."
systemctl restart "$SERVICE"
sleep 2

echo "[4/5] Service health..."
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "Service not active after restart"
  journalctl -u "$SERVICE" -n 20 --no-pager
  exit 1
fi

echo "[5/5] Smoke test..."
curl -fsS http://127.0.0.1:8791/healthz | grep -qx ok || { echo 'Smoke /healthz failed'; exit 1; }
curl -fsS http://127.0.0.1:8791/login | grep -q 'Mail Admin' || { echo 'Smoke /login failed'; exit 1; }

# Faz 2 smoke — login + verify cookie üreterek admin sayfaları + API'i doğrula.
# SESSION_SECRET systemd unit Environment'tan okunur (root-only).
SESSION_SECRET=$(systemctl show mail-admin-v2 -p Environment --value | tr ' ' '\n' | grep '^SESSION_SECRET=' | cut -d= -f2-)
ADMIN_EMAIL=$(systemctl show mail-admin-v2 -p Environment --value | tr ' ' '\n' | grep '^ADMIN_USER=' | cut -d= -f2-)
if [ -z "$SESSION_SECRET" ]; then
  echo "[smoke] SESSION_SECRET unit'ten okunamadı; yalnızca login marker doğrulanacak."
else
  COOKIE=$(.venv/bin/python -c "from itsdangerous import TimestampSigner; \
    s=TimestampSigner('$SESSION_SECRET'); print(s.sign('${ADMIN_EMAIL:-ekrem.mutlu@hotmail.com.tr}'.encode()).decode())")
  echo "[smoke] cookie ile /aktivite + /api/activity doğrulanıyor..."
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/aktivite | grep -q 'data-page="activity"' \
    || { echo '[smoke] FAIL: /aktivite marker yok'; exit 1; }
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/api/activity | grep -q '"messages"' \
    || { echo '[smoke] FAIL: /api/activity payload yok'; exit 1; }
fi

# /login her durumda doğrulanır (cookie istemez)
curl -fsSL http://127.0.0.1:8791/login | grep -q 'data-page="login"' \
  || { echo '[smoke] FAIL: /login marker yok'; exit 1; }

# Faz 3 smoke — reputation + maildir endpoints + cmdk Send-as registry hit
if [ -n "$SESSION_SECRET" ]; then
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/api/reputation/current | grep -q '"score"' \
    || { echo '[smoke] FAIL: /api/reputation/current'; exit 1; }
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/api/reputation/history?days=7 | grep -q '"points"' \
    || { echo '[smoke] FAIL: /api/reputation/history'; exit 1; }
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/api/mailboxes/all | grep -q '"mailboxes"' \
    || { echo '[smoke] FAIL: /api/mailboxes/all'; exit 1; }

  # Faz 4a smoke — mailboxes + suppression auth gates
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/mailboxes | grep -q 'data-page="mailboxes"' \
    || { echo '[smoke] FAIL: /mailboxes marker yok'; exit 1; }
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/suppression | grep -q 'data-page="suppression"' \
    || { echo '[smoke] FAIL: /suppression marker yok'; exit 1; }

  # /mailboxes/api/list with valid domain (set +e: domain listesi boşsa grep 1 döner, kabul)
  set +o pipefail
  FIRST_DOMAIN=$(curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/mailboxes \
                 | grep -oP 'href="/mailboxes\?domain=\K[^"]+' | head -1 || true)
  set -o pipefail
  if [ -n "$FIRST_DOMAIN" ]; then
    curl -fsSL --cookie "ma_sess=$COOKIE" "http://127.0.0.1:8791/mailboxes/api/list?domain=$FIRST_DOMAIN" | grep -q '"mailboxes"' \
      || { echo '[smoke] FAIL: /mailboxes/api/list yok'; exit 1; }
  fi

  # /cron/refresh-mailbox-stats (HMAC token; env yoksa skip)
  set +o pipefail
  CRON_TOKEN=$(systemctl show mail-admin-v2 -p Environment --value | tr ' ' '\n' | grep '^MAILBOX_STATS_CRON_TOKEN=' | cut -d= -f2- || true)
  set -o pipefail
  if [ -n "$CRON_TOKEN" ]; then
    curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" http://127.0.0.1:8791/cron/refresh-mailbox-stats \
      | grep -q '"refreshed_at"\|"status"' \
      || { echo '[smoke] FAIL: /cron/refresh-mailbox-stats'; exit 1; }
  fi
fi

echo "[smoke] Faz 2+3+4a smoke set passed."

# --- Faz 4b smoke tests ---
set +o pipefail
if [ -n "$SESSION_SECRET" ]; then
  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/quarantine | grep -q 'data-page="quarantine"' \
    || { echo '[smoke] FAIL: /quarantine marker yok'; exit 1; }
  echo "  ✓ /quarantine reachable"

  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/blacklist | grep -q 'data-page="blacklist"' \
    || { echo '[smoke] FAIL: /blacklist marker yok'; exit 1; }
  echo "  ✓ /blacklist reachable"

  curl -fsSL --cookie "ma_sess=$COOKIE" http://127.0.0.1:8791/filters | grep -q 'data-page="filters"' \
    || { echo '[smoke] FAIL: /filters marker yok'; exit 1; }
  echo "  ✓ /filters reachable"
fi

# /cron/dnsbl-snapshot rejects bad token (always testable, no auth needed)
DNSBL_BAD_CODE=$(curl -fsS -o /dev/null -w "%{http_code}" -X POST \
  -H "X-Cron-Token: nope" http://127.0.0.1:8791/cron/dnsbl-snapshot || echo "fail")
if [ "$DNSBL_BAD_CODE" = "401" ]; then
  echo "  ✓ /cron/dnsbl-snapshot rejects bad token (401)"
else
  echo "  ✗ /cron/dnsbl-snapshot bad-token smoke FAIL ($DNSBL_BAD_CODE)" && exit 1
fi

# /cron/dnsbl-snapshot accepts valid token if env is set
DNSBL_TOKEN=$(systemctl show mail-admin-v2 -p Environment --value | tr ' ' '\n' | grep '^DNSBL_SNAPSHOT_CRON_TOKEN=' | cut -d= -f2- || true)
if [ -n "$DNSBL_TOKEN" ]; then
  DNSBL_OK_CODE=$(curl -fsS -o /dev/null -w "%{http_code}" -X POST \
    -H "X-Cron-Token: $DNSBL_TOKEN" --max-time 60 http://127.0.0.1:8791/cron/dnsbl-snapshot || echo "fail")
  if [ "$DNSBL_OK_CODE" = "200" ]; then
    echo "  ✓ /cron/dnsbl-snapshot succeeds with valid token"
  else
    echo "  WARN: /cron/dnsbl-snapshot failed even with token ($DNSBL_OK_CODE)"
  fi
else
  echo "  WARN: DNSBL_SNAPSHOT_CRON_TOKEN unset; skipping authenticated cron smoke"
fi
set -o pipefail

echo "[smoke] Faz 4b smoke set passed."

# Backup retention: 30 gün öncesi sil
find "$BACKUP_DIR" -name 'mail-admin-v2-*.tar.gz' -mtime +30 -delete

echo "Deploy OK at $TS"
