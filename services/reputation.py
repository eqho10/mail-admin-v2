"""Composite reputation score from bounce + complaint + deferred rates.

Formula coefficients (locked Faz 3 spec section 3):
- bounce:    %1 = -5pt, cap 50pt
- complaint: %0.1 = -3pt, cap 30pt
- deferred:  %1 = -1pt, cap 10pt

Brevo down (complaint_rate=None) → complaint penalty zeroed, UI flags it.
"""
from typing import Optional

# Penalty coefficients (locked Faz 3 spec section 3)
BOUNCE_PENALTY_PER_PCT    = 5    # %1 bounce = -5pt
BOUNCE_PENALTY_CAP        = 50
COMPLAINT_PENALTY_PER_PCT = 30   # per 1%, i.e. %0.1 = -3pt
COMPLAINT_PENALTY_CAP     = 30
DEFERRED_PENALTY_PER_PCT  = 1    # %1 deferred = -1pt
DEFERRED_PENALTY_CAP      = 10


def composite_score(
    bounce_rate: float,
    complaint_rate: Optional[float],
    deferred_rate: float,
) -> int:
    """Return 0-100 reputation score.

    All rates are 0.0-1.0 decimals (NOT percentages).
    complaint_rate=None signals Brevo unavailable; complaint penalty is then 0.
    """
    bounce_penalty = min(BOUNCE_PENALTY_CAP, bounce_rate * 100 * BOUNCE_PENALTY_PER_PCT)
    complaint_penalty = (
        0.0 if complaint_rate is None
        else min(COMPLAINT_PENALTY_CAP, complaint_rate * 100 * COMPLAINT_PENALTY_PER_PCT)
    )
    deferred_penalty = min(DEFERRED_PENALTY_CAP, deferred_rate * 100 * DEFERRED_PENALTY_PER_PCT)
    score = max(0, min(100, 100 - bounce_penalty - complaint_penalty - deferred_penalty))
    return int(score)

import os
import httpx
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from services.db import get_conn
from services.exim import read_tail, aggregate_messages, EXIM_MAINLOG
from services.audit import audit

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_BASE = "https://api.brevo.com/v3"


def _fetch_exim_window(days: int) -> Dict[str, float]:
    """Aggregate Exim mainlog over last N days. Returns rates 0.0-1.0."""
    lines = read_tail(EXIM_MAINLOG, n_lines=20000)
    msgs = aggregate_messages(lines)
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = [m for m in msgs if _ts_after(m.get('ts', ''), cutoff)]
    total = len(recent)
    if total == 0:
        return {'bounce_rate': 0.0, 'deferred_rate': 0.0, 'total_sent': 0}
    bounced = sum(1 for m in recent if m.get('status') == 'bounced')
    deferred = sum(1 for m in recent if m.get('status') == 'deferred')
    return {
        'bounce_rate': bounced / total,
        'deferred_rate': deferred / total,
        'total_sent': total,
    }


def _ts_after(ts_str: str, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00').split('+')[0]) > cutoff
    except (ValueError, AttributeError):
        return False


def _fetch_brevo_complaints(days: int, total_sent: int) -> Optional[Dict[str, Any]]:
    """Fetch complaint events from Brevo. Returns None if API down/error."""
    if not BREVO_API_KEY or total_sent == 0:
        return {'complaint_count': 0, 'complaint_rate': 0.0}
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(
                f"{BREVO_BASE}/smtp/statistics/events",
                params={'event': 'complaint', 'startDate': start.isoformat(), 'endDate': end.isoformat(), 'limit': 100},
                headers={'api-key': BREVO_API_KEY, 'accept': 'application/json'},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            count = len(data.get('events', []))
            return {'complaint_count': count, 'complaint_rate': count / total_sent}
    except (httpx.HTTPError, ValueError):
        return None


def snapshot_now(source: str = 'cron') -> Dict[str, Any]:
    """Compute current composite score, INSERT into DB, prune old rows."""
    exim = _fetch_exim_window(days=30)
    brevo = _fetch_brevo_complaints(30, exim["total_sent"])
    complaint_rate = brevo['complaint_rate'] if brevo else None
    complaint_count = brevo['complaint_count'] if brevo else None

    score = composite_score(
        bounce_rate=exim['bounce_rate'],
        complaint_rate=complaint_rate,
        deferred_rate=exim['deferred_rate'],
    )

    ts = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO reputation_snapshots "
        "(ts, score, bounce_rate, complaint_rate, deferred_rate, total_sent, complaint_count, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, score, exim['bounce_rate'], complaint_rate, exim['deferred_rate'],
         exim['total_sent'], complaint_count, source)
    )
    conn.commit()
    deleted = _prune_old(days=90)
    audit("reputation_snapshot", score=score, source=source, pruned=deleted)
    return {
        'ts': ts, 'score': score,
        'bounce_rate': exim['bounce_rate'],
        'complaint_rate': complaint_rate,
        'deferred_rate': exim['deferred_rate'],
        'total_sent': exim['total_sent'],
        'complaint_count': complaint_count,
        'source': source,
        'complaint_available': brevo is not None,
    }


def _prune_old(days: int = 90) -> int:
    """DELETE rows older than N days. Return deleted count."""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM reputation_snapshots WHERE ts < datetime('now', '-' || ? || ' days')",
        (days,)
    )
    conn.commit()
    return cur.rowcount


def query_history(days: int = 30) -> List[Dict[str, Any]]:
    """Return snapshots from last N days, oldest first."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT ts, score, bounce_rate, complaint_rate, deferred_rate "
        "FROM reputation_snapshots "
        "WHERE ts > datetime('now', '-' || ? || ' days') "
        "ORDER BY ts ASC",
        (days,)
    ).fetchall()
    return [dict(r) for r in rows]
