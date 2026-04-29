"""Tests for services/hestia.py — write CLI functions.

NOTE: tests reference exceptions/functions through the `hestia` module rather
than via direct symbol import. test_hestia_read.py performs
`importlib.reload(services.hestia)`; once that runs, any symbol previously
imported by name (e.g. `from services.hestia import HestiaCLIError`) points
at the *pre-reload* class object, while the running module raises
post-reload instances. `pytest.raises(StaleClass)` then fails to match.
Reading via `hestia.HestiaCLIError` always resolves against the live module.
"""
import subprocess
import pytest

import services.hestia as hestia


def _fake_run_factory(returncode: int = 0, stdout: str = "", stderr: str = ""):
    captured = {}
    class FakeResult:
        def __init__(self):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr
    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["kwargs"] = kw
        return FakeResult()
    return fake_run, captured


def test_add_mailbox_calls_v_add_mail_account(monkeypatch, tmp_path):
    fake, cap = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(hestia, "TRIGGER_FILE", tmp_path / "trigger")
    hestia.add_mailbox("bilgeworld.com", "test", "VeryStrongPass#123!", 1024)
    assert cap["argv"][0].endswith("v-add-mail-account")
    assert cap["argv"][1] == hestia.HESTIA_USER
    assert cap["argv"][2] == "bilgeworld.com"
    assert cap["argv"][3] == "test"
    assert cap["argv"][4] == "VeryStrongPass#123!"
    assert cap["argv"][5] == "1024"
    assert (tmp_path / "trigger").exists()


def test_add_mailbox_rejects_invalid_user(monkeypatch):
    fake, _ = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(hestia.HestiaCLIError) as exc:
        hestia.add_mailbox("bilgeworld.com", "Invalid User!", "VeryStrongPass#123!", 1024)
    assert "user" in str(exc.value).lower() or "local" in str(exc.value).lower()


def test_add_mailbox_rejects_weak_password(monkeypatch):
    fake, _ = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(hestia.HestiaCLIError) as exc:
        hestia.add_mailbox("bilgeworld.com", "test", "weak", 1024)
    assert "password" in str(exc.value).lower() or "şifre" in str(exc.value).lower()


def test_add_mailbox_rejects_invalid_quota(monkeypatch):
    fake, _ = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(hestia.HestiaCLIError):
        hestia.add_mailbox("bilgeworld.com", "test", "VeryStrongPass#123!", 0)
    with pytest.raises(hestia.HestiaCLIError):
        hestia.add_mailbox("bilgeworld.com", "test", "VeryStrongPass#123!", 99999999)


def test_add_mailbox_translates_stderr_on_failure(monkeypatch, tmp_path):
    fake, _ = _fake_run_factory(
        returncode=2,
        stderr="Error: mail account test exists",
    )
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(hestia, "TRIGGER_FILE", tmp_path / "trigger")
    with pytest.raises(hestia.HestiaCLIError) as exc:
        hestia.add_mailbox("bilgeworld.com", "test", "VeryStrongPass#123!", 1024)
    assert exc.value.translated["id"] == "hestia_user_exists"


def test_delete_mailbox_calls_correct_cli(monkeypatch, tmp_path):
    fake, cap = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(hestia, "TRIGGER_FILE", tmp_path / "trigger")
    hestia.delete_mailbox("bilgeworld.com", "test")
    assert cap["argv"][0].endswith("v-delete-mail-account")


def test_change_quota_validates_range(monkeypatch):
    fake, _ = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(hestia.HestiaCLIError):
        hestia.change_quota("bilgeworld.com", "test", 0)


def test_add_alias_validates_local_part(monkeypatch):
    fake, _ = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(hestia.HestiaCLIError):
        hestia.add_alias("bilgeworld.com", "test", "Bad Alias!")


def test_set_forward_validates_email(monkeypatch):
    fake, _ = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(hestia.HestiaCLIError):
        hestia.set_forward("bilgeworld.com", "test", "not-an-email")


def test_subprocess_timeout_raises_translated(monkeypatch, tmp_path):
    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=10)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(hestia, "TRIGGER_FILE", tmp_path / "trigger")
    with pytest.raises(hestia.HestiaCLIError) as exc:
        hestia.delete_mailbox("bilgeworld.com", "test")
    assert exc.value.translated["id"] == "hestia_subprocess_timeout"


def test_write_invalidates_read_cache(monkeypatch, tmp_path):
    fake, _ = _fake_run_factory(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(hestia, "TRIGGER_FILE", tmp_path / "trigger")
    hestia._cache[("list_mailboxes", "bilgeworld.com")] = (9999999999.0, ["stale"])
    hestia.delete_mailbox("bilgeworld.com", "test")
    assert ("list_mailboxes", "bilgeworld.com") not in hestia._cache
