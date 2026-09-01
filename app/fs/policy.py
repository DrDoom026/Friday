"""The filesystem path policy: what FIRDAY is allowed to touch, and how much.

Every filesystem tool routes each path through :meth:`FilesystemPolicy.resolve`
before it does anything. The rules, in order:

1. The path must be a plausible absolute path (no NUL bytes, no absurd length).
2. It must not contain a ``..`` component - traversal is refused outright
   rather than normalized away, so the audit log records the attempt.
3. It is canonicalized with ``Path.resolve()``, which collapses ``.``, ``..``
   and every symlink along the way. A symlink pointing out of a root therefore
   resolves out of the root, and step 4 rejects it.
4. The canonical path must sit inside one of the allowed roots.
5. The canonical path must not sit inside a protected system location, and no
   component may be a known credential store - this holds even when a root was
   misconfigured to contain one.

Nothing here consults the caller's identity: this is the fixed sandbox, not the
permission engine (Part 7).
"""

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.fs.errors import (
    FileTooLargeError,
    PathNotAllowedError,
    PathTraversalError,
    SystemPathError,
)

MAX_PATH_LENGTH = 4096

#: Absolute locations FIRDAY must never operate on, even inside an allowed root.
DEFAULT_SYSTEM_PATHS: tuple[str, ...] = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/srv",
    "/sys",
    "/usr",
    "/var",
    "/opt",
    "/boot/efi",
)

#: Path components that hold credentials. Denied wherever they appear.
DEFAULT_DENIED_NAMES: frozenset[str] = frozenset(
    {".ssh", ".gnupg", ".aws", ".kube", ".docker", ".password-store"}
)


@dataclass(frozen=True)
class FilesystemLimits:
    """Size and breadth caps applied to filesystem operations."""

    max_read_bytes: int = 5 * 1024 * 1024
    max_write_bytes: int = 5 * 1024 * 1024
    max_copy_bytes: int = 50 * 1024 * 1024
    max_list_entries: int = 1000
    max_search_results: int = 500
    max_search_depth: int = 12


class FilesystemPolicy:
    """The fixed sandbox every filesystem tool resolves its paths against."""

    def __init__(
        self,
        allowed_roots: "list[str | os.PathLike[str]] | tuple[str | os.PathLike[str], ...]",
        *,
        limits: FilesystemLimits | None = None,
        system_paths: "tuple[str, ...] | None" = None,
        denied_names: "frozenset[str] | None" = None,
    ) -> None:
        if not allowed_roots:
            raise ValueError("a filesystem policy needs at least one allowed root")

        self.limits = limits or FilesystemLimits()
        self._system_paths = tuple(
            Path(p) for p in (DEFAULT_SYSTEM_PATHS if system_paths is None else system_paths)
        )
        self._denied_names = DEFAULT_DENIED_NAMES if denied_names is None else denied_names

        roots: list[Path] = []
        for raw in allowed_roots:
            root = Path(raw).expanduser().resolve()
            protected = self._protected_ancestor(root)
            if protected is not None:
                raise ValueError(
                    f"allowed root {str(root)!r} is inside protected system path {str(protected)!r}"
                )
            if root not in roots:
                roots.append(root)
        self._roots = tuple(roots)

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    @property
    def primary_root(self) -> Path:
        return self._roots[0]

    def describe(self) -> dict:
        """A serializable summary of the sandbox, safe to expose to a caller."""
        return {
            "allowed_roots": [str(r) for r in self._roots],
            "protected_system_paths": [str(p) for p in self._system_paths],
            "denied_path_components": sorted(self._denied_names),
            "limits": {
                "max_read_bytes": self.limits.max_read_bytes,
                "max_write_bytes": self.limits.max_write_bytes,
                "max_copy_bytes": self.limits.max_copy_bytes,
                "max_list_entries": self.limits.max_list_entries,
                "max_search_results": self.limits.max_search_results,
                "max_search_depth": self.limits.max_search_depth,
            },
        }

    # --- path resolution ---------------------------------------------------

    def resolve(self, raw: str) -> Path:
        """Canonicalize ``raw`` and confirm it is inside the sandbox.

        Returns the canonical path. Raises a
        :class:`~app.fs.errors.PathNotAllowedError` subclass otherwise. The
        path need not exist: a destination for a write or a mkdir is resolved
        the same way, with its existing parents' symlinks followed.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise PathNotAllowedError(str(raw), "path must be a non-empty string")
        if "\x00" in raw:
            raise PathNotAllowedError(raw, "path contains a NUL byte")
        if len(raw) > MAX_PATH_LENGTH:
            raise PathNotAllowedError(raw[:64] + "...", "path is too long")

        candidate = Path(raw)
        if not candidate.is_absolute():
            raise PathNotAllowedError(raw, "path must be absolute")
        if ".." in PurePosixPath(raw).parts:
            raise PathTraversalError(raw)

        resolved = candidate.resolve()

        # Re-check after canonicalization: a symlink may have pointed anywhere.
        protected = self._protected_ancestor(resolved)
        if protected is not None:
            raise SystemPathError(raw, str(protected))
        denied = self._denied_component(resolved)
        if denied is not None:
            raise SystemPathError(raw, denied)
        if not self._inside_a_root(resolved):
            reason = (
                "resolves to " + str(resolved) + ", outside the allowed roots"
                if str(resolved) != str(candidate)
                else "resolves outside the allowed roots"
            )
            raise PathNotAllowedError(raw, reason)

        return resolved

    def is_allowed(self, raw: str) -> bool:
        """``True`` if :meth:`resolve` would accept ``raw``."""
        try:
            self.resolve(raw)
        except PathNotAllowedError:
            return False
        return True

    def try_resolve(self, raw: str) -> Path | None:
        """Resolve for reporting purposes, returning ``None`` when refused."""
        try:
            return self.resolve(raw)
        except Exception:  # noqa: BLE001 - reporting must never raise
            return None

    # --- size limits -------------------------------------------------------

    def enforce_size(self, path: Path | str, size: int, limit: int) -> None:
        """Raise :class:`FileTooLargeError` when ``size`` exceeds ``limit``."""
        if size > limit:
            raise FileTooLargeError(str(path), size, limit)

    # --- internals ---------------------------------------------------------

    def _inside_a_root(self, resolved: Path) -> bool:
        return any(resolved == root or resolved.is_relative_to(root) for root in self._roots)

    def _protected_ancestor(self, resolved: Path) -> Path | None:
        for protected in self._system_paths:
            if resolved == protected or resolved.is_relative_to(protected):
                return protected
        return None

    def _denied_component(self, resolved: Path) -> str | None:
        for part in resolved.parts:
            if part in self._denied_names:
                return part
        return None


_default_policy: FilesystemPolicy | None = None


def get_default_policy() -> FilesystemPolicy:
    """The process-wide policy built from settings, created on first use."""
    global _default_policy
    if _default_policy is None:
        from app.config import settings

        _default_policy = FilesystemPolicy(
            allowed_roots=list(settings.fs_allowed_roots),
            limits=FilesystemLimits(
                max_read_bytes=settings.fs_max_read_bytes,
                max_write_bytes=settings.fs_max_write_bytes,
                max_copy_bytes=settings.fs_max_copy_bytes,
                max_list_entries=settings.fs_max_list_entries,
                max_search_results=settings.fs_max_search_results,
                max_search_depth=settings.fs_max_search_depth,
            ),
        )
    return _default_policy


def set_default_policy(policy: FilesystemPolicy | None) -> None:
    """Replace the process-wide policy. Intended for tests and startup wiring."""
    global _default_policy
    _default_policy = policy
