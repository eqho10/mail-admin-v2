import os
os.environ.setdefault('DEBUG_TEST_ENDPOINTS', '1')

from pathlib import Path

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
