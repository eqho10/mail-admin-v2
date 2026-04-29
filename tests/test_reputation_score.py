"""Composite reputation score formula scenarios."""
from services.reputation import composite_score


def test_zero_negatives_returns_100():
    assert composite_score(bounce_rate=0.0, complaint_rate=0.0, deferred_rate=0.0) == 100


def test_one_pct_bounce_penalty_5():
    # 0.01 * 100 * 5 = 5pt penalty
    assert composite_score(bounce_rate=0.01, complaint_rate=0.0, deferred_rate=0.0) == 95


def test_five_pct_bounce_caps_at_50():
    # 0.05 * 100 * 5 = 25pt — under cap, expect 75
    assert composite_score(bounce_rate=0.05, complaint_rate=0.0, deferred_rate=0.0) == 75
    # 0.20 * 100 * 5 = 100pt → cap at 50, expect 50
    assert composite_score(bounce_rate=0.20, complaint_rate=0.0, deferred_rate=0.0) == 50


def test_complaint_half_pct_penalty_15():
    # 0.005 * 100 * 30 = 15pt
    assert composite_score(bounce_rate=0.0, complaint_rate=0.005, deferred_rate=0.0) == 85


def test_brevo_none_zeros_complaint_penalty():
    # complaint_rate=None → 0 penalty
    assert composite_score(bounce_rate=0.0, complaint_rate=None, deferred_rate=0.0) == 100


def test_all_caps_max():
    # bounce 0.20 → cap 50, complaint 0.05 → cap 30, deferred 0.20 → cap 10
    # Total penalty 90 → score 10
    assert composite_score(bounce_rate=0.20, complaint_rate=0.05, deferred_rate=0.20) == 10
