# Mail Admin v2 — systemd units

Bu klasör Mail Admin v2'nin tekrarlayan iş (cron) tanımlarını barındırır. Asıl
servis (`mail-admin-v2.service`) `/etc/systemd/system/` altında durmaya devam
eder; buradaki dosyalar yalnızca **timer + ek oneshot servisler** içindir ve
versiyon kontrol altındadır.

## Birim listesi

- `mail-admin-v2-mailbox-stats.timer` — Her 5 dakikada bir
  `mail-admin-v2-mailbox-stats.service`'i tetikler.
- `mail-admin-v2-mailbox-stats.service` — Tek seferlik (oneshot) curl;
  `POST /cron/refresh-mailbox-stats` çağrısı ile mailbox cache'ini yeniler.

## Kurulum

1. Cron token'ını üret ve env dosyasına yaz:

   ```bash
   sudo install -m 0600 /dev/null /etc/mail-admin-v2-cron.env
   sudo tee /etc/mail-admin-v2-cron.env >/dev/null <<EOF
   MAILBOX_STATS_CRON_TOKEN=$(openssl rand -hex 32)
   EOF
   ```

2. Aynı token'ı `mail-admin-v2.service` ortamına da ekle (drop-in):

   ```bash
   sudo systemctl edit mail-admin-v2
   ```

   Açılan editöre:

   ```
   [Service]
   Environment="MAILBOX_STATS_CRON_TOKEN=<aynı hex>"
   ```

3. Birimleri yerine kopyala + etkinleştir:

   ```bash
   sudo cp systemd/mail-admin-v2-mailbox-stats.timer  /etc/systemd/system/
   sudo cp systemd/mail-admin-v2-mailbox-stats.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now mail-admin-v2-mailbox-stats.timer
   ```

4. Doğrulama:

   ```bash
   systemctl list-timers mail-admin-v2-mailbox-stats.timer --no-pager
   journalctl -u mail-admin-v2-mailbox-stats.service -n 20 --no-pager
   ```

`deploy.sh` artık bu klasörü tarar ve değişen birim varsa otomatik kopyalar +
`daemon-reload` + `enable --now` çalıştırır. Yeni token gerekmiyorsa kurulum 3.
adımı atlamak mümkündür; sadece dosya içerikleri değiştiyse senkron olur.

## Notlar

- `EnvironmentFile=/etc/mail-admin-v2-cron.env` zorunlu. Dosya yoksa servis
  `failed` durumuna düşer; sebep `journalctl -u mail-admin-v2-mailbox-stats`
  loglarında görünür.
- Timer `OnBootSec=2min` ile boot'tan 2 dk sonra ilk kez tetikler, ardından
  `OnUnitActiveSec=5min` aralığıyla devam eder.
- Endpoint başarılı yanıt vermezse `curl -fsS` non-zero döner ve unit `failed`
  olur — `systemctl status` üzerinden alarm görünür.
