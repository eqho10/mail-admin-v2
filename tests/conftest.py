import os
os.environ.setdefault('DEBUG_TEST_ENDPOINTS', '1')
os.environ.setdefault('ADMIN_PASS', 'VkCngJrPL9Bspcmdg5rBIfRS')
os.environ.setdefault('BREVO_API_KEY', 'test-fake-not-real')

from pathlib import Path
from unittest.mock import MagicMock

import dns.exception
import dns.resolver
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app import app
    # raise_server_exceptions=False: global exception handler tests can
    # assert on the 500 JSON response. Side effect: every test sees
    # translated 500s instead of raised exceptions; check status_code,
    # do NOT use pytest.raises against routes.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    """Reset auth rate-limit + OTP store before/after every test so login
    flows don't leak attempts across tests (TestClient always uses the
    same `testclient` IP)."""
    rl = Path('/root/mail-admin-v2/data/rate_limit.json')
    otp = Path('/root/mail-admin-v2/data/otp_store.json')
    if rl.exists():
        rl.write_text('{}')
    otp.unlink(missing_ok=True)
    yield
    if rl.exists():
        rl.write_text('{}')
    otp.unlink(missing_ok=True)


@pytest.fixture
def authed_client(monkeypatch):
    """Login + verify ile session cookie almış https TestClient.
    Hem smoke hem router endpoint testleri burayı kullanır.
    raise_server_exceptions=False: 500 error handler testleri response.json'a assert edebilsin."""
    import os
    import json
    import app as app_module
    from app import app, OTP_STORE
    async def fake_send_mail(*a, **kw): return None
    monkeypatch.setattr(app_module, "send_mail", fake_send_mail)
    client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
    client.post("/login", data={
        "email": os.getenv("ADMIN_EMAIL", "ekrem.mutlu@hotmail.com.tr"),
        "password": os.getenv("ADMIN_PASS", "VkCngJrPL9Bspcmdg5rBIfRS"),
    }, follow_redirects=False)
    code = json.loads(OTP_STORE.read_text())["code"]
    client.post("/verify", data={"code": code}, follow_redirects=False)
    # Faz 4a: CSRF middleware requires X-CSRF-Token on session-bound POSTs.
    # Attach a default header derived from the session cookie so existing
    # tests that POST to authenticated endpoints don't need per-test wiring.
    sess_cookie = client.cookies.get("ma_sess", "")
    # httpx stores cookie values with surrounding quotes when the value
    # contains chars (e.g. '@') outside the cookie-octet RFC 6265 set.
    # Starlette strips these on the server side, so we must too — otherwise
    # the token we compute won't match the server's recomputed token.
    if len(sess_cookie) >= 2 and sess_cookie[0] == '"' and sess_cookie[-1] == '"':
        sess_cookie = sess_cookie[1:-1]
    if sess_cookie:
        from services.csrf import issue_token
        client.headers.update({"X-CSRF-Token": issue_token(sess_cookie)})
    return client


# ---------------------------------------------------------------------------
# DNSBL test support (Faz 4b Task 2): mock dns.asyncresolver.Resolver.
# Imports (MagicMock, dns.exception, dns.resolver) hoisted to top of file.
# ---------------------------------------------------------------------------


class MockAnswer:
    """Mimic dns.resolver.Answer iterable of A records."""
    def __init__(self, return_codes):
        self._items = [MagicMock(__str__=lambda s, rc=rc: rc) for rc in return_codes]

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, idx):
        return self._items[idx]


class MockResolver:
    """Configurable mock for dns.asyncresolver.Resolver."""
    def __init__(self, qname_to_result):
        # qname_to_result: dict[str, 'listed:127.0.0.4' | 'clean' | 'timeout' | 'error:msg']
        self._map = qname_to_result
        self.lifetime = 5.0

    async def resolve(self, qname, rdtype='A'):
        result = self._map.get(qname, 'clean')
        if result.startswith('listed:'):
            rc = result.split(':', 1)[1]
            return MockAnswer([rc])
        elif result == 'clean':
            raise dns.resolver.NXDOMAIN()
        elif result == 'timeout':
            raise dns.exception.Timeout()
        elif result.startswith('error:'):
            raise Exception(result.split(':', 1)[1])
        else:
            raise dns.resolver.NXDOMAIN()


@pytest.fixture
def mock_dns_resolver(monkeypatch):
    """Returns a callable: configure(qname_to_result_dict) → patches dns.asyncresolver.Resolver."""
    def configure(qname_to_result):
        monkeypatch.setattr('dns.asyncresolver.Resolver', lambda: MockResolver(qname_to_result))
    return configure


@pytest.fixture
def mock_subprocess_run(monkeypatch):
    """Patch subprocess.run with scripted (rc, stdout, stderr) per command tuple."""
    calls = []
    scripted = {}

    def configure(cmd_to_result):
        scripted.update(cmd_to_result)

    def fake_run(args, capture_output=False, text=False, timeout=None, **kwargs):
        calls.append(list(args))
        key = tuple(args[:3]) if len(args) >= 3 else tuple(args)
        result = scripted.get(key, scripted.get(tuple(args), (0, '', '')))
        rc, out, err = result
        class R:
            returncode = rc
            stdout = out
            stderr = err
        return R()

    monkeypatch.setattr('subprocess.run', fake_run)
    return type('F', (), {'configure': staticmethod(configure), 'calls': calls})
