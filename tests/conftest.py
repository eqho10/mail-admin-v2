import os
os.environ.setdefault('DEBUG_TEST_ENDPOINTS', '1')

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
