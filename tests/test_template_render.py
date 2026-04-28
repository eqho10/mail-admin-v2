def test_root_renders_base_template(client):
    response = client.get('/login')
    assert response.status_code == 200
    assert 'Mail Admin' in response.text
    assert '<!-- base.html -->' in response.text
