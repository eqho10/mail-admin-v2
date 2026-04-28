import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    from app import app
    return TestClient(app)
