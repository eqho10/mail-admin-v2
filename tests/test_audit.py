import json
from datetime import datetime, UTC
from services.audit import audit, AUDIT_LOG


def test_audit_writes_jsonl_with_utc_timestamp():
    AUDIT_LOG.unlink(missing_ok=True)
    audit("test_event", user="ekrem", action="login")
    line = AUDIT_LOG.read_text().strip().split("\n")[-1]
    rec = json.loads(line)
    assert rec["event"] == "test_event"
    assert rec["user"] == "ekrem"
    assert rec["ts"].endswith("+00:00") or rec["ts"].endswith("Z")
    parsed = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None  # UTC-aware (datetime.UTC migrate işe yaradı)
    AUDIT_LOG.unlink(missing_ok=True)
