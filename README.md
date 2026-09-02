# FIRDAY

Minimal FastAPI backend.

- **PART 0 + 0.5** — a runnable, containerized skeleton with a health check,
  structured logging, centralized error handling, and env-based config.
- **PART 1** — FIRDAY Core: an orchestrator that runs a request through a
  planner abstraction and returns a response. The only planner is a mock that
  returns a canned plan; no LLM, nothing executes yet.
- **PART 2** — Tool framework: the common interface every capability will
  implement (name, description, version, input/output schemas, permission
  metadata, validation, execution, structured results), plus a registry and one
  harmless demo tool (`echo`). Permission metadata is declared but not
  enforced — that is a later part.
- **PART 4** — Filesystem: ten registered `fs.*` tools behind a sandbox
  (allowed roots, traversal and symlink-escape defence, protected system paths,
  size limits) with an audit record for every attempt. `fs.delete`, `fs.move`
  and `fs.rename` are registered but disabled until Part 7.

- **PART 5** — Devices: a device model, registry, capability claims, status
  tracking and selection, with trust anchored on Tailscale identity and a
  transport interface whose only real implementation is `local`.

- **PART 6** — System: twenty-two registered tools across five domains
  (`proc.*`, `service.*`, `docker.*`, `net.*`, `git.*`). Fourteen run for
  real; the eight that change system state are registered and refuse until
  Part 7.

- **PART 7** — Security / Permission Engine: the decision layer that authorizes
  every tool execution. Maps tool permission metadata and device trust (from
  Part 5) to ALLOW, DENY, or REQUIRE_CONFIRMATION decisions. Replaces the hardcoded
  stubs in `fs.delete`/`move`/`rename` and system control tools with dynamic policy
  evaluations and structured security audit logging (`firday.security.audit`).

  PART 3 (a generic shell tool) is intentionally skipped by design.

## Requirements

