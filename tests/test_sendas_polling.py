"""Send-as polling — find_by_msgid loop with timeout."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch
import pytest


def test_poll_arrival_succeeds_quickly(tmp_path, monkeypatch):
    monkeypatch.setattr('services.sendas._RUNS_DIR', tmp_path)
    fixture = Path(__file__).parent / 'fixtures' / 'maildir' / 'auth_results_pass.eml'

    # Seed run state
    state = {'run_id': 'r1', 'from': 'a@b.com', 'to': 'c@d.com',
             'msgid': '1abc', 'status': 'sent', 'is_local_to': True}
    (tmp_path / 'r1.json').write_text(json.dumps(state))

    monkeypatch.setattr('services.maildir.find_by_msgid',
                        lambda mid, recipient: str(fixture))

    from services.sendas import poll_arrival
    result = asyncio.run(poll_arrival('r1', timeout=5, interval=0.1))
    assert result['status'] == 'verified'
    assert result['auth_results']['dkim'] == 'pass'


def test_poll_arrival_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr('services.sendas._RUNS_DIR', tmp_path)
    state = {'run_id': 'r2', 'from': 'a@b.com', 'to': 'c@d.com',
             'msgid': '1nope', 'status': 'sent', 'is_local_to': True}
    (tmp_path / 'r2.json').write_text(json.dumps(state))

    monkeypatch.setattr('services.maildir.find_by_msgid',
                        lambda mid, recipient: None)

    from services.sendas import poll_arrival
    result = asyncio.run(poll_arrival('r2', timeout=0.5, interval=0.1))
    assert result['status'] == 'timeout'


def test_poll_skips_external(tmp_path, monkeypatch):
    monkeypatch.setattr('services.sendas._RUNS_DIR', tmp_path)
    state = {'run_id': 'r3', 'from': 'a@b.com', 'to': 'c@d.com',
             'msgid': '1abc', 'status': 'sent', 'is_local_to': False}
    (tmp_path / 'r3.json').write_text(json.dumps(state))

    from services.sendas import poll_arrival
    result = asyncio.run(poll_arrival('r3', timeout=5, interval=0.1))
    assert result['status'] == 'external_no_verify'
