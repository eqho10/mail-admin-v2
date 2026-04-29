"""Tests for /cron/dnsbl-snapshot — Faz 4b Task 4."""
import pytest

from services import dnsbl


def test_cron_dnsbl_snapshot_rejects_missing_token(client, monkeypatch):
    monkeypatch.setenv("DNSBL_SNAPSHOT_CRON_TOKEN", "secret-abc")
    r = client.post("/cron/dnsbl-snapshot")
    assert r.status_code == 401


def test_cron_dnsbl_snapshot_rejects_bad_token(client, monkeypatch):
    monkeypatch.setenv("DNSBL_SNAPSHOT_CRON_TOKEN", "secret-abc")
    r = client.post("/cron/dnsbl-snapshot", headers={"X-Cron-Token": "wrong"})
    assert r.status_code == 401


def test_cron_dnsbl_snapshot_rejects_when_env_not_set(client, monkeypatch):
    monkeypatch.delenv("DNSBL_SNAPSHOT_CRON_TOKEN", raising=False)
    r = client.post("/cron/dnsbl-snapshot", headers={"X-Cron-Token": "anything"})
    assert r.status_code == 401


async def test_cron_dnsbl_snapshot_writes_latest_with_valid_token(client, tmp_path, monkeypatch, mock_dns_resolver):
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnsbl, "LATEST_PATH", tmp_path / "dnsbl_latest.json")
    monkeypatch.setattr(dnsbl, "HISTORY_PATH", tmp_path / "dnsbl_history.json")
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", tmp_path / "alerts.json")
    monkeypatch.setenv("DNSBL_SNAPSHOT_CRON_TOKEN", "secret-abc")
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    dnsbl._cache = None
    dnsbl._cache_at = 0.0

    r = client.post("/cron/dnsbl-snapshot", headers={"X-Cron-Token": "secret-abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["listed_count"] == 0
    assert (tmp_path / "dnsbl_latest.json").exists()