- **Python 3.12.x** — the same minor version the Docker image deploys on, not
  merely "3.12 or newer". See [Python version](#python-version) for why.
- Docker + Docker Compose (for containerized run)

## Run locally

```bash
mise install                 # installs the pinned Python (see mise.toml)
mise exec -- python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env

uvicorn app.main:app --reload
```

Without `mise`, use any 3.12 interpreter — `python3.12 -m venv .venv`. The
suite refuses to pass on a mismatched version, so you will know immediately.

Check it:

```bash
curl http://127.0.0.1:8000/health
```

## Run tests

```bash
source .venv/bin/activate
pytest -v
```

### Python version

The local interpreter must match the Dockerfile's base image exactly, and
`tests/test_python_version.py` fails the suite if it does not.

This is not pedantry. Part 5 shipped a bug that every local test passed over: a
method named `list` shadowed the builtin inside a class body, which breaks the
`-> list[...]` annotations below it. Python 3.12 evaluates those annotations
eagerly and raised `TypeError` at import; 3.14 defers them (PEP 649) and did
not. The suite was green on a 3.14 venv while the 3.12 container crash-looped
on startup — a whole class of error that a version gap makes invisible.

The pin lives in three places, with the Dockerfile as the source of truth:

| Where | What it does |
|-------|--------------|
| `Dockerfile` | `FROM python:3.12-slim` — what actually runs in production |
| `mise.toml` | Installs that interpreter locally |
| `pyproject.toml` | `requires-python = ">=3.12,<3.13"` — declares the constraint |

To move versions, change the Dockerfile, then rebuild the venv. The guard fails
until the two agree, in either direction.

## Run via Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Check it:

```bash
curl http://127.0.0.1:8000/health
```

The compose file bind-mounts the sandbox so its contents outlive the container:

```yaml
environment:
  FS_ALLOWED_ROOTS: /data/workspace
volumes:
  - "${FS_HOST_ROOT:-${HOME}/firday/workspace}:/data/workspace"
```

`FS_ALLOWED_ROOTS` is overridden here on purpose. In the container `~` expands
to `/root`, which the sandbox refuses as a protected system path, so the
container-side root must be an absolute path outside it. Set `FS_HOST_ROOT` to
put the host side somewhere other than `~/firday/workspace`.

## Endpoints

| Method | Path       | Description                                             |
|--------|------------|---------------------------------------------------------|
| `GET`  | `/health`  | Liveness check.                                          |
| `GET`  | `/tools`   | Lists registered tools with schemas and permissions.     |
| `POST` | `/devices` | Registers a device; trust is derived, not supplied.      |
| `GET`  | `/devices` | Lists devices, filterable by capability/trust/status.    |
| `GET`  | `/devices/{id}` | One device, or 404.                                |
| `POST` | `/devices/{id}/heartbeat` | Refreshes `last_seen` and status.        |
| `POST` | `/request` | Runs input through Core and returns the mock plan.      |

`POST /request` generates a correlation ID per request, threads it through the
lifecycle logs, and returns it in the body and the `X-Request-ID` header. Send
your own `X-Request-ID` header to reuse an existing trace ID.

```bash
curl -X POST http://127.0.0.1:8000/request \
  -H 'Content-Type: application/json' \
  -d '{"input": "summarise my inbox"}'
```

Core resolves every planned step against the tool registry — the tool must
exist and the arguments must validate against its input schema — then stops at
status `not_executed`. A step naming an unknown tool, or carrying arguments the
schema rejects, comes back as `error`. Actually running the tool lands in a
later part.

## Adding a tool

Subclass `BaseTool`, declare the metadata and schemas, implement `run`, and
decorate with `@register_tool`. See `app/tools/echo.py` for the reference
implementation.

```python
@register_tool
class EchoTool(BaseTool):
    name = "echo"
    description = "Returns the message it was given. Has no side effects."
    version = "1.0.0"
    permissions = ToolPermissions(side_effect=SideEffect.NONE)
    input_model = EchoInput
    output_model = EchoOutput

    async def run(self, payload: EchoInput, context: ToolExecutionContext) -> EchoOutput:
        return EchoOutput(message=payload.message, length=len(payload.message))
```

Import it from `app/tools/__init__.py` and `build_default_registry()` will
discover it. `BaseTool.execute` handles validation, timing, and wrapping the
outcome in a `ToolResult` — subclasses only implement `run`.

## Filesystem tools (PART 4)

Every filesystem operation is a registered tool. Seven of them execute:

| Tool        | Does                                              |
|-------------|---------------------------------------------------|
| `fs.list`   | List a directory's contents                        |
| `fs.stat`   | Metadata for one path                              |
| `fs.search` | Find entries by name pattern under a root          |
| `fs.read`   | Read a text file                                   |
| `fs.write`  | Create, overwrite or append to a file              |
| `fs.mkdir`  | Create a directory                                 |
| `fs.copy`   | Copy a file or tree (never overwrites)             |

Three are registered, schema-complete, and **disabled** — every call returns a
`not yet authorized` error result without touching the disk:

| Tool         | Blocked until                          |
|--------------|-----------------------------------------|
| `fs.delete`  | PART 7 — Security/Permission Engine      |
| `fs.move`    | PART 7 — Security/Permission Engine      |
| `fs.rename`  | PART 7 — Security/Permission Engine      |

Their authorization check (`DestructiveFilesystemTool.is_authorized`) is a stub
that always denies. It is the single place Part 7 will replace.

### The sandbox

`app/fs/policy.py` resolves every path before any tool touches it:

1. The path must be absolute, NUL-free and of sane length.
2. A `..` component is refused outright, not normalized away.
3. The path is canonicalized, collapsing `.`, `..` and every symlink.
4. The canonical path must sit inside a configured allowed root.
5. The canonical path must not sit inside a protected system location
   (`/etc`, `/usr`, `/proc`, `/sys`, `/root`, `/var`, …), nor pass through a
   credential directory (`.ssh`, `.gnupg`, `.aws`, …) — this holds even when a
   root was misconfigured to contain one.

Reads, writes, appends and copies are additionally capped by byte limits.

### Audit trail

Every attempt — allowed or denied — writes exactly one record to the
`firday.fs.audit` logger, carrying the operation, the raw and canonical paths,
the decision, the outcome and the request's correlation ID from Part 1:

```json
{"timestamp": "...", "level": "WARNING", "logger": "firday.fs.audit",
 "message": "fs audit op=read tool=fs.read decision=denied outcome=denied path=/etc/passwd resolved=- invocation_id=811d3a1a... detail=path '/etc/passwd' is not allowed: '/etc' is a protected system path",
 "request_id": "live-fs-demo"}
```

### Startup check

The sandbox is validated at boot, not on first use. `app/fs/bootstrap.py` runs
from the application lifespan and either makes the sandbox usable or stops the
process:

- a configured root that does not exist is created (`mkdir -p`);
- a root that is missing, relative, not a directory, not writable, or inside a
  protected system path aborts startup with a message naming the problem, the
  current `FS_ALLOWED_ROOTS`, and the fix.

So a misconfigured sandbox fails at `docker compose up`, not on the first
`fs.*` call hours later:

```
filesystem sandbox is unusable: allowed root '/root/firday/workspace' is inside
protected system path '/root'
  FS_ALLOWED_ROOTS = ~/firday/workspace
  fix: point FS_ALLOWED_ROOTS somewhere writable. In a container ~ expands to
  /root, which is a protected system path. ...
```

## Devices (PART 5)

Every machine FIRDAY can act on is a device. The Pi it runs on registers itself
at startup, so `/devices` is answerable from boot.

| Field | Notes |
|-------|-------|
| `device_id` | Stable; the local machine is always `local` |
| `name`, `platform`, `architecture` | `platform` normalizes `Linux`/`macOS`/`win32`/… |
| `network` | hostname, FQDN, addresses |
| `tailscale` | Node id, MagicDNS name, addresses, tailnet user — when resolved |
| `capabilities` | What the device *claims* it can do |
| `status` | `online` / `offline` / `unknown` |
| `trust` | `trusted` / `unverified` / `untrusted` / `revoked` |
| `permissions` | Declared only; PART 7 enforces |
| `last_seen` | Refreshed by `/heartbeat` |

### Trust comes from Tailscale

There is no FIRDAY token system, by design. A device is expected to already be
on the tailnet, so "who is this?" is answered by asking Tailscale — two ways,
because it depends how FIRDAY is exposed:

- **Identity headers.** Behind `tailscale serve`/`funnel`, the proxy injects
  `Tailscale-User-Login` / `Tailscale-User-Name`, which a client cannot forge
  through it.
- **`tailscale whois`.** Reached directly on a tailnet address, the peer's
  source IP resolves to a node and user via the local `tailscaled`.

Both are read-only lookups against the local daemon, so no Tailscale client
library is needed — the CLI is shelled out to. `TailscaleIdentitySource` is the
seam if the LocalAPI socket is preferred later.

The rules: `revoked` is sticky and never silently lifted; an identified node on
the expected tailnet user is `trusted`; a different tailnet user is
`untrusted`; anything unresolvable is `unverified`. **When Tailscale is
unavailable the policy degrades to never granting trust** — it does not fall
back to a substitute check.

A device cannot assert its own trust: `trust` and `tailscale` are not fields on
the registration model, and `DeviceRegistry.update()` refuses to set trust.

### Transports

`local` is the only implemented transport — the Pi itself. `ssh`, `tailscale`
and `agent` are declared so a device can name one, but every call raises
`TransportNotImplementedError`. The interface stops at "can I reach this
device?"; there is deliberately no method that runs a command, because remote
execution is gated on PART 7.

### Docker

The compose file mounts the host's `tailscaled` socket and `tailscale` binary
read-only so the container can perform identity lookups. Both are optional —
without them devices simply stay `unverified`.

## System tools (PART 6)

Five families of high-level tools, all registered through the same Part 2
framework. The split is the one Part 4 drew across the filesystem: **a tool
that observes runs; a tool that changes something waits for Part 7.**

| Domain | Runs for real | Registered, always refuses |
|--------|---------------|----------------------------|
| Processes | `proc.list`, `proc.inspect` | `proc.terminate` |
| Services  | `service.status` | `service.start`, `service.stop`, `service.restart` |
| Docker    | `docker.containers`, `docker.inspect`, `docker.logs`, `docker.images` | `docker.start`, `docker.stop`, `docker.restart` |
| Network   | `net.interfaces`, `net.routes`, `net.ping`, `net.dns` | — |
| Git       | `git.status`, `git.branches`, `git.clone` | `git.pull` |

`git.clone` sits on the enabled side for the same reason `fs.copy` does: it
only ever creates something new, and refuses any destination that already
exists rather than writing into it.

`git.pull` does not, and the reasoning is worth recording. It rewrites a
working tree someone may be mid-edit in, it can fast-forward a branch out from
under uncommitted work, and it applies whatever the remote currently says.
"It is only one more write operation" is precisely the argument the skipped
Part 3 exists to refuse, so it gets no exception.

### How each family reaches the system

| Family | Mechanism | Why |
|--------|-----------|-----|
| `proc.*` | `/proc`, read directly | `ps` is not in the slim image and its output format drifts between distributions; `/proc` is the kernel's own interface |
| `service.*` | `systemctl show` | Emits stable `Key=Value` lines meant for machines, and exits zero for an unknown unit |
| `docker.*` | The engine's HTTP API over its unix socket | No SDK and no CLI binary whose libc has to match the host's |
| `net.interfaces`, `net.routes` | `ip -json` | The kernel's own view as JSON, so there is no column scraping |
| `net.ping`, `net.dns` | The standard library | `getaddrinfo` is the resolver the rest of FIRDAY uses, and a TCP probe needs no binary |
| `git.*` | `git`, via a fixed argv | Never a command string — see below |

The image installs exactly four packages for this: `git`, `iproute2`,
`iputils-ping` and `ca-certificates`. No Python dependency was added.

### Nothing here is a shell

Part 3 was skipped by design, and `app/system/command.py` is not a way back to
it. Nothing accepts a command string. Every caller passes a fixed argument
vector whose executable is a constant in FIRDAY's own source, `shell=False` is
not overridable, and each caller-supplied argument goes through
`reject_option_like` first so a value cannot arrive disguised as a flag.

`git.clone` gets two more layers, because git's URL syntax is itself
executable: the `ext::` transport hands git a command to run as its transport.
So the URL is checked against an allow-list of schemes, and every git
invocation carries `-c protocol.ext.allow=never` in case a URL form slipped
past the check.

### The sandbox is the Part 4 one

The `git.*` tools resolve every path through the same
`FilesystemPolicy` the `fs.*` tools use. There is no second sandbox, no second
set of allowed roots, and `git.status /etc` is refused by the same rule that
refuses `fs.read /etc`.

### Audit trail

Every system operation produces exactly one record on `firday.system.audit`,
carrying the request's correlation ID — the same contract `firday.fs.audit`
established in Part 4:

```
system audit domain=docker op=stop tool=docker.stop decision=denied \
  outcome=not_authorized target=firday-api-1 invocation_id=b7a2… \
  detail=state-changing system operations are gated on …
```

Successes log at INFO; denials, refusals and errors log at WARNING.

### Refusing, honestly

A disabled tool returns before the framework validates its input, so there is
no code path from a caller to a signal, a unit, a container or a working tree:

```json
{
  "authorized": false,
  "domain": "process",
  "operation": "terminate",
  "reason": "state-changing system operations are gated on the Security/Permission Engine (PART 7), which does not exist yet",
  "blocked_until": "PART 7 - Security/Permission Engine",
  "targets": ["4211"]
}
```

`blocked_until` is imported from the Part 4 destructive tools rather than
restated, so the two families cannot drift apart. The tests do not take the
refusal at its word: each one spawns a real process, points at a real running
container, or builds a clone whose origin has moved ahead, and then checks the
real system afterwards.

### Docker access, and what `:ro` does not do

The compose file mounts the host's Docker socket read-only. It is optional —
without it those four tools report Docker as unreachable and nothing else
changes.

Be clear about the trust involved: a bind mount does not make a unix socket
read-only, and anything that can reach the Docker API can control the daemon.
What actually keeps FIRDAY read-only here is that `DockerClient` implements no
write verb at all — no `post`, no `put`, no `delete` — and that
`docker.start`/`stop`/`restart` refuse before reaching it.

### Where systemd is not

`service.status` needs a systemd manager. There is none inside FIRDAY's
container, so there it reports:

```
required command 'systemctl' is not available on this host
(service tools need systemd; this looks like a container)
```

That is the same graceful degradation Part 5 applies to Tailscale: say what is
missing rather than guess. Making it work from inside the container would mean
installing systemd in the image and mounting the host's D-Bus system socket —
a much wider privilege surface than the Docker socket, for one read-only tool.
That trade has not been made. Run FIRDAY on the host, or accept the limitation.


## Configuration

Config is loaded from environment variables (see `.env.example`):

| Variable   | Default       | Description                       |
|------------|---------------|------------------------------------|
| `APP_ENV`  | `development` | Environment name, echoed in `/health` |
| `LOG_LEVEL`| `INFO`        | Log level for structured logging   |
| `PORT`     | `8000`        | Host port mapped in Docker Compose |
| `FS_ALLOWED_ROOTS` | `~/firday/workspace` | Colon-separated absolute roots the `fs.*` tools may touch |
| `FS_HOST_ROOT` | `~/firday/workspace` | Docker only: host side of the sandbox bind mount |
| `FS_MAX_READ_BYTES` | `5242880` | Largest file `fs.read` will load |
| `FS_MAX_WRITE_BYTES` | `5242880` | Largest payload `fs.write` will write |
| `FS_MAX_COPY_BYTES` | `52428800` | Largest file or tree `fs.copy` will duplicate |
| `FS_MAX_LIST_ENTRIES` | `1000` | Cap on entries returned by `fs.list` |
| `FS_MAX_SEARCH_RESULTS` | `500` | Cap on matches returned by `fs.search` |
| `FS_MAX_SEARCH_DEPTH` | `12` | How deep `fs.search` will recurse |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Where the `docker.*` tools look for the engine |
| `SYSTEM_COMMAND_TIMEOUT_SECONDS` | `10` | Deadline for a system tool's external command |
| `SYSTEM_GIT_TIMEOUT_SECONDS` | `120` | Deadline for `git.clone`, which is network-bound |
| `SYSTEM_MAX_PROCESSES` | `500` | Cap on processes returned by `proc.list` |
| `SYSTEM_MAX_LOG_LINES` | `500` | Cap on lines returned by `docker.logs` |
| `SYSTEM_MAX_PING_COUNT` | `10` | Cap on echo requests `net.ping` will send |

Never commit a real `.env` file — it's git-ignored. Copy `.env.example` to
`.env` and edit locally.
