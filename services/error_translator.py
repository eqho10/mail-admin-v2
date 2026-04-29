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

# Defensive default: dosya yok/bozuk → empty entries + minimal fallback
_DEFENSIVE_DICT = {
    "version": 0,
    "fallback": {
        "id": "unknown",
        "title": "Bilinmeyen hata",
        "body_template": "Sözlük yüklenemedi. Ham mesaj: {raw}",
        "actions": [],
        "severity": "warning",
    },
    "entries": [],
}


def load_dictionary(force: bool = False) -> dict:
    """Sözlüğü diskten yükle ve regex patternleri pre-compile et. Threadsafe.
    Dosya yok / bozuk / şekil hatalı → _DEFENSIVE_DICT'e düşer (debt 7)."""
    global _dictionary_cache, _compiled_patterns
    if _dictionary_cache is not None and not force:
        return _dictionary_cache
    with _load_lock:
        if _dictionary_cache is not None and not force:
            return _dictionary_cache
        try:
            with _DICT_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if "fallback" not in data or "entries" not in data:
                data = _DEFENSIVE_DICT
            compiled = []
            for entry in data.get("entries", []):
                mt = entry.get("match_type", "substring")
                pattern = entry["match"]
                if mt == "regex":
                    rx = re.compile(pattern, re.IGNORECASE)
                else:
                    rx = re.compile(re.escape(pattern), re.IGNORECASE)
                compiled.append((entry["id"], rx, mt, entry))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError, KeyError, re.error):
            data = _DEFENSIVE_DICT
            compiled = []
        _compiled_patterns = compiled
        _dictionary_cache = data
    return _dictionary_cache


def translate(raw_message: str) -> dict[str, Any]:
    """Raw mesajı sözlüğe çevir. Match yoksa JSON'daki fallback dict döner (debt 1)."""
    d = load_dictionary()
    for entry_id, compiled, mt, entry in _compiled_patterns:
        if compiled.search(raw_message):
            return {
                "id": entry_id,
                "title": entry["title"],
                "body": entry["body"],
                "actions": list(entry.get("actions", [])),
                "severity": entry.get("severity", "warning"),
                "raw": raw_message,
            }
    fb = d.get("fallback", _DEFENSIVE_DICT["fallback"])
    return {
        "id": fb["id"],
        "title": fb["title"],
        "body": fb["body_template"].format(raw=raw_message),
        "actions": list(fb.get("actions", [])),
        "severity": fb.get("severity", "warning"),
        "raw": raw_message,
    }
