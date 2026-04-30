import pytest
from services import quarantine
from services import exim as exim_svc
from services import brevo_suppression


def test_get_frozen_messages_filters_only_frozen(monkeypatch):
    fake_queue_list = [
        {"age": "1h", "size": "2.3K", "msgid": "AAA", "from": "a@b.com", "to": ["c@d.com"], "frozen": False},
        {"age": "27h", "size": "1.8K", "msgid": "BBB", "from": "x@y.com", "to": ["z@w.com"], "frozen": True},
        {"age": "5h", "size": "5.1K", "msgid": "CCC", "from": "p@q.com", "to": ["r@s.com"], "frozen": True},
    ]
    monkeypatch.setattr(exim_svc, "exim_queue_list", lambda: fake_queue_list)
    frozen = quarantine.get_frozen_messages()
    assert len(frozen) == 2
    assert {m["msgid"] for m in frozen} == {"BBB", "CCC"}


def test_get_frozen_messages_empty_when_queue_empty(monkeypatch):
    monkeypatch.setattr(exim_svc, "exim_queue_list", lambda: [])
    assert quarantine.get_frozen_messages() == []


def test_parse_rejected_lines_smtp_pattern():
    ln = '2026-04-29 14:00:00 H=evil.example [1.2.3.4] F=<spam@x.com> rejected SMTP from <user@us.com>: blocked by spamhaus'
    out = quarantine.parse_rejected_lines([ln])
    assert len(out) == 1
    g = out[0]
    assert g["ip"] == "1.2.3.4"
    assert g["sender"] == "spam@x.com"
    assert "spamhaus" in g["reason"].lower()


def test_parse_rejected_lines_rcpt_pattern():
    ln = '2026-04-29 14:00:00 H=mailer.bad [5.6.7.8] F=<x@y> rejected RCPT <victim@us.com>: relay not permitted'
    out = quarantine.parse_rejected_lines([ln])
    assert len(out) == 1
    assert out[0]["ip"] == "5.6.7.8"
    assert out[0]["recipient"] == "victim@us.com"


def test_parse_rejected_lines_data_pattern():
    ln = '2026-04-29 14:00:00 H=src [9.9.9.9] F=<a@b> rejected after DATA: message rejected as spam'
    out = quarantine.parse_rejected_lines([ln])
    assert len(out) == 1
    assert "spam" in out[0]["reason"].lower()


def test_parse_rejected_lines_groups_dupes():
    lns = [
        '2026-04-29 14:00:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: blocked',
        '2026-04-29 14:01:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: blocked',
        '2026-04-29 14:02:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: blocked',
    ]
    out = quarantine.parse_rejected_lines(lns)
    assert len(out) == 1
    assert out[0]["count"] == 3
    assert out[0]["last_seen"] == "2026-04-29 14:02:00"
    assert out[0]["first_seen"] == "2026-04-29 14:00:00"


def test_parse_rejected_lines_groups_separate_ips():
    lns = [
        '2026-04-29 14:00:00 H=a [1.1.1.1] F=<x@y> rejected SMTP: blocked',
        '2026-04-29 14:01:00 H=b [2.2.2.2] F=<x@y> rejected SMTP: blocked',
    ]
    out = quarantine.parse_rejected_lines(lns)
    assert len(out) == 2


def test_parse_rejected_lines_normalizes_reason_with_numbers():
    lns = [
        '2026-04-29 14:00:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: rate limit 5 attempts',
        '2026-04-29 14:01:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: rate limit 12 attempts',
    ]
    out = quarantine.parse_rejected_lines(lns)
    assert len(out) == 1
    assert out[0]["count"] == 2


def test_parse_rejected_lines_skips_non_reject_lines():
    lns = [
        '2026-04-29 14:00:00 H=h [1.2.3.4] => user@x.com',
        '2026-04-29 14:00:00 H=h [1.2.3.4] <= user@x.com',
        '2026-04-29 14:00:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: blocked',
    ]
    out = quarantine.parse_rejected_lines(lns)
    assert len(out) == 1


def test_parse_rejected_lines_handles_empty_input():
    assert quarantine.parse_rejected_lines([]) == []


def test_get_brevo_blocked_count_returns_zero_on_error(monkeypatch):
    async def fail():
        raise RuntimeError("brevo down")
    monkeypatch.setattr(brevo_suppression, "list_blocked", fail)
    import asyncio
    assert asyncio.run(quarantine.get_brevo_blocked_count()) == 0


def test_get_rejected_groups_reads_mainlog(monkeypatch):
    fake_lines = [
        '2026-04-29 14:00:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: blocked',
    ]
    monkeypatch.setattr(exim_svc, "read_tail", lambda path, n: fake_lines)
    out = quarantine.get_rejected_groups()
    assert len(out) == 1


async def test_get_summary_combines_three_sources(monkeypatch):
    monkeypatch.setattr(exim_svc, "exim_queue_list", lambda: [
        {"msgid": "A", "frozen": True, "from": "", "to": []},
        {"msgid": "B", "frozen": False, "from": "", "to": []},
    ])
    monkeypatch.setattr(exim_svc, "read_tail", lambda p, n: ['2026-04-29 14:00:00 H=h [1.2.3.4] F=<x@y> rejected SMTP: bad'])

    async def fake_brevo(*args, **kwargs):
        return [{"email": f"x{i}@y.com"} for i in range(7)]
    monkeypatch.setattr(brevo_suppression, "list_blocked", fake_brevo)

    s = await quarantine.get_summary()
    assert s == {"frozen_count": 1, "rejected_count": 1, "rejected_groups": 1, "brevo_blocked_count": 7}
