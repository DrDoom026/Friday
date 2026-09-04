"""PART 11: dashboard route and static asset delivery."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_dashboard_root_serves_index_html():
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "FRIDAY" in response.text


def test_dashboard_stylesheet_is_served():
    response = client.get("/dashboard/dashboard.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_dashboard_scripts_are_served():
    for asset in ("particles.js", "api.js", "dashboard.js"):
        response = client.get(f"/dashboard/{asset}")
        assert response.status_code == 200, asset
