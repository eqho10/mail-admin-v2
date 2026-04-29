"""Maildir message parser — body modes + attachments + auth header."""
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'maildir'


def test_parse_multipart_html_plain():
    from services.maildir import parse_message
    raw = (FIXTURE_DIR / 'multipart_html_plain.eml').read_bytes()
    parsed = parse_message(raw)
    assert parsed['html'] is not None
    assert 'HTML body here' in parsed['html']
    assert parsed['plain'] is not None
    assert 'Plain body here' in parsed['plain']
    assert parsed['attachments'] == []


def test_parse_plain_only():
    from services.maildir import parse_message
    raw = (FIXTURE_DIR / 'plain_only.eml').read_bytes()
    parsed = parse_message(raw)
    assert parsed['html'] is None
    assert 'Just plain text content' in parsed['plain']


def test_parse_with_attachment():
    from services.maildir import parse_message
    raw = (FIXTURE_DIR / 'with_attachment.eml').read_bytes()
    parsed = parse_message(raw)
    assert len(parsed['attachments']) == 1
    att = parsed['attachments'][0]
    assert att['filename'] == 'report.pdf'
    assert att['content_type'] == 'application/pdf'
    assert att['size'] > 0
    assert att['idx'] == 0


def test_parse_auth_results():
    from services.maildir import parse_message
    raw = (FIXTURE_DIR / 'auth_results_pass.eml').read_bytes()
    parsed = parse_message(raw)
    ar = parsed['auth_results']
    assert ar['dkim'] == 'pass'
    assert ar['spf'] == 'pass'
    assert ar['dmarc'] == 'pass'


def test_parse_no_auth_results_returns_none_chips():
    from services.maildir import parse_message
    raw = (FIXTURE_DIR / 'plain_only.eml').read_bytes()
    parsed = parse_message(raw)
    ar = parsed['auth_results']
    assert ar['dkim'] is None
    assert ar['spf'] is None
    assert ar['dmarc'] is None
