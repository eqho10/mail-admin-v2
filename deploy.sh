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

# Backup retention: 30 gün öncesi sil
find "$BACKUP_DIR" -name 'mail-admin-v2-*.tar.gz' -mtime +30 -delete

echo "Deploy OK at $TS"
