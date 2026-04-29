import json
from datetime import datetime, UTC

import pytest

import services.audit as audit_module


@pytest.fixture(autouse=True)
def isolated_audit_log(tmp_path, monkeypatch):
    """Redirect AUDIT_LOG to tmp_path so tests never touch the real production log."""
    fake_log = tmp_path / "audit.log"
    monkeypatch.setattr(audit_module, "AUDIT_LOG", fake_log)
    return fake_log


def test_audit_writes_jsonl_with_utc_timestamp(isolated_audit_log):
    from services.audit import audit
    audit("test_event", user="ekrem", action="login")
    line = isolated_audit_log.read_text().strip().split("\n")[-1]
    rec = json.loads(line)
    assert rec["event"] == "test_event"
    assert rec["user"] == "ekrem"
    assert rec["ts"].endswith("+00:00") or rec["ts"].endswith("Z")
    parsed = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
