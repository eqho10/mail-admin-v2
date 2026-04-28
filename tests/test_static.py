def test_static_css_served(client):
    response = client.get('/static/css/app.css')
    assert response.status_code == 200
    assert '--bg-0' in response.text
    assert '@font-face' in response.text

def test_static_theme_js_served(client):
    response = client.get('/static/js/theme.js')
    assert response.status_code == 200
    assert 'toggleTheme' in response.text

def test_static_lucide_served(client):
    response = client.get('/static/icons/lucide.min.js')
    assert response.status_code == 200
    assert len(response.content) > 50000  # bundle ~80-150 KB
