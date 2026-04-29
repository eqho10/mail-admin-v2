from pathlib import Path
from services.exim import parse_line, aggregate_messages

FIXTURE = Path(__file__).parent / "fixtures" / "exim_mainlog_sample.txt"


def test_parse_line_received():
    line = "2026-04-25 09:12:01 1rXyZ-0001Ab-7K <= ekrem@bilgeworld.com H=(localhost) [127.0.0.1] P=esmtpa S=2143 id=20260425091201.4567@bilgeworld.com"
    result = parse_line(line)
    assert result is not None
    assert result["msgid"] == "1rXyZ-0001Ab-7K"
    assert result["sym"] == "<="
    assert "ekrem@bilgeworld.com" in result["addr"]


def test_parse_line_delivered():
    line = '2026-04-25 09:12:02 1rXyZ-0001Ab-7K => user@gmail.com R=dnslookup T=remote_smtp H=gmail-smtp-in.l.google.com [142.251.16.27] X=TLS1.3 C="250 2.0.0 OK"'
    result = parse_line(line)
    assert result is not None
    assert result["msgid"] == "1rXyZ-0001Ab-7K"
    assert result["sym"] == "=>"


def test_parse_line_frozen():
    line = "2026-04-25 09:14:34 1rXza-0002Cd-8L Frozen (delivery error message)"
    # "Frozen" is not a recognised sym — parse_line should return None
    # (the fixture line is a Frozen notice, not matching LOG_LINE sym group)
    result = parse_line(line)
    # Frozen lines don't match the LOG_LINE pattern — None is correct
    assert result is None


def test_parse_line_bounced():
    line = "2026-04-25 09:14:34 1rXza-0002Cd-8L ** test@invalid.tld R=dnslookup: SERVFAIL"
    result = parse_line(line)
    assert result is not None
    assert result["msgid"] == "1rXza-0002Cd-8L"
    assert result["sym"] == "**"


def test_aggregate_messages_groups_by_msgid():
    lines = FIXTURE.read_text().strip().split("\n")
    msgs = aggregate_messages(lines)
    msgids = {m["msgid"] for m in msgs}
    assert "1rXyZ-0001Ab-7K" in msgids
    assert "1rXza-0002Cd-8L" in msgids
    assert "1rXzb-0003Ef-9M" in msgids
    assert len(msgids) == 3


def test_aggregate_messages_status_completed():
    lines = FIXTURE.read_text().strip().split("\n")
    msgs = aggregate_messages(lines)
    delivered = next(m for m in msgs if m["msgid"] == "1rXyZ-0001Ab-7K")
    # => line sets status to "delivered"; Completed line doesn't override since not "pending"
    assert delivered["status"] == "delivered"


def test_aggregate_messages_status_bounced():
    lines = FIXTURE.read_text().strip().split("\n")
    msgs = aggregate_messages(lines)
    bounced = next(m for m in msgs if m["msgid"] == "1rXza-0002Cd-8L")
    assert bounced["status"] == "bounced"
