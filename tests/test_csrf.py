import os
os.environ.setdefault('SESSION_SECRET', 'test-secret-do-not-use-in-prod')

import re

import pytest
from itsdangerous import TimestampSigner

from services.csrf import issue_token, verify_token


def test_issue_token_is_deterministic_for_same_session():
    """Same session value -> same token (so client can re-submit)."""
    t1 = issue_token('session-cookie-value-abc')
    t2 = issue_token('session-cookie-value-abc')
    assert t1 == t2
    assert len(t1) >= 32


def test_issue_token_differs_per_session():
    """Different session -> different token."""
    t1 = issue_token('session-a')
    t2 = issue_token('session-b')
    assert t1 != t2


def test_verify_accepts_matching_token():
    sess = 'session-xyz'
    token = issue_token(sess)
    assert verify_token(sess, token) is True


def test_verify_rejects_wrong_token():
    sess = 'session-xyz'
    assert verify_token(sess, 'not-a-valid-token-zzz') is False


def test_verify_rejects_token_from_different_session():
    t1 = issue_token('session-a')
    assert verify_token('session-b', t1) is False


def test_get_request_has_no_csrf_check(authed_client):
    """GET endpoints don't need CSRF (Faz 1 /aktivite still works)."""
    r = authed_client.get('/aktivite')
    assert r.status_code == 200


def test_post_without_csrf_token_is_rejected(authed_client):
    """POST to a state-changing endpoint without csrf_token form field returns 403."""
    # The authed_client fixture sets a default X-CSRF-Token header so that
    # other Faz 4a POST tests "just work"; here we explicitly drop it to
    # exercise the rejection path.
    authed_client.headers.pop('X-CSRF-Token', None)
    r = authed_client.post('/api/_test/csrf-protected', data={})
    assert r.status_code == 403
    assert 'csrf' in r.text.lower()


def test_post_with_valid_csrf_token_in_form_passes(authed_client):
    """Valid token in `csrf_token` form field passes."""
    page = authed_client.get('/aktivite')
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', page.text)
    assert m, "CSRF meta tag missing from authenticated page"
    token = m.group(1)
    r = authed_client.post('/api/_test/csrf-protected', data={'csrf_token': token})
    assert r.status_code == 200


def test_post_with_valid_csrf_token_in_header_passes(authed_client):
    """Valid token in X-CSRF-Token header also passes."""
    page = authed_client.get('/aktivite')
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', page.text)
    assert m
    token = m.group(1)
    r = authed_client.post('/api/_test/csrf-protected', headers={'X-CSRF-Token': token}, data={})
    assert r.status_code == 200
