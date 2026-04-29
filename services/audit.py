"""Audit log helper — JSONL formatında AUDIT_LOG'a yazar, datetime.UTC kullanır."""
import json
from datetime import datetime, UTC
from pathlib import Path

AUDIT_LOG = Path(__file__).resolve().parent.parent / "data" / "audit.log"
AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)


def audit(event: str, **kwargs) -> None:
    """Olay kaydı. Her satır JSON, ts ISO-8601 UTC."""
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **kwargs,
    }
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
