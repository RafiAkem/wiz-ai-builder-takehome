import os

import pytest
from fastapi.testclient import TestClient



@pytest.fixture
def client(tmp_path):
    os.environ["SOURCE_LLM_MODE"] = "mock"
    from app.main import app
    os.environ["LEADS_DATABASE_PATH"] = str(tmp_path / "test.db")
    with TestClient(app) as test_client:
        yield test_client
    os.environ.pop("LEADS_DATABASE_PATH", None)
