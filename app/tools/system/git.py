"""Git tools: status, branches, clone and pull.

``git.status`` and ``git.branches`` observe. ``git.clone`` creates something
that was not there, which is additive in the same sense ``fs.mkdir`` and
``fs.copy`` are, so it runs - and like ``fs.copy`` it refuses to overwrite,
declining any destination that already exists rather than merging into it.

``git.pull`` does not run. It rewrites a working tree that someone else may be
mid-edit in, it can fast-forward a branch out from under uncommitted work, and
it fetches whatever the remote currently says. "It is only one more write
operation" is exactly the reasoning the skipped Part 3 exists to refuse, so it
is a deny-stub like the rest, and stays one until PART 7.

Every path a git tool touches is resolved through the Part 4
:class:`~app.fs.policy.FilesystemPolicy` first. There is no second sandbox.
"""

import asyncio
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.errors import ToolExecutionError
from app.core.registry import register_tool
from app.system.command import reject_option_like, run_command_async
from app.system.errors import CommandNotAvailableError, InvalidTargetError, SystemToolError
from app.core.tools import SideEffect, ToolPermissions
from app.tools.system.base import (
    DeniedSystemTool,
    SystemTool,
    control_permissions,
    read_permissions,
)

DOMAIN = "git"

GIT_READ = read_permissions("git.read", filesystem=True)
GIT_WRITE = control_permissions("git.write", network=True, filesystem=True)

#: git.clone actually runs (like fs.mkdir/fs.copy) rather than being a denied
#: stub, so unlike GIT_WRITE it does not set requires_confirmation - it is a
#: plain write operation, allowed by default, that enforces its own
#: no-overwrite rule instead of gating on the Security Engine.
GIT_CLONE = ToolPermissions(
    side_effect=SideEffect.WRITE,
    scopes=("system.write", "git.write"),
    network_access=True,
    filesystem_access=True,
)

GIT_BINARY = "git"

#: URL schemes ``git.clone`` will accept. ``ext::`` is absent on purpose: it
#: hands git an arbitrary command to run as the transport, which would turn
#: clone into the general shell tool this project deliberately does not have.
ALLOWED_URL_SCHEMES = ("https", "http", "ssh", "git", "file")

#: Matches an scp-style remote such as ``git@github.com:owner/repo.git``.
_SCP_LIKE = ("@", ":")

#: Passed to every git invocation. Blocks the ``ext`` transport at the git
#: level too, so a URL form this module failed to anticipate still cannot run a
#: command, and stops git from blocking on a credential prompt.
GIT_HARDENING = ("-c", "protocol.ext.allow=never")
GIT_ENVIRONMENT = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "LC_ALL": "C",
}


def require_git() -> None:
    if shutil.which(GIT_BINARY) is None:
        raise CommandNotAvailableError(GIT_BINARY, "git tools need the git binary")


def _timeout() -> float:
    from app.config import settings

    return settings.system_command_timeout_seconds


class GitTool(SystemTool):
    """A system tool that runs git against a repository inside the sandbox."""

    def audited_targets(self, payload) -> list[str]:
        return [str(getattr(payload, "path", ""))]

    async def repository(self, raw: str) -> Path:
        """Resolve a path through the sandbox and confirm it is a git work tree."""
        require_git()
        target = self.policy.resolve(raw)
        if not target.is_dir():
            raise ToolExecutionError(self.name, f"{raw!r} is not a directory")

        result = await self.git(target, ["rev-parse", "--show-toplevel"])
        if not result.ok:
            raise ToolExecutionError(self.name, f"{raw!r} is not a git repository")

        top = Path(result.stdout.strip())
        # The work tree root may sit above the requested path; it has to be
        # inside the sandbox too, or the tool would report on a repo it is not
        # allowed to see.
        return self.policy.resolve(str(top))

    async def git(self, repository: Path, arguments: list[str], *, timeout: float | None = None):
        return await run_command_async(
            [GIT_BINARY, *GIT_HARDENING, "-C", str(repository), *arguments],
            timeout=timeout if timeout is not None else _timeout(),
            env=dict(GIT_ENVIRONMENT),
        )


# --- git.status ------------------------------------------------------------


class GitChange(BaseModel):
    path: str
    status: str = Field(..., description="Two-letter XY code: index state, then worktree state.")
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False
    unmerged: bool = False
    renamed_from: str | None = None


