from __future__ import annotations

import json

from english_lab.config import config


def test_health_is_public_and_pages_require_login(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/api/library").status_code == 401
    assert client.get("/audio/not-found.wav").status_code == 401
    assert client.get("/openapi.json").status_code == 404


def test_setup_login_and_csrf(client):
    status = client.get("/api/auth/status").json()
    if status["setup_required"]:
        response = client.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse-battery"})
    else:
        response = client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"})
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/library/rebuild").status_code == 403
    csrf = response.json()["csrf_token"]
    assert client.post("/api/library/rebuild", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_sensitive_settings_are_encrypted_at_rest(authenticated_client):
    client, csrf = authenticated_client
    secret = "pytest-secret-that-must-not-be-plain-text"
    response = client.post(
        "/api/settings",
        headers={"X-CSRF-Token": csrf},
        json={"deepseek_api_key": secret},
    )
    assert response.status_code == 200, response.text
    raw = (config.data_root / "settings.json").read_text(encoding="utf-8")
    stored = json.loads(raw)
    assert secret not in raw
    assert stored["deepseek_api_key"].startswith("enc:v1:")
