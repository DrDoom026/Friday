"""PART 10: API-layer authentication.

Distinct from device trust (Tailscale) and tool authorization (Security
Engine) - this only decides whether a caller may reach FIRDAY's API at all.
Open by default (``FIRDAY_API_KEYS`` unset) so existing behavior/tests are
unaffected; enforced only once an operator configures keys.
"""

import dataclasses

from fastapi.testclient import TestClient

import app.api_auth as api_auth
from app.config import settings
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def _with_keys(*keys):
    return dataclasses.replace(settings, api_keys=tuple(keys))


def test_protected_endpoints_are_open_when_no_keys_are_configured():
    assert settings.api_keys == ()
    assert client.get("/tools").status_code == 200
    assert client.post("/request", json={"input": "a"}).status_code == 200


def test_health_never_requires_a_key():
    assert client.get("/health").status_code == 200


def test_protected_endpoint_rejects_missing_key_once_configured(monkeypatch):
    monkeypatch.setattr(api_auth, "settings", _with_keys("secret-key"))
    response = client.get("/tools")
    assert response.status_code == 401


def test_protected_endpoint_rejects_wrong_key_once_configured(monkeypatch):
    monkeypatch.setattr(api_auth, "settings", _with_keys("secret-key"))
    response = client.get("/tools", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_protected_endpoint_accepts_correct_key_once_configured(monkeypatch):
    monkeypatch.setattr(api_auth, "settings", _with_keys("secret-key"))
    response = client.get("/tools", headers={"X-API-Key": "secret-key"})
    assert response.status_code == 200


def test_health_still_open_once_keys_are_configured(monkeypatch):
    monkeypatch.setattr(api_auth, "settings", _with_keys("secret-key"))
    assert client.get("/health").status_code == 200