class GitStatusInput(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute path inside the repository.")
    max_changes: int | None = Field(None, ge=1, description="Cap on reported changed files.")


class GitStatusOutput(BaseModel):
    repository: str
    branch: str = ""
    detached: bool = False
    commit: str = ""
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    clean: bool = True
    changes: list[GitChange] = Field(default_factory=list)
    change_count: int = 0
    truncated: bool = False


@register_tool
class GitStatusTool(GitTool):
    name = "git.status"
    domain = DOMAIN
    operation = "status"
    description = "Report a git repository's branch, upstream divergence and working tree state."
    version = "1.0.0"
    permissions = GIT_READ
    input_model = GitStatusInput
    output_model = GitStatusOutput

    async def operate(
        self, payload: GitStatusInput, context: ToolExecutionContext
    ) -> GitStatusOutput:
        repository = await self.repository(payload.path)
        result = await self.git(
            repository, ["status", "--porcelain=v2", "--branch", "--untracked-files=all"]
        )
        result.require_ok()

        output = GitStatusOutput(repository=str(repository))
        changes: list[GitChange] = []

        for line in result.stdout.splitlines():
            if line.startswith("# "):
                self._header(line[2:], output)
            elif change := _parse_entry(line):
                changes.append(change)

        limit = payload.max_changes or len(changes)
        output.changes = changes[:limit]
        output.change_count = len(changes)
        output.truncated = len(changes) > limit
        output.clean = not changes
        return output

    @staticmethod
    def _header(line: str, output: GitStatusOutput) -> None:
        key, _, value = line.partition(" ")
        if key == "branch.oid":
            output.commit = "" if value == "(initial)" else value
        elif key == "branch.head":
            output.detached = value == "(detached)"
            output.branch = "" if output.detached else value
        elif key == "branch.upstream":
            output.upstream = value
        elif key == "branch.ab":
            ahead, _, behind = value.partition(" ")
            output.ahead = int(ahead.lstrip("+") or 0)
            output.behind = int(behind.lstrip("-") or 0)


def _parse_entry(line: str) -> GitChange | None:
    """Parse one ``--porcelain=v2`` entry line."""
    kind, _, rest = line.partition(" ")

    if kind == "?":
        return GitChange(path=rest, status="??", untracked=True)
    if kind == "!":
        return None
    if kind == "u":
        fields = rest.split(" ", 9)
        return GitChange(path=fields[-1], status=fields[0], unmerged=True) if fields else None
    if kind not in ("1", "2"):
        return None

    fields = rest.split(" ", 7 if kind == "1" else 8)
    if len(fields) < (8 if kind == "1" else 9):
        return None

    code = fields[0]
    path_field = fields[-1]
    renamed_from = None
    if kind == "2" and "\t" in path_field:
        path_field, _, renamed_from = path_field.partition("\t")

    return GitChange(
        path=path_field,
        status=code,
        staged=code[0] not in (".", "?"),
        unstaged=code[1] not in (".", "?"),
        renamed_from=renamed_from,
    )


# --- git.branches ----------------------------------------------------------

_BRANCH_FORMAT = "%(refname:short)%09%(objectname:short)%09%(upstream:short)%09%(committerdate:iso-strict)%09%(contents:subject)"


class GitBranch(BaseModel):
    name: str
    commit: str = ""
    upstream: str | None = None
    last_commit_at: str = ""
    subject: str = ""
    remote: bool = False
    current: bool = False


class GitBranchesInput(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute path inside the repository.")
    include_remote: bool = Field(True, description="Include remote-tracking branches.")
    max_branches: int | None = Field(None, ge=1, description="Cap on returned branches.")


class GitBranchesOutput(BaseModel):
    repository: str
    current_branch: str = ""
    detached: bool = False
    branches: list[GitBranch] = Field(default_factory=list)
    branch_count: int = 0
    truncated: bool = False


@register_tool
class GitBranchesTool(GitTool):
    name = "git.branches"
    domain = DOMAIN
    operation = "branches"
    description = "List a git repository's local and remote-tracking branches."
    version = "1.0.0"
    permissions = GIT_READ
    input_model = GitBranchesInput
    output_model = GitBranchesOutput

    async def operate(
        self, payload: GitBranchesInput, context: ToolExecutionContext
    ) -> GitBranchesOutput:
        repository = await self.repository(payload.path)

        head = await self.git(repository, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        current = head.stdout.strip() if head.ok else ""

        listed = await self.git(
            repository, ["for-each-ref", f"--format={_BRANCH_FORMAT}", "refs/heads"]
        )
        listed.require_ok()

        remote_names: set[str] = set()
        if payload.include_remote:
            remotes = await self.git(
                repository, ["for-each-ref", f"--format={_BRANCH_FORMAT}", "refs/remotes"]
            )
            remotes.require_ok()
            listed_output = listed.stdout + remotes.stdout
            remote_names = {
                line.split("\t", 1)[0] for line in remotes.stdout.splitlines() if line.strip()
            }
        else:
            listed_output = listed.stdout

        branches = []
        for line in listed_output.splitlines():
            fields = line.split("\t")
            if not fields or not fields[0]:
                continue
            fields += [""] * (5 - len(fields))
            name = fields[0]
            branches.append(
                GitBranch(
                    name=name,
                    commit=fields[1],
                    upstream=fields[2] or None,
                    last_commit_at=fields[3],
                    subject=fields[4],
                    remote=name in remote_names,
                    current=name == current,
                )
            )

        limit = payload.max_branches or len(branches)
        return GitBranchesOutput(
            repository=str(repository),
            current_branch=current,
            detached=not current,
            branches=branches[:limit],
            branch_count=len(branches),
            truncated=len(branches) > limit,
        )


# --- git.clone -------------------------------------------------------------


def validate_clone_url(url: str) -> str:
    """Accept only URL forms that cannot make git run a command of its own."""
    reject_option_like("url", url)

    lowered = url.lower()
    if lowered.startswith("ext::"):
        raise InvalidTargetError("url", url, "the 'ext' transport can execute commands")

    scheme, separator, _ = url.partition("://")
    if separator:
        if scheme.lower() not in ALLOWED_URL_SCHEMES:
            raise InvalidTargetError(
                "url", url, f"scheme must be one of {', '.join(ALLOWED_URL_SCHEMES)}"
            )
        return url

    if url.startswith("/"):
        return url
    if all(part in url for part in _SCP_LIKE) and "::" not in url:
        return url

    raise InvalidTargetError("url", url, "must be an absolute path or a supported URL")


class GitCloneInput(BaseModel):
    url: str = Field(..., min_length=1, description="Repository to clone from.")
    destination: str = Field(
        ..., min_length=1, description="Absolute path to clone into. Must not already exist."
    )
    branch: str | None = Field(None, description="Branch or tag to check out.")
    depth: int | None = Field(None, ge=1, description="Create a shallow clone of this depth.")
    create_parents: bool = Field(False, description="Create the destination's parents.")


class GitCloneOutput(BaseModel):
    url: str
    destination: str
    branch: str = ""
    commit: str = ""
    shallow: bool = False
    duration_ms: float = 0.0


@register_tool
class GitCloneTool(GitTool):
    name = "git.clone"
    domain = DOMAIN
    operation = "clone"
    description = (
        "Clone a git repository into a new directory inside an allowed root. "
        "Refuses any destination that already exists."
    )
    version = "1.0.0"
    permissions = GIT_CLONE
    input_model = GitCloneInput
    output_model = GitCloneOutput

    def audited_targets(self, payload: GitCloneInput) -> list[str]:
        return [payload.url, payload.destination]

    async def operate(
        self, payload: GitCloneInput, context: ToolExecutionContext
    ) -> GitCloneOutput:
        from app.config import settings

        require_git()
        url = validate_clone_url(payload.url)
        destination = self.policy.resolve(payload.destination)

        if destination.exists() or destination.is_symlink():
            raise ToolExecutionError(
                self.name,
                f"{payload.destination!r} already exists; clone will not write into it",
            )

        parent = destination.parent
        if not parent.exists():
            if not payload.create_parents:
                raise ToolExecutionError(
                    self.name,
                    f"destination parent {str(parent)!r} does not exist; "
                    "pass create_parents=true to create it",
                )
            self.policy.resolve(str(parent))
            parent.mkdir(parents=True, exist_ok=True)

        arguments = [GIT_BINARY, *GIT_HARDENING, "clone"]
        if payload.branch:
            arguments += ["--branch", reject_option_like("branch", payload.branch)]
        if payload.depth:
            arguments += ["--depth", str(payload.depth)]
        arguments += ["--", url, str(destination)]

        result = await run_command_async(
            arguments,
            timeout=settings.system_git_timeout_seconds,
            env=dict(GIT_ENVIRONMENT),
        )
        if not result.ok:
            # git leaves a partial directory behind on failure; do not.
            await asyncio.to_thread(shutil.rmtree, destination, True)
            raise SystemToolError(f"clone of {url!r} failed: {_last_line(result)}")

        head = await self.git(destination, ["rev-parse", "HEAD"])
        branch = await self.git(destination, ["symbolic-ref", "--quiet", "--short", "HEAD"])

        return GitCloneOutput(
            url=url,
            destination=str(destination),
            branch=branch.stdout.strip() if branch.ok else "",
            commit=head.stdout.strip() if head.ok else "",
            shallow=bool(payload.depth),
            duration_ms=result.duration_ms,
        )


def _last_line(result) -> str:
    text = (result.stderr or result.stdout or "").strip()
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else "no detail"


# --- git.pull --------------------------------------------------------------


class GitPullInput(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute path inside the repository.")
    remote: str = Field("origin", description="Remote to pull from.")
    branch: str | None = Field(None, description="Branch to pull; defaults to the upstream.")


@register_tool
class GitPullTool(DeniedSystemTool):
    name = "git.pull"
    domain = DOMAIN
    operation = "pull"
    description = (
        "Fetch and integrate changes into a working tree. DISABLED: always "
        "refuses, pending the Security/Permission Engine (PART 7)."
    )
    version = "0.1.0"
    permissions = control_permissions("git.write", network=True, filesystem=True)
    input_model = GitPullInput
    target_arguments = ("path", "remote")
