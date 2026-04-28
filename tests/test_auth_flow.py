import json

from app import OTP_STORE as OTP_PATH


def test_login_redirects_to_verify_on_correct_credentials(client, monkeypatch):
    import app as app_module
    async def fake_send_mail(*a, **kw): return None
    monkeypatch.setattr(app_module, 'send_mail', fake_send_mail)

    response = client.post('/login', data={
        'email': 'ekrem.mutlu@hotmail.com.tr',
        'password': 'VkCngJrPL9Bspcmdg5rBIfRS',
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/verify'
    assert OTP_PATH.exists()
    data = json.loads(OTP_PATH.read_text())
    assert 'code' in data
    assert len(data['code']) == 6


def test_login_rejects_wrong_password(client, monkeypatch):
    import app as app_module
    async def fake_send_mail(*a, **kw): return None
    monkeypatch.setattr(app_module, 'send_mail', fake_send_mail)

    response = client.post('/login', data={
        'email': 'ekrem.mutlu@hotmail.com.tr',
        'password': 'wrong',
    }, follow_redirects=False)
    assert response.status_code == 401


def test_verify_with_correct_code_sets_cookie(client, monkeypatch):
    import app as app_module
    async def fake_send_mail(*a, **kw): return None
    monkeypatch.setattr(app_module, 'send_mail', fake_send_mail)

    # Login first
    client.post('/login', data={
        'email': 'ekrem.mutlu@hotmail.com.tr',
        'password': 'VkCngJrPL9Bspcmdg5rBIfRS',
    }, follow_redirects=False)
    data = json.loads(OTP_PATH.read_text())
    code = data['code']

    response = client.post('/verify', data={'code': code}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/'
    cookie = response.cookies.get('ma_sess')
    assert cookie is not None
    assert len(cookie) > 30

    # Cookie security attrs (M2): httponly, secure, samesite=strict
    set_cookie = response.headers.get('set-cookie', '').lower()
    assert 'httponly' in set_cookie
    assert 'secure' in set_cookie
    assert 'samesite=strict' in set_cookie
