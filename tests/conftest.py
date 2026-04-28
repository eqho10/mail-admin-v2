import os
os.environ.setdefault('DEBUG_TEST_ENDPOINTS', '1')

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app import app
    return TestClient(app, raise_server_exceptions=False)
