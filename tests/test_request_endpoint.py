"""API tests for POST /request."""

from fastapi.testclient import TestClient

import app.main as app_main
from app.core.orchestrator import Core
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_request_returns_200_and_mock_plan():
    response = client.post("/request", json={"input": "what is the weather"})
    assert response.status_code == 200

    body = response.json()
    assert body["output"] == "[mock] Acknowledged: what is the weather"
    assert body["plan"]["planner_name"] == "mock"
    assert body["plan"]["steps"][0]["tool_name"] == "echo"
    assert body["results"][0]["status"] == "not_executed"


def test_request_generates_a_request_id_per_call():
    first = client.post("/request", json={"input": "a"}).json()["request_id"]
    second = client.post("/request", json={"input": "b"}).json()["request_id"]
    assert first and second and first != second


def test_request_id_is_echoed_in_the_response_header():
    response = client.post("/request", json={"input": "a"})
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_request_honours_caller_supplied_correlation_id():
    response = client.post(
        "/request", json={"input": "a"}, headers={"X-Request-ID": "caller-xyz"}
    )
    assert response.json()["request_id"] == "caller-xyz"
    assert response.headers["x-request-id"] == "caller-xyz"


def test_request_accepts_optional_metadata():
    response = client.post("/request", json={"input": "a", "metadata": {"channel": "cli"}})
    assert response.status_code == 200


def test_request_rejects_missing_input():
    assert client.post("/request", json={}).status_code == 422


def test_request_rejects_empty_input():
    assert client.post("/request", json={"input": ""}).status_code == 422


def test_planner_failure_surfaces_as_part_0_error_envelope(monkeypatch):
    class BrokenPlanner:
        name = "broken"

        async def plan(self, request, context):
            raise RuntimeError("planner exploded")

    monkeypatch.setattr(app_main, "core", Core(BrokenPlanner()))

    response = client.post("/request", json={"input": "a"})
    assert response.status_code == 500
    assert response.json()["error"] == "internal_server_error"


def test_health_still_works_alongside_the_new_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
