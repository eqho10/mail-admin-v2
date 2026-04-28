"""Error translator — raw exception/log mesajlarını insan dilinde sözlüğe çevirir."""
import json
import re
import threading
from pathlib import Path
from typing import Any

_DICT_PATH = Path(__file__).resolve().parent.parent / "config" / "error_dictionary.json"
_dictionary_cache: dict | None = None
_compiled_patterns: list[tuple[str, re.Pattern, str, dict]] = []
_load_lock = threading.Lock()


def load_dictionary(force: bool = False) -> dict:
    """Sözlüğü diskten yükle ve regex patternleri pre-compile et. Threadsafe."""
    global _dictionary_cache, _compiled_patterns
    if _dictionary_cache is not None and not force:
        return _dictionary_cache
    with _load_lock:
        if _dictionary_cache is not None and not force:
            return _dictionary_cache
        with _DICT_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        compiled = []
        for entry in data.get("entries", []):
            mt = entry.get("match_type", "substring")
            pattern = entry["match"]
            if mt == "regex":
                rx = re.compile(pattern, re.IGNORECASE)
            else:
                rx = re.compile(re.escape(pattern), re.IGNORECASE)
            compiled.append((entry["id"], rx, mt, entry))
        _compiled_patterns = compiled
        _dictionary_cache = data
    return _dictionary_cache


def translate(raw_message: str) -> dict[str, Any]:
    """Raw mesajı sözlüğe çevir. Match yoksa fallback dict döner."""
    load_dictionary()
    for entry_id, compiled, mt, entry in _compiled_patterns:
        if compiled.search(raw_message):
            return {
                "id": entry_id,
                "title": entry["title"],
                "body": entry["body"],
                "actions": entry.get("actions", []),
                "severity": entry.get("severity", "warning"),
                "raw": raw_message,
            }
    return {
        "id": "unknown",
        "title": "Bilinmeyen hata",
        "body": raw_message,
        "actions": [{"label": "Sözlüğe ekle", "endpoint": "/api/error-dictionary/add"}],
        "severity": "warning",
        "raw": raw_message,
    }
