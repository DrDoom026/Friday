"""Tests for the startup sandbox check.

The sandbox is built lazily, so before this existed a missing or misconfigured
allowed root surfaced as an opaque failure on the first ``fs.*`` call. These
pin the boot-time behaviour: create what is missing, refuse to start otherwise,
and say why in a message a human can act on.
"""

import dataclasses
import os

import pytest

from app.fs.bootstrap import SandboxConfigurationError, ensure_sandbox_ready
from app.fs.policy import FilesystemPolicy, get_default_policy, set_default_policy


@pytest.fixture(autouse=True)
def restore_default_policy():
    """Never let a test leak its policy into the process-wide default."""
    import app.fs.policy as policy_module

    saved = policy_module._default_policy
    yield
    set_default_policy(saved)


# --- creating what is missing ---------------------------------------------


def test_missing_root_is_created_at_startup(tmp_path):
    root = tmp_path / "firday" / "workspace"
    assert not root.exists()

    policy = ensure_sandbox_ready([str(root)], install=False)

    assert root.is_dir()
    assert policy.roots == (root.resolve(),)


def test_existing_root_is_left_alone(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "keep.txt").write_text("existing data")

    ensure_sandbox_ready([str(root)], install=False)

    assert (root / "keep.txt").read_text() == "existing data"


def test_every_configured_root_is_prepared(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"

    ensure_sandbox_ready([str(first), str(second)], install=False)

    assert first.is_dir() and second.is_dir()


def test_creation_can_be_refused_instead(tmp_path):
    root = tmp_path / "absent"

    with pytest.raises(SandboxConfigurationError) as exc:
        ensure_sandbox_ready([str(root)], create_missing=False)

    assert "does not exist" in str(exc.value)
    assert not root.exists()


# --- refusing to boot ------------------------------------------------------


def test_no_configured_roots_is_refused():
    with pytest.raises(SandboxConfigurationError) as exc:
        ensure_sandbox_ready(())

    assert "no allowed roots" in str(exc.value)


def test_protected_system_root_is_refused_with_the_docker_hint():
    with pytest.raises(SandboxConfigurationError) as exc:
        ensure_sandbox_ready(["/root/firday/workspace"])

    message = str(exc.value)
    assert "protected system path" in message
    assert "/data/workspace" in message, "the message must name the working alternative"


def test_relative_root_is_refused(tmp_path):
    with pytest.raises(SandboxConfigurationError) as exc:
        ensure_sandbox_ready(["firday/workspace"])

    assert "absolute" in str(exc.value)


def test_root_that_is_a_file_is_refused(tmp_path):
    impostor = tmp_path / "workspace"
    impostor.write_text("not a directory")

    with pytest.raises(SandboxConfigurationError) as exc:
        ensure_sandbox_ready([str(impostor)])

    assert "not a directory" in str(exc.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_root_is_refused(tmp_path):
    root = tmp_path / "readonly"
    root.mkdir(mode=0o500)
    try:
        with pytest.raises(SandboxConfigurationError) as exc:
            ensure_sandbox_ready([str(root)])
        assert "not writable" in str(exc.value)
    finally:
        root.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_uncreatable_root_is_refused_with_the_reason(tmp_path):
    parent = tmp_path / "locked"
    parent.mkdir(mode=0o500)
    try:
        with pytest.raises(SandboxConfigurationError) as exc:
            ensure_sandbox_ready([str(parent / "workspace")])
        assert "could not be created" in str(exc.value)
        assert "PermissionError" in str(exc.value)
    finally:
        parent.chmod(0o700)


def test_error_message_names_the_variable_and_a_fix(tmp_path):
    with pytest.raises(SandboxConfigurationError) as exc:
        ensure_sandbox_ready(["/root/firday/workspace"])

    message = str(exc.value)
    assert "FS_ALLOWED_ROOTS" in message
    assert "fix:" in message


# --- installing the validated policy ---------------------------------------


def test_validated_policy_becomes_the_process_default(tmp_path):
    root = tmp_path / "workspace"

    policy = ensure_sandbox_ready([str(root)])

    assert get_default_policy() is policy


def test_install_can_be_skipped(tmp_path):
    set_default_policy(FilesystemPolicy(allowed_roots=[str(tmp_path)]))
    before = get_default_policy()

    ensure_sandbox_ready([str(tmp_path / "other")], install=False)

    assert get_default_policy() is before


# --- the app refuses to start ----------------------------------------------


def test_app_lifespan_creates_the_root_and_serves(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    root = tmp_path / "workspace"
    monkeypatch.setattr(
        main, "settings", dataclasses.replace(main.settings, fs_allowed_roots=(str(root),))
    )

    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200

    assert root.is_dir(), "startup must have created the missing sandbox root"


def test_app_refuses_to_start_on_an_unusable_sandbox(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setattr(
        main,
        "settings",
        dataclasses.replace(main.settings, fs_allowed_roots=("/root/firday/workspace",)),
    )

    with pytest.raises(SandboxConfigurationError):
        with TestClient(main.app):
            pass
