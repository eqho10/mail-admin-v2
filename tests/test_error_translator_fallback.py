import json
import pytest
from pathlib import Path

import services.error_translator as et


@pytest.fixture(autouse=True)
def reset_cache():
    """Her test sonrası cache temizle ki dosya değişikliği etkili olsun."""
    et._dictionary_cache = None
    et._compiled_patterns = []
    yield
    et._dictionary_cache = None
    et._compiled_patterns = []


def test_fallback_uses_json_template():
    """Match yoksa JSON'daki fallback.body_template kullanılır."""
    result = et.translate("xyz çok ekzotik bir hata")
    assert result["id"] == "unknown"
    assert result["title"] == "Bilinmeyen hata"
    # body_template '{raw}' içeriyor — raw mesaj substitute edilmeli
    assert "xyz çok ekzotik bir hata" in result["body"]


def test_missing_dict_falls_back_to_defensive(tmp_path, monkeypatch):
    """Dosya yoksa _DEFENSIVE_DICT yüklenir, exception fırlamaz (debt 7)."""
    fake_path = tmp_path / "missing.json"
    monkeypatch.setattr(et, "_DICT_PATH", fake_path)
    et._dictionary_cache = None
    result = et.translate("herhangi bir hata")
    assert result["id"] == "unknown"
    # Defensive dict body_template "Sözlük yüklenemedi" diyor
    assert "yüklenemedi" in result["body"].lower()


def test_malformed_json_falls_back_to_defensive(tmp_path, monkeypatch):
    """JSON bozuksa _DEFENSIVE_DICT yüklenir (debt 7)."""
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json")
    monkeypatch.setattr(et, "_DICT_PATH", bad)
    et._dictionary_cache = None
    result = et.translate("smtp 421")
    assert result["id"] == "unknown"
    assert "yüklenemedi" in result["body"].lower()


def test_dict_missing_fallback_key_falls_back_to_defensive(tmp_path, monkeypatch):
    """JSON valid ama 'fallback' key yoksa _DEFENSIVE_DICT yüklenir (debt 7)."""
    p = tmp_path / "no_fallback.json"
    p.write_text('{"version": 1, "entries": []}')
    monkeypatch.setattr(et, "_DICT_PATH", p)
    et._dictionary_cache = None
    result = et.translate("herhangi bir hata")
    assert result["id"] == "unknown"
    assert "yüklenemedi" in result["body"].lower()


def test_dict_with_null_entries_falls_back_to_defensive(tmp_path, monkeypatch):
    """JSON'da entries: null gibi şekil hatası → _DEFENSIVE_DICT (Fix 2 coverage)."""
    p = tmp_path / "null_entries.json"
    p.write_text('{"version": 1, "fallback": {"id":"x","title":"y","body_template":"z","actions":[],"severity":"warning"}, "entries": null}')
    monkeypatch.setattr(et, "_DICT_PATH", p)
    et._dictionary_cache = None
    # Should not crash; defensive fallback returns
    result = et.translate("herhangi")
    assert result["id"] == "unknown"  # _DEFENSIVE_DICT fallback id
    assert "yüklenemedi" in result["body"].lower()  # _DEFENSIVE_DICT body_template
