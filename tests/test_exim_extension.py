import pytest
from services import exim


def test_release_msg_invokes_exim_Mt(mock_subprocess_run):
    mock_subprocess_run.configure({("exim", "-Mt", "1wEk9N-0001AB-Xy"): (0, "Message released", "")})
    rc, out, err = exim.exim_release_msg("1wEk9N-0001AB-Xy")
    assert rc == 0
    assert ["exim", "-Mt", "1wEk9N-0001AB-Xy"] in mock_subprocess_run.calls


def test_release_msg_rejects_invalid_msgid():
    rc, out, err = exim.exim_release_msg("../etc/passwd")
    assert rc != 0
    assert "bad msgid" in err.lower() or "invalid" in err.lower()


def test_view_msg_returns_headers_and_body(mock_subprocess_run):
    mock_subprocess_run.configure({
        ("exim", "-Mvh", "1wEk9N-0001AB-Xy"): (0, "From: a@b.com\nTo: c@d.com\nSubject: hi\n", ""),
        ("exim", "-Mvb", "1wEk9N-0001AB-Xy"): (0, "Hello world\n", ""),
    })
    res = exim.exim_view_msg("1wEk9N-0001AB-Xy")
    assert "headers" in res and "body" in res
    assert "From: a@b.com" in res["headers"]
    assert "Hello world" in res["body"]
    assert res["truncated"] is False


def test_view_msg_truncates_body_over_1mb(mock_subprocess_run):
    big = "x" * (1024 * 1024 + 100)
    mock_subprocess_run.configure({
        ("exim", "-Mvh", "1wEk9N"): (0, "From: a\n", ""),
        ("exim", "-Mvb", "1wEk9N"): (0, big, ""),
    })
    res = exim.exim_view_msg("1wEk9N")
    assert res["truncated"] is True
    assert len(res["body"]) <= 1024 * 1024


def test_view_msg_returns_404_dict_when_not_found(mock_subprocess_run):
    mock_subprocess_run.configure({
        ("exim", "-Mvh", "missing"): (1, "", "spool: message missing not found"),
    })
    res = exim.exim_view_msg("missing")
    assert res.get("not_found") is True


def test_view_msg_rejects_invalid_msgid():
    res = exim.exim_view_msg("../etc/passwd")
    assert "error" in res or res.get("invalid") is True
