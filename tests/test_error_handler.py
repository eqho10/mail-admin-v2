def test_unhandled_exception_returns_translated_error_json(client):
    response = client.get('/api/_test/raise?raw=Failed%20to%20open%20input%20file%20for%20XXX-H')
    assert response.status_code == 500
    data = response.json()
    assert data['error']['id'] == 'exim_no_input_file'
    assert data['error']['severity'] == 'info'
    assert 'Maildir' in data['error']['body']

def test_unknown_exception_falls_back(client):
    response = client.get('/api/_test/raise?raw=totally%20unknown%20error%20xyz')
    assert response.status_code == 500
    data = response.json()
    assert data['error']['id'] == 'unknown'
