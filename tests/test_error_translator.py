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


@pytest.mark.parametrize('raw, expected_id, expected_severity', [
    ('Error: mail account john already exists',                       'hestia_user_exists',         'warning'),
    ('Error: invalid quota format :: abc',                            'hestia_invalid_quota',       'error'),
    ('Error: invalid password format :: foo',                         'hestia_password_policy',     'warning'),
    ('Error: mail alias info already exists',                         'hestia_alias_exists',        'warning'),
    ("Error: alias info doesn't exist",                               'hestia_alias_not_found',     'warning'),
    ("Error: bilgeworld.com account ghost doesn't exist",             'hestia_user_not_found',      'warning'),
    ("Error: mail domain example.com doesn't exist",                  'hestia_domain_not_found',    'error'),
    ('Error: Mail domain example.com exists',                         'hestia_domain_exists',       'warning'),
    ('HestiaCP CLI timeout after 10s',                                'hestia_subprocess_timeout',  'error'),
    ('HestiaCP API returned 401 Unauthorized',                        'hestia_api_unauthorized',    'error'),
    ('HestiaCP API connection refused',                               'hestia_api_unreachable',     'error'),
])
def test_hestia_error_translates(raw, expected_id, expected_severity):
    result = translate(raw)
    assert result['id'] == expected_id
    assert result['severity'] == expected_severity
