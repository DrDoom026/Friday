"""PART 11: POST /docker/{operation} - the dashboard's containers panel.

Mirrors PART 10's ``/files/{operation}`` test shape: delegates to
``Core.execute_tool`` -> ``docker.{operation}``, so Security Engine
authorization is exercised exactly as it is for a planned step.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_unknown_docker_operation_is_404():
    response = client.post("/docker/nope", json={})
    assert response.status_code == 404


def test_docker_containers_runs_through_the_docker_tool():
    response = client.post("/docker/containers", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["tool_name"] == "docker.containers"
    # Docker socket is not available in the test environment, so this
    # exercises the tool path honestly (error), not a mocked success.
    assert body["status"] in ("success", "error")


def test_docker_restart_is_blocked_not_executed():
    """``docker.restart`` is PRIVILEGED, so the Security Engine blocks it
    (REQUIRE_CONFIRMATION - no confirmation channel exists yet). This endpoint
    reports that block, it never lifts it (never a security bypass, never a
    fake auto-approval)."""
    response = client.post("/docker/restart", json={"container": "anything"})
    assert response.status_code == 200
    body = response.json()
    assert body["tool_name"] == "docker.restart"
    assert body["status"] == "error"
    assert body["output"]["authorized"] is False
    assert "confirmation" in body["output"]["blocked_until"].lower()


def test_only_the_docker_namespace_is_reachable():
    """The scoping guard: this endpoint must never resolve a non-docker tool."""
    response = client.post("/docker/../fs.read", json={})
    # FastAPI/Starlette normalizes the path; either a 404 (no such docker
    # operation) or a routing 404 is acceptable - what must never happen is a
    # 200 with a filesystem tool result.
    assert response.status_code == 404 or response.json().get("tool_name") != "fs.read"
