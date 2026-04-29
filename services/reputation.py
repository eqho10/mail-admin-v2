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
