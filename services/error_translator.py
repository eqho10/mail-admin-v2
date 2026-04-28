"""Error translator — raw exception/log mesajlarını insan dilinde sözlüğe çevirir."""
import json
import re
from pathlib import Path
from typing import Any

_DICT_PATH = Path(__file__).resolve().parent.parent / "config" / "error_dictionary.json"
_dictionary_cache: dict | None = None
_compiled_patterns: list[tuple[str, re.Pattern, str, dict]] = []


def load_dictionary(force: bool = False) -> dict:
    """Sözlüğü diskten yükle ve regex patternleri pre-compile et."""
    global _dictionary_cache, _compiled_patterns
    if _dictionary_cache is None or force:
        with _DICT_PATH.open("r", encoding="utf-8") as f:
            _dictionary_cache = json.load(f)
        _compiled_patterns = []
        for entry in _dictionary_cache.get("entries", []):
            mt = entry.get("match_type", "substring")
            pattern = entry["match"]
            if mt == "regex":
                compiled = re.compile(pattern, re.IGNORECASE)
            else:
                compiled = re.compile(re.escape(pattern), re.IGNORECASE)
            _compiled_patterns.append((entry["id"], compiled, mt, entry))
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
