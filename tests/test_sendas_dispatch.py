"""Send-as dispatch — sendmail subprocess + run state file."""
import json
from pathlib import Path
from unittest.mock import MagicMock


def test_dispatch_local_to_local_writes_state(tmp_path, monkeypatch):
    monkeypatch.setattr('services.sendas._RUNS_DIR', tmp_path)
    monkeypatch.setattr('services.mailboxes.list_all',
                        lambda: ['info@bilgeworld.com', 'noreply@bilgeworld.com'])

    fake_run = MagicMock()
    fake_run.returncode = 0
    fake_run.stdout = ''
    monkeypatch.setattr('services.sendas.subprocess.run', lambda *a, **kw: fake_run)
    monkeypatch.setattr('services.sendas._extract_msgid', lambda *a: '1xxxxx-yyyyy-zz')

    from services.sendas import dispatch
    result = dispatch(
        from_mailbox='info@bilgeworld.com',
        to_email='noreply@bilgeworld.com',
        subject='test',
        body='test body',
    )
    assert result['status'] == 'sent'
    assert result['msgid'] == '1xxxxx-yyyyy-zz'
    assert result['run_id']
    assert result['is_local_to'] is True

    state_file = tmp_path / f"{result['run_id']}.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state['from'] == 'info@bilgeworld.com'
    assert state['to'] == 'noreply@bilgeworld.com'


def test_dispatch_local_to_external(tmp_path, monkeypatch):
    monkeypatch.setattr('services.sendas._RUNS_DIR', tmp_path)
    monkeypatch.setattr('services.mailboxes.list_all',
                        lambda: ['info@bilgeworld.com'])
    fake_run = MagicMock(returncode=0, stdout='')
    monkeypatch.setattr('services.sendas.subprocess.run', lambda *a, **kw: fake_run)
    monkeypatch.setattr('services.sendas._extract_msgid', lambda *a: '1abc')

    from services.sendas import dispatch
    result = dispatch(
        from_mailbox='info@bilgeworld.com',
        to_email='someone@external.com',
    )
    assert result['is_local_to'] is False


def test_dispatch_rejects_invalid_from(tmp_path, monkeypatch):
    monkeypatch.setattr('services.sendas._RUNS_DIR', tmp_path)
    monkeypatch.setattr('services.mailboxes.list_all',
                        lambda: ['info@bilgeworld.com'])

    from services.sendas import dispatch, DispatchError
    import pytest
    with pytest.raises(DispatchError, match='not a local mailbox'):
        dispatch(from_mailbox='attacker@evil.com', to_email='someone@example.com')


def test_dispatch_rejects_invalid_to(tmp_path, monkeypatch):
    monkeypatch.setattr('services.sendas._RUNS_DIR', tmp_path)
    monkeypatch.setattr('services.mailboxes.list_all',
                        lambda: ['info@bilgeworld.com'])

    from services.sendas import dispatch, DispatchError
    import pytest
    with pytest.raises(DispatchError, match='not a valid email'):
        dispatch(from_mailbox='info@bilgeworld.com', to_email='not-an-email')
