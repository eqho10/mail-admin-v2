import pytest
from services.error_translator import translate, load_dictionary

def test_exim_no_input_file_translates():
    raw = 'Failed to open input file for 1wHfJ8-0000000C1d7-0nsc-H: No such file or directory'
    result = translate(raw)
    assert result['id'] == 'exim_no_input_file'
    assert result['title'] == 'Mesaj zaten teslim edildi'
    assert result['severity'] == 'info'
    assert len(result['actions']) >= 1

def test_brevo_421_translates():
    raw = '421 Service not available, closing transmission channel'
    result = translate(raw)
    assert result['id'] == 'brevo_421'
    assert result['severity'] == 'warning'

def test_unknown_error_falls_back():
    raw = 'Some completely unknown error occurred xyz'
    result = translate(raw)
    assert result['id'] == 'unknown'
    assert result['severity'] == 'warning'
    assert raw in result['body']
    assert any(a['endpoint'].endswith('/error-dictionary/add') for a in result['actions'])

def test_substring_match_works():
    raw = 'auth: Permission denied (publickey)'
    result = translate(raw)
    assert result['id'] == 'ssh_publickey'

def test_regex_match_works():
    raw = 'host mail.example.com SERVFAIL when querying'
    result = translate(raw)
    assert result['id'] == 'dns_servfail'

def test_empty_dictionary_does_not_crash():
    from services import error_translator
    original_dict = error_translator._dictionary_cache
    original_compiled = error_translator._compiled_patterns
    error_translator._dictionary_cache = {'version': 1, 'entries': []}
    error_translator._compiled_patterns = []
    try:
        result = translate('any error')
        assert result['id'] == 'unknown'
    finally:
        error_translator._dictionary_cache = original_dict
        error_translator._compiled_patterns = original_compiled


def test_translate_hestia_user_exists():
    res = translate("Error: mail account john exists")
    assert res["id"] == "hestia_user_exists"
    assert "zaten var" in res["body"]
    assert res["severity"] == "warning"


def test_translate_hestia_invalid_quota():
    res = translate("Error: invalid quota value")
    assert res["id"] == "hestia_invalid_quota"


def test_translate_hestia_password_policy():
    res = translate("Error: invalid password format")
    assert res["id"] == "hestia_password_policy"


def test_translate_hestia_alias_exists():
    res = translate("Error: alias info@x.com exists")
    assert res["id"] == "hestia_alias_exists"


def test_translate_hestia_alias_not_found():
    res = translate("Error: alias info@x.com not exist")
    assert res["id"] == "hestia_alias_not_found"


def test_translate_hestia_user_not_found():
    res = translate("Error: mail account ghost not exist")
    assert res["id"] == "hestia_user_not_found"


def test_translate_hestia_domain_not_found():
    res = translate("Error: mail domain example.com not exist")
    assert res["id"] == "hestia_domain_not_found"


def test_translate_hestia_subprocess_timeout():
    res = translate("HestiaCP CLI timeout after 10s")
    assert res["id"] == "hestia_subprocess_timeout"


def test_translate_hestia_api_unauthorized():
    res = translate("HestiaCP API returned 401 Unauthorized")
    assert res["id"] == "hestia_api_unauthorized"


def test_translate_hestia_api_unreachable():
    res = translate("HestiaCP API connection refused")
    assert res["id"] == "hestia_api_unreachable"
