#!/bin/bash
set -euo pipefail

ROOT="/root/mail-admin-v2"
SERVICE="mail-admin-v2"
BACKUP_DIR="/root/backups"
TS=$(date +%Y-%m-%d-%H%M%S)

cd "$ROOT"

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
fi

echo "[smoke] Faz 2+3 smoke set passed."

# Backup retention: 30 gün öncesi sil
find "$BACKUP_DIR" -name 'mail-admin-v2-*.tar.gz' -mtime +30 -delete

echo "Deploy OK at $TS"
