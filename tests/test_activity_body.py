"""/api/message/{msgid}/body + /attachment/{idx} + SSE topic extension."""
from pathlib import Path
from unittest.mock import patch

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'maildir'


def test_body_endpoint_returns_parsed(authed_client, monkeypatch):
    fixture = FIXTURE_DIR / 'multipart_html_plain.eml'
    monkeypatch.setattr('services.maildir.find_by_msgid',
                        lambda msgid, recipient: str(fixture))

    r = authed_client.get('/api/message/1abc-def-01/body?recipient=info@example.com')
    assert r.status_code == 200
    body = r.json()
    assert 'HTML body here' in body['html']
    assert 'Plain body here' in body['plain']
    assert body['source_file'] == str(fixture)


def test_body_404_when_maildir_missing(authed_client, monkeypatch):
    monkeypatch.setattr('services.maildir.find_by_msgid',
                        lambda msgid, recipient: None)
    r = authed_client.get('/api/message/1nope/body?recipient=info@example.com')
    assert r.status_code == 404
    assert r.json()['error'] == 'maildir_not_found'


def test_body_400_when_no_recipient(authed_client):
    r = authed_client.get('/api/message/1abc/body')
    assert r.status_code == 400


def test_attachment_streams_bytes(authed_client, monkeypatch):
    fixture = FIXTURE_DIR / 'with_attachment.eml'
    monkeypatch.setattr('services.maildir.find_by_msgid',
                        lambda msgid, recipient: str(fixture))

    r = authed_client.get('/api/message/1abc/attachment/0?recipient=info@example.com')
    assert r.status_code == 200
    assert 'attachment' in r.headers['content-disposition']
    assert 'report.pdf' in r.headers['content-disposition']


def test_sse_send_as_test_topic_accepted(authed_client):
    # Just checks the topic is in ALLOWED set — actual SSE streaming
    # tested in dedicated SSE test or manually
    from routers.activity import ALLOWED_SSE_TOPICS
    assert 'send_as_test' in ALLOWED_SSE_TOPICS
