from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="english-lab-tests-"))
os.environ["ENGLISH_LAB_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["MEDIA_STORAGE_ROOT"] = str(TEST_DATA_DIR / "media")
os.environ["MEDIA_IMPORT_ROOT"] = str(TEST_DATA_DIR / "import")
os.environ["COOKIE_SECURE"] = "false"
os.environ["EXPOSE_API_DOCS"] = "false"

from app import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client

@pytest.fixture()
def authenticated_client(client: TestClient) -> tuple[TestClient, str]:
    response = client.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse-battery"})
    if response.status_code == 409:
        response = client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"})
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]
