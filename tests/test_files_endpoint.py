"""API tests for PART 10: POST /files/{operation}.

Delegates to Core.execute_tool -> fs.{operation}, so authorization (sandbox
policy + Security Engine) is exercised exactly as it is for a planned step.

Pins its own sandbox root for the duration of these tests (mirroring
test_bootstrap.py's pattern) rather than relying on the process-wide default
policy singleton, which other test modules are free to repoint.
"""

import pytest
from fastapi.testclient import TestClient

from app.fs.policy import FilesystemPolicy, set_default_policy
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def sandboxed_default_policy(tmp_path):
    import app.fs.policy as policy_module

    saved = policy_module._default_policy
    set_default_policy(FilesystemPolicy(allowed_roots=[str(tmp_path)]))
    yield tmp_path
    set_default_policy(saved)


def test_list_operation_runs_through_the_fs_tool(sandboxed_default_policy):
    response = client.post("/files/list", json={"path": str(sandboxed_default_policy)})
    assert response.status_code == 200
    body = response.json()
    assert body["tool_name"] == "fs.list"
    assert body["status"] == "success"


def test_unknown_operation_is_404(sandboxed_default_policy):
    response = client.post("/files/nope", json={"path": str(sandboxed_default_policy)})
    assert response.status_code == 404


def test_disabled_destructive_operation_is_not_executed(sandboxed_default_policy):
    response = client.post(
        "/files/delete", json={"path": str(sandboxed_default_policy / "missing.txt")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tool_name"] == "fs.delete"
    assert body["status"] == "error"


def test_invalid_arguments_come_back_as_a_clean_error_not_a_500(sandboxed_default_policy):
    response = client.post("/files/read", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "error"
