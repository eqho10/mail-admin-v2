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
