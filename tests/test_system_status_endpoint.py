"""PART 11: GET /system/status - shape, and no secret material ever leaves it."""

import dataclasses

from fastapi.testclient import TestClient

import app.main as app_main
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_system_status_returns_expected_shape():
    response = client.get("/system/status")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "uptime_seconds",
        "cpu_percent",
        "memory_percent",
        "vault_percent",
        "tailscale_connected",
        "planner_mode",
        "omniroute_configured",
        "gmail_configured",
    ):
        assert key in body


def test_system_status_never_exposes_configured_secrets(monkeypatch):
    secret_key = "sk-super-secret-omniroute-key"
    secret_gmail = "gmail-refresh-token-xyz"
    patched = dataclasses.replace(
        app_main.settings,
        omniroute_api_key=secret_key,
        gmail_client_id="client-id",
        gmail_client_secret="gmail-client-secret",
        gmail_refresh_token=secret_gmail,
    )
    monkeypatch.setattr(app_main, "settings", patched)

    response = client.get("/system/status")
    assert response.status_code == 200
    raw = response.text
    assert secret_key not in raw
    assert secret_gmail not in raw
    assert "gmail-client-secret" not in raw
    # only booleans should describe provider/adapter configuration
    assert response.json()["omniroute_configured"] is True
    assert response.json()["gmail_configured"] is True
