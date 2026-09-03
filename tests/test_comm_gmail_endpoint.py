"""PART 13 core integration: POST /comm/gmail/poll.

Proves the concrete flow Gmail -> GmailAdapter -> normalized FirdayRequest ->
Core.handle (planner/Security Engine/tools) -> FirdayResponse, without a
polling loop/scheduler (PART 15) and without any real Gmail credentials.
"""

import httpx
from fastapi.testclient import TestClient

import app.main as app_main
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_poll_endpoint_returns_503_when_gmail_is_not_configured():
    """Default test environment has no GMAIL_* env vars set."""
    response = client.post("/comm/gmail/poll")
    assert response.status_code == 503
    assert "not configured" in response.json()["error"].lower()


def test_poll_endpoint_runs_each_message_through_core(monkeypatch):
    def fake_fetch_new(self, limit=10):
        from app.comm.models import InboundMessage

        async def _inner():
            return [
                InboundMessage(
                    platform="gmail",
                    external_id="m1",
                    thread_id="t1",
                    sender="alice@example.com",
                    subject="Hi",
                    body="please echo this",
                )
            ]

        return _inner()

    monkeypatch.setattr("app.comm.gmail.adapter.GmailAdapter.fetch_new", fake_fetch_new)

    response = client.post("/comm/gmail/poll")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    # Went through the same mock planner as /request - Core, not a Gmail-side brain.
    assert body[0]["plan"]["planner_name"] == app_main.core.planner.name
