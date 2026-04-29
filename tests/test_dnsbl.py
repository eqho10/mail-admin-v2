"""Tests for services/dnsbl.py — Task 2 (Faz 4b).

Covers:
  - check_zone: 4 outcomes (listed/clean/timeout/error)
  - get_status: parallel 20-zone lookup + 60s in-mem cache + IPv6 deferred
"""
import pytest

from services import dnsbl


async def test_check_zone_returns_listed_for_a_record(mock_dns_resolver):
    mock_dns_resolver({"179.1.92.153.zen.spamhaus.org": "listed:127.0.0.4"})
    result = await dnsbl.check_zone("153.92.1.179", "zen.spamhaus.org")
    assert result.status == "listed"
    assert result.return_code == "127.0.0.4"
    assert result.zone == "zen.spamhaus.org"


async def test_check_zone_returns_clean_for_nxdomain(mock_dns_resolver):
    mock_dns_resolver({"179.1.92.153.zen.spamhaus.org": "clean"})
    result = await dnsbl.check_zone("153.92.1.179", "zen.spamhaus.org")
    assert result.status == "clean"
    assert result.return_code is None


async def test_check_zone_returns_timeout(mock_dns_resolver):
    mock_dns_resolver({"179.1.92.153.zen.spamhaus.org": "timeout"})
    result = await dnsbl.check_zone("153.92.1.179", "zen.spamhaus.org", timeout=0.1)
    assert result.status == "timeout"


async def test_check_zone_returns_error_on_other_exception(mock_dns_resolver):
    mock_dns_resolver({"179.1.92.153.zen.spamhaus.org": "error:network unreachable"})
    result = await dnsbl.check_zone("153.92.1.179", "zen.spamhaus.org")
    assert result.status == "error"
    assert "network unreachable" in (result.return_text or "")


async def test_get_status_runs_all_zones_in_parallel(mock_dns_resolver, monkeypatch, tmp_path):
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnsbl, "LATEST_PATH", tmp_path / "dnsbl_latest.json")
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    dnsbl._cache = None
    dnsbl._cache_at = 0.0
    snap = await dnsbl.get_status()
    assert len(snap.results) == 20
    assert all(r.status == "clean" for r in snap.results)


async def test_get_status_returns_cached_within_ttl(mock_dns_resolver, monkeypatch, tmp_path):
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    dnsbl._cache = None
    dnsbl._cache_at = 0.0
    s1 = await dnsbl.get_status()
    # Re-mock to 'listed' — if cache works, second call returns first result.
    mock_dns_resolver({q: "listed:127.0.0.4" for q in qmap})
    s2 = await dnsbl.get_status()
    assert s1.ts == s2.ts
    assert all(r.status == "clean" for r in s2.results)


async def test_get_status_skipped_v6_when_env_unset(mock_dns_resolver, monkeypatch, tmp_path):
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    monkeypatch.delenv("DNSBL_CHECK_IP_V6", raising=False)
    dnsbl._cache = None
    dnsbl._cache_at = 0.0
    snap = await dnsbl.get_status()
    # ip field should be IPv4 only.
    assert snap.ip == "1.2.3.4"
