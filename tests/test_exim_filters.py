import pytest
import os
from services import exim_filters as ef


def test_list_entries_reads_dnsbl_initial(tmp_exim_dir):
    out = ef.list_entries(ef.FilterFile.DNSBL)
    assert len(out) == 1
    assert out[0].value == "bl.spamcop.net"
    assert out[0].comment == ""


def test_list_entries_skips_comment_only_lines(tmp_exim_dir):
    (tmp_exim_dir / "spam-blocks.conf").write_text(
        "# header comment\n"
        "1.2.3.4\n"
        "# another comment\n"
        "10.0.0.0/24\n"
    )
    out = ef.list_entries(ef.FilterFile.SPAM_BLOCKS)
    assert len(out) == 2
    assert out[0].value == "1.2.3.4"
    assert out[1].value == "10.0.0.0/24"


def test_list_entries_parses_inline_comment(tmp_exim_dir):
    (tmp_exim_dir / "spam-blocks.conf").write_text(
        "1.2.3.4   # spammer 2026-04-29\n"
    )
    out = ef.list_entries(ef.FilterFile.SPAM_BLOCKS)
    assert len(out) == 1
    assert out[0].value == "1.2.3.4"
    assert "spammer" in out[0].comment


def test_validate_value_ipv4_ok():
    assert ef.validate_value(ef.FilterFile.SPAM_BLOCKS, "1.2.3.4") == "1.2.3.4"


def test_validate_value_ipv4_cidr_ok():
    assert ef.validate_value(ef.FilterFile.SPAM_BLOCKS, "10.0.0.0/24") == "10.0.0.0/24"


def test_validate_value_ipv6_ok():
    out = ef.validate_value(ef.FilterFile.SPAM_BLOCKS, "2001:db8::/32")
    assert out.startswith("2001:")


def test_validate_value_rejects_invalid_ip():
    with pytest.raises(ValueError) as e:
        ef.validate_value(ef.FilterFile.SPAM_BLOCKS, "1.2.3.999")
    assert "ip" in str(e.value).lower() or "cidr" in str(e.value).lower()


def test_validate_value_rejects_zero_prefix():
    with pytest.raises(ValueError):
        ef.validate_value(ef.FilterFile.SPAM_BLOCKS, "0.0.0.0/0")


def test_validate_value_dnsbl_zone_ok():
    assert ef.validate_value(ef.FilterFile.DNSBL, "zen.spamhaus.org") == "zen.spamhaus.org"


def test_validate_value_dnsbl_rejects_ip():
    with pytest.raises(ValueError):
        ef.validate_value(ef.FilterFile.DNSBL, "1.2.3.4")


def test_validate_value_dnsbl_rejects_invalid_hostname():
    with pytest.raises(ValueError):
        ef.validate_value(ef.FilterFile.DNSBL, "not a hostname")


def test_validate_value_rejects_empty():
    with pytest.raises(ValueError):
        ef.validate_value(ef.FilterFile.SPAM_BLOCKS, "")
    with pytest.raises(ValueError):
        ef.validate_value(ef.FilterFile.DNSBL, "")


@pytest.mark.asyncio
async def test_add_entry_appends_to_file(tmp_exim_dir):
    entry = await ef.add_entry(ef.FilterFile.SPAM_BLOCKS, "1.2.3.4", "spammer 2026-04-29", by="ekrem")
    assert entry.value == "1.2.3.4"
    out = ef.list_entries(ef.FilterFile.SPAM_BLOCKS)
    assert any(e.value == "1.2.3.4" for e in out)


@pytest.mark.asyncio
async def test_add_entry_normalizes_cidr(tmp_exim_dir):
    entry = await ef.add_entry(ef.FilterFile.SPAM_BLOCKS, "10.0.0.5/24", "", by="ekrem")
    assert entry.value == "10.0.0.0/24"


@pytest.mark.asyncio
async def test_add_entry_rejects_duplicate(tmp_exim_dir):
    await ef.add_entry(ef.FilterFile.SPAM_BLOCKS, "1.2.3.4", "", by="x")
    with pytest.raises(ValueError) as e:
        await ef.add_entry(ef.FilterFile.SPAM_BLOCKS, "1.2.3.4", "", by="x")
    assert "duplicate" in str(e.value).lower() or "zaten" in str(e.value)


@pytest.mark.asyncio
async def test_add_entry_preserves_comments(tmp_exim_dir):
    (tmp_exim_dir / "spam-blocks.conf").write_text("# top header\n# more comments\n")
    await ef.add_entry(ef.FilterFile.SPAM_BLOCKS, "1.2.3.4", "test", by="x")
    content = (tmp_exim_dir / "spam-blocks.conf").read_text()
    assert "# top header" in content
    assert "# more comments" in content
    assert "1.2.3.4" in content


@pytest.mark.asyncio
async def test_remove_entry_by_line_no(tmp_exim_dir):
    (tmp_exim_dir / "spam-blocks.conf").write_text(
        "# header\n"
        "1.2.3.4\n"
        "5.6.7.8\n"
    )
    removed = await ef.remove_entry(ef.FilterFile.SPAM_BLOCKS, line_no=2, by="x")
    assert removed == "1.2.3.4"
    out = ef.list_entries(ef.FilterFile.SPAM_BLOCKS)
    assert len(out) == 1
    assert out[0].value == "5.6.7.8"


@pytest.mark.asyncio
async def test_remove_entry_invalid_line_no_raises(tmp_exim_dir):
    with pytest.raises(ValueError) as e:
        await ef.remove_entry(ef.FilterFile.SPAM_BLOCKS, line_no=999, by="x")
    assert "line" in str(e.value).lower()


@pytest.mark.asyncio
async def test_add_entry_mtime_lock_conflict(tmp_exim_dir, monkeypatch):
    """Simulate external file change between read and write -> ConflictError."""
    await ef.add_entry(ef.FilterFile.SPAM_BLOCKS, "1.2.3.4", "", by="x")
    real_stat = os.stat
    captured_mtime_box = {}

    def fake_stat(path, *args, **kwargs):
        s = real_stat(path, *args, **kwargs)
        if str(path).endswith("spam-blocks.conf"):
            if "captured" not in captured_mtime_box:
                captured_mtime_box["captured"] = True
                return s
            else:
                from os import stat_result
                lst = list(s)
                lst[8] = lst[8] + 100  # st_mtime
                return stat_result(lst)
        return s

    monkeypatch.setattr("os.stat", fake_stat)
    with pytest.raises(Exception) as e:
        await ef.add_entry(ef.FilterFile.SPAM_BLOCKS, "9.9.9.9", "", by="x")
    assert "conflict" in str(e.value).lower() or "changed" in str(e.value).lower()
