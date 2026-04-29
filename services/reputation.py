"""Composite reputation score from bounce + complaint + deferred rates.

Formula coefficients (locked Faz 3 spec section 3):
- bounce:    %1 = -5pt, cap 50pt
- complaint: %0.1 = -3pt, cap 30pt
- deferred:  %1 = -1pt, cap 10pt

Brevo down (complaint_rate=None) → complaint penalty zeroed, UI flags it.
"""
from typing import Optional


def composite_score(
    bounce_rate: float,
    complaint_rate: Optional[float],
    deferred_rate: float,
) -> int:
    """Return 0-100 reputation score.

    All rates are 0.0-1.0 decimals (NOT percentages).
    complaint_rate=None signals Brevo unavailable; complaint penalty is then 0.
    """
    bounce_penalty = min(50, bounce_rate * 100 * 5)
    complaint_penalty = 0 if complaint_rate is None else min(30, complaint_rate * 100 * 30)
    deferred_penalty = min(10, deferred_rate * 100 * 1)
    score = max(0, min(100, 100 - bounce_penalty - complaint_penalty - deferred_penalty))
    return int(score)
