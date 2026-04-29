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
