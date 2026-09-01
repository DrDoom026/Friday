"""Startup validation for the filesystem sandbox.

The sandbox is configured by environment (``FS_ALLOWED_ROOTS``) but only built
on first use, which means a misconfigured or missing root used to surface as an
opaque tool failure on the first ``fs.*`` call, long after boot. This module
moves that discovery to startup: :func:`ensure_sandbox_ready` builds the policy,
creates any missing root, and raises a readable
:class:`SandboxConfigurationError` when it cannot make the sandbox usable.

``app.main`` calls it from the application lifespan, so the process either comes
up with a working sandbox or refuses to come up at all.
"""

import logging
import os
from pathlib import Path

from app.fs.policy import FilesystemLimits, FilesystemPolicy, set_default_policy

logger = logging.getLogger("firday.fs.bootstrap")

#: Pointer used in every remediation hint, so the message is actionable.
_DOCKER_HINT = (
    "In a container ~ expands to /root, which is a protected system path. Set an "
    "absolute non-protected path instead (the bundled docker-compose.yml uses "
    "FS_ALLOWED_ROOTS=/data/workspace with a bind mount)."
)


class SandboxConfigurationError(RuntimeError):
    """The filesystem sandbox cannot be used as configured.

    Raised at startup only. It carries a message meant to be read by a human in
    the boot log, not handled by calling code.
    """


def _fail(problem: str, remedy: str) -> "SandboxConfigurationError":
    return SandboxConfigurationError(
        f"filesystem sandbox is unusable: {problem}\n"
        f"  FS_ALLOWED_ROOTS = {os.getenv('FS_ALLOWED_ROOTS') or '(unset, using the default)'}\n"
        f"  fix: {remedy}"
    )


def ensure_sandbox_ready(
    roots: "tuple[str, ...] | list[str]",
    *,
    limits: FilesystemLimits | None = None,
    create_missing: bool = True,
    install: bool = True,
) -> FilesystemPolicy:
    """Validate the sandbox and return the policy the tools will use.

    Creates any allowed root that does not exist yet. Raises
    :class:`SandboxConfigurationError` - with a message that names the problem
    and the fix - when the configuration is unusable: no roots at all, a root
    inside a protected system path, a root that is not a directory, or a root
    that cannot be created or written to.

    When ``install`` is set the validated policy becomes the process-wide
    default, so no later call rebuilds it and hits the same error.
    """
    if not roots:
        raise _fail(
            "no allowed roots are configured",
            "set FS_ALLOWED_ROOTS to one or more absolute paths, colon-separated.",
        )

    for raw in roots:
        if not os.path.isabs(os.path.expanduser(str(raw))):
            raise _fail(
                f"allowed root {str(raw)!r} is not an absolute path",
                f"use an absolute path. {_DOCKER_HINT}",
            )

    try:
        policy = FilesystemPolicy(allowed_roots=list(roots), limits=limits)
    except ValueError as exc:
        raise _fail(str(exc), f"point FS_ALLOWED_ROOTS somewhere writable. {_DOCKER_HINT}") from exc

    for root in policy.roots:
        _prepare_root(root, create_missing=create_missing)

    if install:
        set_default_policy(policy)

    logger.info(
        "filesystem sandbox ready (roots=%s)", ", ".join(str(r) for r in policy.roots)
    )
    return policy


def _prepare_root(root: Path, *, create_missing: bool) -> None:
    """Make one allowed root exist, be a directory, and be writable."""
    if not root.exists():
        if not create_missing:
            raise _fail(
                f"allowed root {str(root)!r} does not exist",
                f"create it (mkdir -p {root}) or point FS_ALLOWED_ROOTS elsewhere.",
            )
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _fail(
                f"allowed root {str(root)!r} could not be created "
                f"({type(exc).__name__}: {exc.strerror or exc})",
                f"create it manually (mkdir -p {root}) or grant the service write "
                f"access to {root.parent}. {_DOCKER_HINT}",
            ) from exc
        logger.info("created missing allowed root (path=%s)", root)

    if not root.is_dir():
        raise _fail(
            f"allowed root {str(root)!r} exists but is not a directory",
            f"remove or replace {root} with a directory.",
        )

    if not os.access(root, os.W_OK | os.X_OK):
        raise _fail(
            f"allowed root {str(root)!r} is not writable by this process",
            f"grant write access (chown/chmod) to {root}. {_DOCKER_HINT}",
        )
