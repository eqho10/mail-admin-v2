"""Maildir finder — smart narrow scan via recipient + Hestia user mapping."""
import json
from pathlib import Path
import pytest


def test_find_by_msgid_uses_recipient_path(tmp_path, monkeypatch):
    # Build fake Maildir tree: /tmp.../home/ekrem/mail/example.com/info/Maildir/cur/<file>
    fake_home = tmp_path / 'home' / 'ekrem' / 'mail' / 'example.com' / 'info' / 'Maildir' / 'cur'
    fake_home.mkdir(parents=True)
    msg_file = fake_home / '12345.M0P0.host'
    msg_file.write_text(
        "Received: from origin (1.2.3.4) by mx.bilgeworld.com with esmtps\n"
        "          id 1xxabc-def-01\n"
        "          for info@example.com; Tue, 29 Apr 2026 10:00:00 +0000\n"
        "From: a@b.com\nTo: info@example.com\nSubject: hi\n\nbody"
    )

    import services.maildir as md
    monkeypatch.setattr(md, '_MAIL_ROOT', str(tmp_path / 'home'))
    monkeypatch.setattr(md, '_resolve_hestia_user', lambda domain: 'ekrem')

    result = md.find_by_msgid('1xxabc-def-01', recipient='info@example.com')
    assert result is not None
    assert result == str(msg_file)


def test_find_by_msgid_returns_none_when_missing(tmp_path, monkeypatch):
    import services.maildir as md
    monkeypatch.setattr(md, '_MAIL_ROOT', str(tmp_path))  # empty
    monkeypatch.setattr(md, '_resolve_hestia_user', lambda domain: 'ekrem')

    result = md.find_by_msgid('1nope-nope-nope', recipient='info@example.com')
    assert result is None


def test_find_by_msgid_invalid_recipient_returns_none(monkeypatch):
    import services.maildir as md
    result = md.find_by_msgid('1abc', recipient='not-an-email')
    assert result is None
