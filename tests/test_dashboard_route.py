"""PART 11: dashboard route and static asset delivery.

The dashboard is now built from `frontend/` (Vite + React + Three.js) into
`app/static/dashboard/`; FastAPI serves whatever that build produced. Asset
filenames are content-hashed by the bundler, so this file discovers them
from the built `index.html` rather than hardcoding names.
"""

import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def _asset_paths():
    """Extract the /dashboard/assets/... paths referenced by the built index.html."""
    html = client.get("/dashboard/").text
    return re.findall(r'(?:src|href)="(/dashboard/assets/[^"]+)"', html)


def test_dashboard_root_serves_index_html():
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "FRIDAY" in response.text


def test_dashboard_build_references_at_least_one_script_and_one_stylesheet():
    assets = _asset_paths()
    assert any(a.endswith(".js") for a in assets), assets
    assert any(a.endswith(".css") for a in assets), assets


def test_dashboard_built_assets_are_served():
    for asset in _asset_paths():
        response = client.get(asset)
        assert response.status_code == 200, asset


def test_dashboard_data_endpoints_are_open_when_no_api_keys_are_configured():
    """Regression: the Pi's real deployment ships with FIRDAY_API_KEYS unset.

    Every endpoint the dashboard polls on load must succeed with no
    ``X-API-Key`` header in that configuration - the frontend's key gate must
    never be the thing standing between an open backend and a working
    dashboard.
    """
    from app.config import settings

    assert settings.api_keys == ()
    for path in ("/system/status", "/tools", "/automation/tasks"):
        response = client.get(path)
        assert response.status_code == 200, path
