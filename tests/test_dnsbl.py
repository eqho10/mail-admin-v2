"""Tests for services/dnsbl.py — Task 2 + Task 3 (Faz 4b).

Covers:
  - check_zone: 4 outcomes (listed/clean/timeout/error)
  - get_status: parallel 20-zone lookup + 60s in-mem cache + IP validation
  - refresh_and_persist: atomic write, history rolling, alert fan-out
  - diff_for_alert: clean/timeout/error → listed transition emission only
"""
import json

import pytest

from services import dnsbl
from services.dnsbl import Snapshot, ZoneResult


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


# ---------------------------------------------------------------------------
# Task 3: refresh_and_persist + history rolling + alert diff.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refresh_and_persist_writes_latest_json(mock_dns_resolver, monkeypatch, tmp_path):
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnsbl, "LATEST_PATH", tmp_path / "dnsbl_latest.json")
    monkeypatch.setattr(dnsbl, "HISTORY_PATH", tmp_path / "dnsbl_history.json")
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", tmp_path / "blacklist_alerts.json")
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    dnsbl._cache = None

    snap = await dnsbl.refresh_and_persist()
    assert (tmp_path / "dnsbl_latest.json").exists()
    saved = json.loads((tmp_path / "dnsbl_latest.json").read_text())
    assert saved["ip"] == "1.2.3.4"
    assert len(saved["results"]) == 20


@pytest.mark.asyncio
async def test_refresh_and_persist_appends_history_and_trims(mock_dns_resolver, monkeypatch, tmp_path):
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnsbl, "LATEST_PATH", tmp_path / "dnsbl_latest.json")
    monkeypatch.setattr(dnsbl, "HISTORY_PATH", tmp_path / "dnsbl_history.json")
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", tmp_path / "blacklist_alerts.json")
    # Pre-seed history with 120 entries
    seed = [{"ts": f"2026-01-{i+1:02d}T00:00:00+00:00", "ip": "1.2.3.4", "results": []} for i in range(120)]
    (tmp_path / "dnsbl_history.json").write_text(json.dumps(seed))

    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": "clean" for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    dnsbl._cache = None

    await dnsbl.refresh_and_persist()
    history = json.loads((tmp_path / "dnsbl_history.json").read_text())
    assert len(history) == 120  # capped, oldest dropped
    assert history[-1]["ip"] == "1.2.3.4"  # newest at end


def test_diff_for_alert_emits_event_on_clean_to_listed():
    prev_results = [
        ZoneResult("zen.spamhaus.org", "clean", None, None, 100),
        ZoneResult("b.barracudacentral.org", "clean", None, None, 110),
    ]
    curr_results = [
        ZoneResult("zen.spamhaus.org", "listed", "127.0.0.4", None, 120),
        ZoneResult("b.barracudacentral.org", "clean", None, None, 110),
    ]
    prev = Snapshot(ts="2026-04-29T00:00:00+00:00", ip="1.2.3.4", results=prev_results)
    curr = Snapshot(ts="2026-04-29T06:00:00+00:00", ip="1.2.3.4", results=curr_results)
    events = dnsbl.diff_for_alert(prev, curr)
    assert len(events) == 1
    assert events[0].zone == "zen.spamhaus.org"
    assert events[0].return_code == "127.0.0.4"


def test_diff_for_alert_emits_for_timeout_to_listed():
    prev = Snapshot("t1", "1.2.3.4", [ZoneResult("z", "timeout", None, None, 0)])
    curr = Snapshot("t2", "1.2.3.4", [ZoneResult("z", "listed", "127.0.0.2", None, 0)])
    assert len(dnsbl.diff_for_alert(prev, curr)) == 1


def test_diff_for_alert_no_event_when_already_listed():
    prev = Snapshot("t1", "1.2.3.4", [ZoneResult("z", "listed", "127.0.0.4", None, 0)])
    curr = Snapshot("t2", "1.2.3.4", [ZoneResult("z", "listed", "127.0.0.4", None, 0)])
    assert dnsbl.diff_for_alert(prev, curr) == []


def test_diff_for_alert_no_event_when_listed_to_clean():
    prev = Snapshot("t1", "1.2.3.4", [ZoneResult("z", "listed", "127.0.0.4", None, 0)])
    curr = Snapshot("t2", "1.2.3.4", [ZoneResult("z", "clean", None, None, 0)])
    assert dnsbl.diff_for_alert(prev, curr) == []  # delisting is not an alert


def test_diff_for_alert_handles_missing_prev():
    curr = Snapshot("t2", "1.2.3.4", [ZoneResult("z", "listed", "127.0.0.4", None, 0)])
    # First-ever snapshot with listed → still emits (treat missing prev as clean)
    assert len(dnsbl.diff_for_alert(None, curr)) == 1


@pytest.mark.asyncio
async def test_refresh_and_persist_no_duplicate_alert_when_still_listed(mock_dns_resolver, monkeypatch, tmp_path):
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnsbl, "LATEST_PATH", tmp_path / "dnsbl_latest.json")
    monkeypatch.setattr(dnsbl, "HISTORY_PATH", tmp_path / "dnsbl_history.json")
    monkeypatch.setattr(dnsbl, "ALERTS_PATH", tmp_path / "blacklist_alerts.json")
    monkeypatch.setenv("DNSBL_CHECK_IP", "1.2.3.4")
    qmap = {f"{dnsbl._reverse_ipv4('1.2.3.4')}.{z}": ("listed:127.0.0.4" if z == "zen.spamhaus.org" else "clean") for z in dnsbl.DNSBL_ZONES}
    mock_dns_resolver(qmap)
    dnsbl._cache = None

    await dnsbl.refresh_and_persist()
    alerts1 = json.loads((tmp_path / "blacklist_alerts.json").read_text())
    assert len(alerts1) == 1

    # Second refresh — same listed state — no new alert
    dnsbl._cache = None
    await dnsbl.refresh_and_persist()
    alerts2 = json.loads((tmp_path / "blacklist_alerts.json").read_text())
    assert len(alerts2) == 1  # no growth


# ---------------------------------------------------------------------------
# P4: invalid DNSBL_CHECK_IP raises ValueError early.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_status_raises_on_invalid_dnsbl_check_ip(monkeypatch, tmp_path):
    monkeypatch.setenv("DNSBL_CHECK_IP", "not-an-ip")
    monkeypatch.setattr(dnsbl, "DATA_DIR", tmp_path)
    dnsbl._cache = None
    with pytest.raises(ValueError, match="not a valid IPv4"):
        await dnsbl.get_status()
