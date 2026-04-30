"""Inbound exim filter editor — spam-blocks, white-blocks, dnsbl conf files.

Pattern: read = direct file read (cheap). Write = atomic tmp+replace under per-file
asyncio.Lock with optimistic mtime check. Validate exim config before declaring
success; restore on validation failure (Task 11). Reload via sudo systemctl (Task 11).
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class FilterFile(Enum):
    SPAM_BLOCKS = "spam_blocks"
    WHITE_BLOCKS = "white_blocks"
    DNSBL = "dnsbl"


SPAM_BLOCKS_PATH = "/etc/exim4/spam-blocks.conf"
WHITE_BLOCKS_PATH = "/etc/exim4/white-blocks.conf"
DNSBL_PATH = "/etc/exim4/dnsbl.conf"

FILE_PATHS = {
    FilterFile.SPAM_BLOCKS: SPAM_BLOCKS_PATH,
    FilterFile.WHITE_BLOCKS: WHITE_BLOCKS_PATH,
    FilterFile.DNSBL: DNSBL_PATH,
}

RE_DNSBL_ZONE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
RE_ADDED_AT = re.compile(r"#\s*added\s+(\d{4}-\d{2}-\d{2})")

_locks = {f: asyncio.Lock() for f in FilterFile}


class ConflictError(Exception):
    """Raised when file mtime changed between read and write."""
    pass


@dataclass
class FilterEntry:
    raw: str
    value: str
    comment: str
    line_no: int
    added_at: Optional[str]


def _parse_line(raw: str, line_no: int) -> Optional[FilterEntry]:
    s = raw.rstrip("\n")
    if not s.strip():
        return None
    if s.lstrip().startswith("#"):
        return None
    if "#" in s:
        value_part, comment = s.split("#", 1)
        comment = comment.strip()
    else:
        value_part, comment = s, ""
    value = value_part.strip()
    if not value:
        return None
    added = None
    m = RE_ADDED_AT.search(comment) if comment else None
    if m:
        added = m.group(1)
    return FilterEntry(raw=raw, value=value, comment=comment, line_no=line_no, added_at=added)


def list_entries(f: FilterFile) -> list[FilterEntry]:
    path = FILE_PATHS[f]
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for i, raw in enumerate(fh, start=1):
            entry = _parse_line(raw, i)
            if entry:
                out.append(entry)
    return out


def validate_value(f: FilterFile, value: str) -> str:
    """Validate; returns normalized value. Raises ValueError on bad input."""
    v = (value or "").strip()
    if not v:
        raise ValueError("değer boş olamaz")
    if f in (FilterFile.SPAM_BLOCKS, FilterFile.WHITE_BLOCKS):
        try:
            net = ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"invalid ip/cidr address: {e}")
        if net.prefixlen == 0:
            raise ValueError("CIDR /0 izin verilmiyor — tüm interneti kapsar")
        # Single host without explicit prefix → return bare IP (no /32 or /128 suffix)
        if "/" not in v:
            return str(net.network_address)
        return str(net)
    if f == FilterFile.DNSBL:
        # Must NOT be an IP
        try:
            ipaddress.ip_address(v)
        except ValueError:
            pass  # not an IP — good
        else:
            raise ValueError("DNSBL alanına hostname girilmeli (IP değil)")
        if not RE_DNSBL_ZONE.match(v):
            raise ValueError("geçersiz DNSBL zone hostname")
        return v
    raise ValueError(f"unknown filter file: {f}")


def _atomic_write_lines(path: str, lines: list, original_mtime) -> None:
    """Atomic write with optimistic mtime lock. original_mtime=None skips check."""
    if original_mtime is not None:
        try:
            current_mtime = os.stat(path).st_mtime
        except FileNotFoundError:
            current_mtime = original_mtime
        if abs(current_mtime - original_mtime) > 0.001:
            raise ConflictError("file changed externally; refresh and retry")
    tmp = f"{path}.tmp.{os.getpid()}.{int(datetime.now().timestamp()*1000)}"
    Path(tmp).write_text("".join(lines))
    os.replace(tmp, path)


def _force_restore(path: str, lines: list) -> None:
    """Restore file content without lock/validation/reload (used by Task 11 on validate fail)."""
    tmp = f"{path}.tmp.restore.{os.getpid()}"
    Path(tmp).write_text("".join(lines))
    os.replace(tmp, path)


def _format_added_comment(comment: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    if comment:
        return f"# {comment} (added {today})"
    return f"# added {today}"


async def add_entry(f: FilterFile, value: str, comment: str = "", *, by: str) -> FilterEntry:
    norm = validate_value(f, value)
    path = FILE_PATHS[f]
    async with _locks[f]:
        try:
            with open(path) as fh:
                original = fh.readlines()
            original_mtime = os.stat(path).st_mtime
        except FileNotFoundError:
            original = []
            original_mtime = None
        for i, line in enumerate(original, start=1):
            entry = _parse_line(line, i)
            if entry and entry.value.lower() == norm.lower():
                raise ValueError(f"duplicate entry on line {i}")
        comment_str = _format_added_comment(comment)
        new_line = f"{norm}   {comment_str}\n"
        new_lines = original + [new_line]
        _atomic_write_lines(path, new_lines, original_mtime)
        return FilterEntry(
            raw=new_line,
            value=norm,
            comment=comment,
            line_no=len(new_lines),
            added_at=datetime.now().strftime("%Y-%m-%d"),
        )


async def remove_entry(f: FilterFile, line_no: int, *, by: str) -> str:
    path = FILE_PATHS[f]
    async with _locks[f]:
        try:
            with open(path) as fh:
                original = fh.readlines()
            original_mtime = os.stat(path).st_mtime
        except FileNotFoundError:
            raise ValueError("file not found")
        if line_no < 1 or line_no > len(original):
            raise ValueError(f"line {line_no} out of range (file has {len(original)} lines)")
        target_raw = original[line_no - 1]
        entry = _parse_line(target_raw, line_no)
        if entry is None:
            raise ValueError(f"line {line_no} is comment or blank — cannot remove via UI")
        new_lines = original[:line_no - 1] + original[line_no:]
        _atomic_write_lines(path, new_lines, original_mtime)
        return entry.value



def validate_exim_config() -> tuple[bool, str]:
    """Run `sudo exim4 -bV` to validate config. Returns (ok, stderr-or-stdout)."""
    try:
        r = subprocess.run(["sudo", "/usr/sbin/exim4", "-bV"],
                           capture_output=True, text=True, timeout=15)
        return (r.returncode == 0, (r.stderr or r.stdout or "").strip())
    except subprocess.TimeoutExpired:
        return (False, "exim4 -bV timed out (15s)")
    except FileNotFoundError as e:
        return (False, f"sudo or exim4 not found: {e}")


def reload_exim() -> tuple[bool, str]:
    """Reload exim daemon via sudo systemctl. Returns (ok, stderr)."""
    try:
        r = subprocess.run(["sudo", "/bin/systemctl", "reload", "exim4"],
                           capture_output=True, text=True, timeout=20)
        return (r.returncode == 0, (r.stderr or r.stdout or "").strip())
    except subprocess.TimeoutExpired:
        return (False, "exim reload timed out (20s)")
    except FileNotFoundError as e:
        return (False, f"sudo or systemctl not found: {e}")


async def add_entry_with_reload(f: FilterFile, value: str, comment: str = "", *, by: str) -> dict:
    """add_entry → exim4 -bV → reload. Returns {entry, reload_warning?}.

    On validation fail: file is rolled back to pre-add state via _force_restore.
    On reload fail: file stays, reload_warning surfaces in response.
    """
    path = FILE_PATHS[f]
    try:
        with open(path) as fh:
            original_lines = fh.readlines()
    except FileNotFoundError:
        original_lines = []

    entry = await add_entry(f, value, comment, by=by)
    ok, stderr = validate_exim_config()
    if not ok:
        _force_restore(path, original_lines)
        raise ValueError(f"config validation failed: {stderr}")
    reload_ok, reload_msg = reload_exim()
    out = {"entry": entry}
    if not reload_ok:
        out["reload_warning"] = f"exim reload failed: {reload_msg}"
    return out


async def remove_entry_with_reload(f: FilterFile, line_no: int, *, by: str) -> dict:
    """remove_entry → exim4 -bV → reload. Returns {removed_value, reload_warning?}."""
    path = FILE_PATHS[f]
    try:
        with open(path) as fh:
            original_lines = fh.readlines()
    except FileNotFoundError:
        original_lines = []

    removed = await remove_entry(f, line_no, by=by)
    ok, stderr = validate_exim_config()
    if not ok:
        _force_restore(path, original_lines)
        raise ValueError(f"config validation failed: {stderr}")
    reload_ok, reload_msg = reload_exim()
    out = {"removed_value": removed}
    if not reload_ok:
        out["reload_warning"] = f"exim reload failed: {reload_msg}"
    return out
