# FIRDAY — Project Handoff Document

> **Purpose:** This document contains everything needed to continue building FIRDAY if the current AI assistant session ends. It records the current architecture, completed milestones, remaining work, hard constraints, security boundaries, deployment workflow, and decisions that must not be silently changed by a future coding agent.
>
> **Repo location:** `docs/FIRDAY_HANDOFF.md`
>
> **Handoff rule:** A future AI (Claude Code, Codex, Gemini, or another agent) must read this file before modifying FIRDAY. Existing code is the source of truth for implementation details; this document is the source of truth for architectural intent and project constraints. When they disagree, inspect the code and tests before changing either one.

---

## 1. What Is FIRDAY?

FIRDAY is a modular personal AI gateway and control system running on a Raspberry Pi. The long-term goal is one central system that:

- Understands natural-language requests
- Reasons about them using an LLM
- Interacts with the OS through controlled, named tools
- Manages files safely
- Communicates across multiple devices
- Remembers context across conversations
- Eventually performs multi-step autonomous tasks

**FIRDAY is the platform. The LLM is only one component inside it.**

The LLM never executes OS commands directly. Every action must go through:

```text
FIRDAY Core → Security Engine → Tool Framework → Named Tool
```

This is a hard architectural rule, not a style preference.

---

## 2. Non-Negotiable Architecture Rules

These rules apply to every future milestone. A feature is not complete if it violates one of them.

1. FIRDAY Core is independent of any specific LLM provider.
2. The LLM must NOT directly execute OS commands or arbitrary shell commands.
3. LLM-originated actions must enter through FIRDAY Core.
4. Every tool request must pass through the Security Engine before execution.
5. Every OS capability must be exposed as a named tool with fixed inputs/outputs.
6. Tools must use the common Tool Framework and registry.
7. There is **no generic shell tool**, now or later.
8. Frontends/clients use the FIRDAY API; frontend code contains no backend business logic.
9. External integrations are adapters, not separate agent brains.
10. Remote devices must be explicitly authenticated/trusted; current trust anchor is Tailscale identity.
11. Secrets must never be hardcoded or committed to Git.
12. Destructive operations require confirmation or are denied by default.
13. Security-sensitive decisions and important operations must be auditable and carry correlation/request IDs.
14. Cloud LLMs must not receive sensitive/private tool output unless the privacy/security policy explicitly permits it.
15. A provider/router must not bypass FIRDAY's Security Engine.
16. Each subsystem should remain replaceable behind an abstraction.
17. Do not add speculative features or refactor for style during milestone work.
18. Real deployment on the Raspberry Pi is part of verification; local tests alone do not prove a milestone is complete.

### No Generic Shell Tool (HARD RULE)

Part 3 was intentionally removed. FIRDAY has NO generic shell tool. There is no allowlist-based shell escape hatch. Do not reintroduce one for convenience, debugging, automation, or agent functionality.

---

## 3. Current Infrastructure & Environment

| Item | Value |
|------|-------|
| Pi hostname | `sherlock-void` |
| Pi user | `sherlock` |
| Pi Tailscale IP | `100.104.228.90` |
| Pi architecture | `aarch64` (ARM64) |
| Pi RAM | 4GB |
| Git repo | `https://github.com/DrDoom026/Friday.git` |
| Git branch | `master` |
| GitHub username | `DrDoom026` |
| GitHub auth | Classic Personal Access Token (repo scope) |
| App port | `8000` |
| Docker service name | `api` (container: `friday-api-1`) |
| Python version | `3.12` exactly |
| Filesystem sandbox | `/data/workspace` (container) → `~/firday/workspace` (Pi host) |
| Obsidian vault | `~/firday/vault` (Pi host), separate from workspace |
| Private network | Tailscale |

### Dev / Deployment Workflow

```text
Laptop (AI-assisted development)
  → git push origin master
  → SSH into Pi
  → cd ~/Friday
  → git pull
  → docker compose up -d --build
  → verify on the real Pi
```

**Git is the source of truth. Never manually copy project files between machines.**

Secrets and `.env` files are never committed.

### Pi `.env` currently documented

Path: `~/Friday/.env`

```env
APP_ENV=production
LOG_LEVEL=INFO
PORT=8000
FS_ALLOWED_ROOTS=/data/workspace
```

> Part 8 will add the Obsidian vault as a second allowed filesystem root. Do not assume the exact final environment-variable value until implemented and tested in code.

---

## 4. Target Runtime Architecture

The conceptual request path is:

```text
                         USER / CLIENT
                              │
                              ▼
                         FIRDAY API
                              │
                              ▼
                         FIRDAY CORE
                              │
                ┌─────────────┴─────────────┐
                │                           │
                ▼                           ▼
        Privacy / Sensitivity          Context / Memory
             Gate                       Retrieval
                │                           │
                └─────────────┬─────────────┘
                              ▼
                         LLM / Planner
                              │
                              ▼
                    Structured Action Plan
                              │
                              ▼
                       SECURITY ENGINE
                              │
               ┌──────────────┴──────────────┐
               │                             │
          ALLOW / DENY              REQUIRE_CONFIRMATION
               │                             │
               ▼                             ▼
        TOOL FRAMEWORK                 Pending Action
               │                             │
               ▼                             │
             TOOL                           │
               │                             │
               └──────────────┬──────────────┘
                              ▼
                         Tool Result
                              │
                              ▼
                    Output Privacy Filter
                              │
                              ▼
                         FIRDAY CORE
                              │
                              ▼
                           LLM
                              │
                              ▼
                         User Response
```

### Important privacy boundary

The privacy boundary applies in **both directions**:

- Before a cloud LLM request, inspect/sanitize the user request and context.
- Before sending tool output back to a cloud LLM, inspect/sanitize the result as well.

Never assume that because the original prompt was safe, the resulting tool data is safe to send to the cloud.

---

## 5. Security / Authorization Contract

The Security Engine is the mandatory gate immediately before tool execution.

### Permission levels

```text
READ
WRITE
MODIFY
EXECUTE
PRIVILEGED
DESTRUCTIVE
```

### Decision outcomes

```text
ALLOW
DENY
REQUIRE_CONFIRMATION
```

### Current default policy

- `READ` → ALLOW for non-revoked devices
- `WRITE`, `MODIFY`, `EXECUTE`, `PRIVILEGED` → ALLOW by default, configurable
- `DESTRUCTIVE` or `requires_confirmation=True` → REQUIRE_CONFIRMATION
- Unverified device + non-READ → DENY
- Revoked device → DENY everything

### Required authorization flow

Every tool execution must follow this conceptual lifecycle:

```text
Request
  ↓
Resolve tool + inputs
  ↓
Build SecurityContext
  ↓
SecurityEngine.authorize()
  ↓
┌──────────────┬─────────────────────────┬──────────────────┐
│ ALLOW        │ REQUIRE_CONFIRMATION    │ DENY             │
│              │                         │                  │
│ execute      │ create pending action  │ do not execute   │
│ tool         │ wait for confirmation   │ return reason    │
└──────────────┴─────────────────────────┴──────────────────┘
```

### Confirmation lifecycle (future implementation)

Confirmation must **not** be implemented as a boolean bypass such as `confirmed=true` that skips authorization.

The intended pattern is:

```text
Tool request
  ↓
SecurityEngine → REQUIRE_CONFIRMATION
  ↓
Create pending action / confirmation request
  ↓
User explicitly confirms through an approved channel
  ↓
Reconstruct / validate the original action
  ↓
Run SecurityEngine again
  ↓
Only then execute the tool
```

The confirmation mechanism itself must be authenticated to the requesting device/user and must be bound to the exact pending action (tool, arguments, requester, and correlation ID or equivalent). A confirmation must never authorize a different action.

### Security audit

Every security decision should remain auditable with at least:

- timestamp
- correlation/request ID
- device/requester
- tool
- decision
- reason

Current audit logger: `firday.security.audit`.

---

## 6. Completed Milestones

### ✅ Part 0 + 0.5 — Foundation

**Tag:** `v0.0-part0-part0.5`

Implemented:

- FastAPI application
- `GET /health` → `{"status":"ok","env":"production"}`
- Structured logging
- Centralized error handling
- Environment-based configuration
- `.gitignore`, `.env.example`
- Dockerfile + docker-compose
- pytest suite
- Local and Tailscale health verification

### ✅ Part 1 — Core / Orchestrator

**Tag:** `v0.1-part1`

Implemented:

- Request model
- Response model
- Request context
- Correlation/request IDs threaded through logs
- FIRDAY Core orchestration service
- Planner abstraction with one mock implementation
- Tool abstraction/interface
- Execution/result model
- `POST /request`
- Core lifecycle logging
- 27 tests at milestone completion

### ✅ Part 2 — Tool Framework

**Tag:** `v0.2-part2`

Implemented:

- Base Tool interface/protocol
- Tool name/description/version
- Input/output schema metadata
- Permission metadata
- Validation and execution contract
- Structured `ToolResult`
- `ToolExecutionContext`
- Tool registry
- Tool lookup by name
- Tool-specific error types
- Demo `echo` tool
- `GET /tools`

**Tool-count clarification:** before Part 6, the registry contained **11 tools total** (the Part 2/4-era registry). Part 6 added **22 system tools**, bringing the documented total to **33 tools**.

### ⏭️ Part 3 — Generic Shell Tool

**Permanently skipped.** Do not implement.

### ✅ Part 4 — Filesystem Tools

**Tag:** `v0.4-part4`

Allowed root:

```text
/data/workspace (container)
    ↕ bind mount
~/firday/workspace (Pi host)
```

Real tools:

- `fs.list`
- `fs.stat`
- `fs.search`
- `fs.read`
- `fs.write`
- `fs.mkdir`
- `fs.copy`

Initially disabled tools that later route through Part 7:

- `fs.delete`
- `fs.move`
- `fs.rename`

Security implemented:

- canonical path validation
- traversal protection
- symlink escape prevention
- protected system path blocking
- credential-directory blocking
- file-size limits
- filesystem audit logging
- sandbox bootstrap with `ensure_sandbox_ready()`

**Important:** create `~/firday/workspace` on the Pi before first deployment so Docker does not create the bind-mounted directory as `root:root`.

### ✅ Part 5 — Device Management

**Tag:** `v0.5-part5`

Trust mechanism:

```text
Tailscale identity
```

Implemented:

- Device model
- Device registry
- Device lookup/list/update/remove
- Trust policy
- Online/offline status tracking
- `last_seen`
- Live capability model from tool registry
- Device selection/filtering
- `LocalTransport`
- Remote transport stubs
- `POST /devices`
- `GET /devices`
- Pi self-identification using Tailscale identity
- Disabled tools correctly exposed as unavailable
- DNS startup stall fixed by avoiding `getfqdn()`/`gethostbyname_ex()` dependency
- Python 3.12 guard

### ✅ Part 6 — System Tools

**Tag:** `v0.6-part6`

**Total tools:** 33 = 11 existing + 22 added in Part 6.

Processes:

- `proc.list` ✅
- `proc.inspect` ✅
- `proc.terminate` 🔒

Services:

- `service.status` ✅
- `service.start` 🔒
- `service.stop` 🔒
- `service.restart` 🔒

Docker:

- `docker.containers` ✅
- `docker.inspect` ✅
- `docker.logs` ✅
- `docker.images` ✅
- `docker.start` 🔒
- `docker.stop` 🔒
- `docker.restart` 🔒

Network:

- `net.interfaces` ✅
- `net.routes` ✅
- `net.ping` ✅ (ICMP + TCP)
- `net.dns` ✅

Git:

- `git.status` ✅
- `git.branches` ✅
- `git.clone` ✅
- `git.pull` 🔒

Implementation notes:

- `/proc` is read directly; no `ps` command.
- Network tools use `ip -json` and stdlib sockets.
- Git uses `shell=False` and URL-scheme restrictions.
- Git disallows `protocol.ext.allow`.
- Docker uses the Unix socket directly; no Docker SDK.
- Docker socket is mounted read-only, but `:ro` does **not** make the socket protocol itself read-only. Current safety depends on implemented operations plus the Security Engine.
- Dockerfile includes `git`, `iproute2`, `iputils-ping`, and `ca-certificates`.

### ✅ Part 7 — Security / Permission Engine

**Status:** implemented and hardened.

**Tag:** use the actual Git tag in the repository as the authoritative release marker. If a `v0.7-part7` tag exists, record it here; otherwise do not invent a tag.

**Tests at handoff:** 406 passing.

Implemented:

- Permission levels: READ / WRITE / MODIFY / EXECUTE / PRIVILEGED / DESTRUCTIVE
- Decisions: ALLOW / DENY / REQUIRE_CONFIRMATION
- Default security policy
- Device trust integration
- Revoked-device blocking
- Security audit logging
- All previously hardcoded refusal stubs route through SecurityEngine
- `git.clone` regression fixed by using its own `GIT_CLONE` permission class rather than `GIT_WRITE`
- Every tool call is required to pass through SecurityEngine before execution

Current limitation:

`REQUIRE_CONFIRMATION` actions cannot execute yet because a real confirmation channel has not been implemented. They must remain blocked until the confirmation lifecycle exists.

---

## 7. Current State / Next Work

### ✅ Part 8 — Memory and Context

**Status note (corrected during Part 9 work):** implemented in the repository (`app/memory/`, `tests/test_memory.py`) though this section below was left in its original pre-implementation planning form. See `app/memory/service.py` for the actual `MemoryService` API Part 9's `LLMPlanner` calls.

**Design decisions already made:**

- Backend: Obsidian vault using plain Markdown files
- No vector database for now
- Vault location: `~/firday/vault` on the Pi
- Vault is separate from `~/firday/workspace`
- Syncthing is the sync layer
- Syncthing operates over the private network and is independent of FIRDAY code
- Obsidian on laptop/Android can open the synced local vault
- FIRDAY reads/writes vault Markdown files through the existing filesystem abstractions

### Proposed vault structure

The following is the intended logical structure; exact filenames may be changed during implementation if the same separation of concerns is preserved:

```text
vault/
├── People/
├── Preferences/
├── Devices/
├── Tasks/
├── Conversations/
├── Memories/
└── System/
```

### Memory categories

- Temporary conversation context
- User preferences
- Device information
- Task history
- Tool execution history
- Explicit long-term memories (people, relationships, facts)

### Memory rules

Do **not**:

- blindly store every conversation
- store passwords, API keys, tokens, or other secrets
- add a vector database without a demonstrated requirement

Memory writes should have a defined policy. At minimum, implementation must answer:

1. What makes information worth storing?
2. Which component decides that?
3. How are conflicting memories updated?
4. How is memory deleted or corrected?
5. How is relevant memory retrieved for a request?
6. Are sensitive memory writes allowed without confirmation?
7. How are memory/tool outputs kept out of cloud prompts when not permitted?

### Person notes

Each person who interacts with FIRDAY may have a note in the vault containing relationship/context/tone information needed for personalized communication. Do not store unnecessary secrets or highly sensitive information in person notes.

### Syncthing status

Already installed/running on the Pi as a **user service**:

```bash
systemctl --user status syncthing
```

Still required:

- add the vault as a Syncthing shared folder
- pair laptop
- pair Android phone
- install/configure Obsidian on those devices

---

### ✅ Part 9 — Hybrid LLM Layer ($0 target)

**Status:** implemented and unit-tested locally. **Not yet verified as a running service on the Pi** - see "Pi deployment status" below before treating it as deployed.

**Tests at handoff:** 456 passing (427 pre-Part-9 + 29 new).

Implemented (`app/llm/`):

- `app/llm/privacy.py` - deterministic, regex-based sensitivity gate. `is_sensitive()` detects home/private paths (`/home/...`, `~/...`, `.ssh`, `.env`, `id_rsa`), credential/token key=value pairs, AWS/OpenAI/GitHub-style secret shapes, JWTs, and PEM private-key blocks. `redact()` is the output privacy filter - it replaces every matched span with `[REDACTED]`. No cloud LLM call is ever used to make this decision.
- `app/llm/providers.py`:
  - `OllamaClient` - local inference. Only method is `classify_intent()`: a short prompt, single-word category reply. Fails safe (`"unknown"`) if Ollama is unreachable - never blocks planning.
  - `OmniRouteClient` - cloud routing. Only method is `complete()`, calling `POST {base}/v1/chat/completions` (OpenAI-compatible). Capped retries with exponential backoff on network/5xx errors; a `429` fails immediately without retrying (no uncontrolled retry loop against an exhausted rate limit). Raises `LLMProviderError` on final failure - the error message never contains the API key. No MCP, A2A, Cloud Agent, or router-managed tool execution is used or referenced anywhere in this client; it only ever calls the one endpoint.
- `app/llm/planner.py` - `LLMPlanner`, a real implementation of the Part 1 `Planner` protocol:
  - `plan(request, context)`: privacy gate on the raw input first (blocks cloud routing entirely if sensitive, no cloud call made) → best-effort local intent classification (Ollama) → memory retrieval via the existing `MemoryService.search()` (Part 8), capped to `LLM_MEMORY_TOP_K` most-recently-updated notes, never the whole vault, redacted before being placed in the prompt, memory failure caught and logged rather than propagated → cloud `complete()` call → strict JSON parse of the reply (`{"tool_name": str|null, "arguments": {}, "summary": str}`); anything else (invalid JSON, wrong types, provider failure) becomes a structured `Plan` with **no steps** - it never guesses a tool or arguments.
  - `finalize(results, context)`: the output-privacy-filter step. Every `ToolResult` is redacted (`app.llm.privacy.redact`) before it is serialized into the follow-up cloud prompt that produces the final natural-language response. On provider failure, falls back to a plain (still-redacted) status summary instead of raising.
  - The planner never touches the tool registry, Security Engine, or `Tool.execute` - it only returns a `Plan`.
- `app/core/orchestrator.py` - `Core.handle()` gained an `execute: bool = False` keyword (default preserves the exact Part 1/2 `NOT_EXECUTED` contract; existing tests are unchanged). When `execute=True`, each planned step is run through `Tool.execute()`, which is where the **Part 7 Security Engine already lives** (`BaseTool.execute()` calls `get_security_engine().authorize()` before anything runs) - Part 9 did not add a second authorization path, it just started actually calling the one that existed. `DENY` and `REQUIRE_CONFIRMATION` both surface as an `ExecutionStatus.ERROR` result with no side effect, exactly as Part 7 defined. After execution, if the planner defines a `finalize()` method (only `LLMPlanner` does), Core calls it to produce the response text; any planner without `finalize` keeps using the plan summary.
- `app/main.py` - `FIRDAY_PLANNER=llm` (env var, default `mock`) switches the process-wide `Core` to `LLMPlanner`, wired to `OmniRouteClient`, `OllamaClient`, and the existing `MemoryService`. The `/request` endpoint passes `execute=True` only when the active planner defines `finalize` (i.e. only for `LLMPlanner`), so the mock-planner default path is completely unaffected.

#### Environment variables (`app/config.py`)

| Variable | Default | Meaning |
|---|---|---|
| `FIRDAY_PLANNER` | `mock` | `mock` (Part 1, unchanged) or `llm` (Part 9 real planner). |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama server. |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Any Q4-quantized small model pulled into Ollama; `tinyllama:1.1b` also supported. |
| `OMNIROUTE_BASE_URL` | `http://localhost:3333` | OmniRoute's own HTTP address. |
| `OMNIROUTE_MODEL` | `auto` | Model alias passed to OmniRoute; provider selection (Groq → Gemini → Cerebras) is OmniRoute's own internal routing config, not FIRDAY's. |
| `OMNIROUTE_API_KEY` | unset | OmniRoute's own API key, if it requires one. **Never logged** - `OmniRouteClient` only logs status codes/exception types. |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `20.0` | Per-attempt HTTP timeout for both clients. |
| `LLM_MAX_RETRIES` | `2` | Capped retry count for `OmniRouteClient.complete()`; `429` never retries regardless of this value. |
| `LLM_MAX_CONTEXT_CHARS` | `4000` | Soft cap on prompt/system-message size (character count, not a tokenizer). |
| `LLM_MEMORY_TOP_K` | `3` | Max memory notes pulled into a planning prompt. |

None of these are committed; set them in the Pi's `~/Friday/.env` alongside the existing Part 4/6/8 variables.

#### Sensitivity / privacy gate - as implemented

Deterministic and regex-based, exactly as specified - no paid/cloud LLM call decides sensitivity. Runs in two places:

1. **Before any cloud call**: `LLMPlanner.plan()` checks `is_sensitive(request.input)`. If true, cloud routing is blocked outright - the `Plan` returned has no steps and a summary explaining why, and no request ever reaches OmniRoute.
2. **After tool execution, before the next cloud call**: `LLMPlanner.finalize()` runs every `ToolResult` through `redact()` before it is included in the follow-up prompt that produces the final response.

#### Memory integration - as implemented

`LLMPlanner` takes the existing Part 8 `MemoryService` by constructor injection and calls `.search()` (no vector DB, same substring/tag search Part 8 already has). Results are sorted by `updated_at` and capped to `LLM_MEMORY_TOP_K` before being redacted and placed in the prompt - the full vault is never dumped. If `MemoryService.search()` raises for any reason, the planner logs a warning and continues with no memory context; it never turns a memory failure into unrestricted tool access, and planning is not blocked.

#### Provider fallback / rate-limit / failure behavior - as implemented

- FIRDAY calls exactly one cloud endpoint (OmniRoute's `/v1/chat/completions`); Groq → Gemini → Cerebras fallback happens **inside OmniRoute's own configuration**, not in FIRDAY code - FIRDAY has no knowledge of which underlying provider actually answered.
- `OmniRouteClient.complete()` retries up to `LLM_MAX_RETRIES` times with exponential backoff (1s, 2s, 4s, capped at 8s) on network errors or 5xx responses.
- A `429` (rate limit) is treated as immediately fatal - no retry - to avoid hammering an exhausted free tier.
- On total provider failure, `LLMPlanner.plan()` returns a no-op `Plan` (empty steps, explanatory summary) rather than raising; `LLMPlanner.finalize()` falls back to a plain redacted status line. Core is never bypassed and no tool executes as a result of a provider failure.
- A malformed/non-JSON LLM reply is treated the same way as a provider failure: empty-step `Plan`, nothing guessed.

#### OmniRoute hard restrictions (respected)

`OmniRouteClient` only ever calls `/v1/chat/completions`. Nothing in this codebase registers, starts, or talks to OmniRoute's bundled MCP server, A2A, or Cloud Agent features, and no router-managed tool execution path exists. The LLM never calls `Tool.execute` directly - only `Core._resolve_steps` does, and only after `SecurityEngine.authorize()` allows it.

#### How Part 9 is tested (`tests/test_llm.py`, 29 tests)

Privacy gate (sensitive text detected/allowed, redaction), `OllamaClient` (configurable, classifies, fails safe when unreachable), `OmniRouteClient` (success, retry-then-succeed, retries-exhausted failure, 429 fails without retry, API key never appears in logs), `LLMPlanner.plan()` (sensitive input blocks cloud call, well-formed JSON produces a structured `PlanStep`, malformed JSON produces zero steps, provider failure handled gracefully, memory failure fails safe, relevant memory reaches the prompt), `LLMPlanner.finalize()` (tool output redacted before the cloud call, graceful fallback on provider failure), and a `Core` integration group proving an LLM-originated tool step still goes through the real `SecurityEngine` when executed - `REQUIRE_CONFIRMATION` and `DENY` both block execution, and the Part 1 default (`execute` omitted) still returns `NOT_EXECUTED` unchanged.

#### Deferred / intentionally out of scope for Part 9

- Actually pulling a model into Ollama and deploying/running OmniRoute as a process on the Pi (see "Pi deployment status" and "Manual deployment steps" below) - Part 9 delivers the FIRDAY-side integration code and tests, not the third-party service installation.
- Wiring `FIRDAY_PLANNER=llm` as the Pi's production default - it is available via env var but the Pi's `.env` was not changed as part of this milestone; that is an operator decision once Ollama/OmniRoute are actually verified running.
- A real confirmation channel (Part 10/11) - `REQUIRE_CONFIRMATION` still blocks unconditionally, as it did after Part 7.
- Token-accurate context limiting - `LLM_MAX_CONTEXT_CHARS` is a character-count cap, not a tokenizer; adequate for the small local model / short prompts this part specifies, not exact.
- Any API/gateway surface beyond the existing `/request` endpoint (Part 10).

#### Pi deployment status

**Neither Ollama nor OmniRoute has been verified running on the Pi as part of this milestone.** SSH access to the Pi was not available during this implementation session, so this must not be read as "deployed" - it is FIRDAY-side code and tests only, verified locally (Python 3.12, `pytest -v`, 456 passing) and via local import smoke tests of both `FIRDAY_PLANNER=mock` and `FIRDAY_PLANNER=llm` startup paths. A future session (or the operator) must SSH in and check:

```bash
ssh sherlock@100.104.228.90
systemctl --user status ollama 2>&1 || command -v ollama
curl -s http://localhost:11434/api/tags
pgrep -af omniroute
curl -s http://localhost:3333/v1/chat/completions
```

#### Manual deployment steps still required on the Pi

**Ollama:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:0.5b        # or: ollama pull tinyllama:1.1b
systemctl --user enable --now ollama   # or: sudo systemctl enable --now ollama, depending on install mode
curl -s http://localhost:11434/api/tags   # verify
```

**OmniRoute** (Node process; exact package name/repo to be confirmed against the OmniRoute project the operator intends to use - this records the generic install shape, not a verified package name):

```bash
node --version   # Node 18+ expected; install via nvm/apt if missing
npm install -g omniroute        # or: git clone <omniroute repo> && npm install && npm run build
# Configure OmniRoute's own provider priority (Groq -> Gemini -> Cerebras)
# and API keys in ITS config/.env - not FIRDAY's - per OmniRoute's own docs.
omniroute --config ~/omniroute/config.yaml &   # or the project's documented start command
# Persistent process (systemd user service), e.g. ~/.config/systemd/user/omniroute.service:
#   [Unit]
#   Description=OmniRoute LLM router
#   [Service]
#   ExecStart=/usr/bin/node /path/to/omniroute/dist/index.js
#   Restart=on-failure
#   [Install]
#   WantedBy=default.target
systemctl --user daemon-reload
systemctl --user enable --now omniroute
curl -s http://localhost:3333/v1/chat/completions   # verify (expect a 4xx for a bad/empty body, not connection refused)
```

**FIRDAY's `.env` additions** once both are verified running:

```env
FIRDAY_PLANNER=llm
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:0.5b
OMNIROUTE_BASE_URL=http://localhost:3333
OMNIROUTE_MODEL=auto
OMNIROUTE_API_KEY=<if OmniRoute requires one>
```

Then `docker compose up -d --build` and re-verify `/request` end to end against the real container, per the standard Part 9+ verification checklist.

---

### ✅ Part 10 — FIRDAY API / Gateway

Most of the gateway surface (`/health`, `/request`, `/tools`, `/devices*`) already
existed from earlier parts and needed no redesign - Part 10's real work was
adding the file-operations endpoint and the API authentication boundary.

**New:**

- `POST /files/{operation}` - runs one filesystem tool (`list`, `stat`,
  `search`, `read`, `write`, `mkdir`, `copy`, plus the disabled `delete`,
  `move`, `rename` stubs) by name. The route only ever constructs
  `fs.{operation}` as the tool name, so it can never reach a non-filesystem
  tool. It resolves the tool via `Core.execute_tool()` (new: a thin wrapper
  around `ToolRegistry.try_get` + `Tool.execute`, factored out of
  `Core._resolve_steps` so both the planner path and this direct path share
  one execution routine) - so it goes through the exact same Security Engine
  gate as a planned `/request` step. An unknown operation is a 404 before
  Core is ever called; a known-but-denied operation (e.g. `fs.delete`) comes
  back as a normal `ToolResult` with `status: "error"`, not a special case in
  the route.
- API-key authentication (`app/api_auth.py`, `require_api_key` dependency) on
  every functional endpoint except `/health`. Reads `X-API-Key` against
  `FIRDAY_API_KEYS` (comma-separated env var / `Settings.api_keys`). Empty by
  default - the API stays open until an operator configures keys, matching
  the Part 9 `FIRDAY_PLANNER=llm` opt-in convention, so existing deployments
  and the full test suite are unaffected until it's turned on.

**Not added (architecture doesn't need it yet):**

- **Tool activity/status** - `GET /tools` (already existed) is the tool
  status surface: name, permissions, schemas. There is no persisted
  execution-history store to expose beyond the Security Engine's structured
  log lines (`app/security/audit.py`); building one was out of scope for
  Part 10 and would be speculative infra ahead of a real need.
- **Task status** - execution is synchronous; `POST /request` and
  `POST /files/{operation}` already return each step's `ToolResult.status`
  inline. There is no async job queue, so no task-status endpoint was
  invented.

**The four-way boundary, concretely:**

```text
network authentication  -> Tailscale (outside this process; unchanged)
device trust             -> app.devices (Device.trust, derived from Tailscale identity)
API authorization        -> app.api_auth.require_api_key (this Part; X-API-Key header)
tool authorization       -> app.security.engine (Security Engine; unchanged, still mandatory)
```

A missing/invalid API key is rejected by FastAPI's dependency layer before
Core, the planner, or any tool ever runs. DENY and REQUIRE_CONFIRMATION are
unaffected - they still happen inside `Tool.execute`, after API auth has
already passed.

**Limitations:** API keys are a flat shared-secret list (no per-key scoping
or per-device identity) - sufficient for a single-operator Pi gateway behind
Tailscale, not a multi-tenant design. No confirmation channel exists yet
(unchanged from Part 7/9) - `REQUIRE_CONFIRMATION` still blocks rather than
executing.

---

## 8. Future Build Priority

After Part 10, the agreed priority is based on personal utility rather than numeric order:

### 1. Part 13 — Communication Adapters

Approved first adapters:

- Gmail (official API)
- SMS via Android Telephony APIs
- Telegram

Not approved without a separate deliberate architecture review:

- WhatsApp unofficial APIs
- ChatGPT UI scraping
- live voice-call automation

### 2. Part 14 — External Device Agent

Android/Kotlin component using:

```text
Notification Listener Service
        ↓
Notification contents
        ↓
Tailscale → FIRDAY on Pi
        ↓
Obsidian/person context
        ↓
LLM reply generation
        ↓
Android RemoteInput / direct notification reply
```

Do NOT use:

- WhatsApp Web automation
- unofficial WhatsApp APIs
- headless browser automation
- Accessibility tap simulation

This mechanism is considered lower-risk than UI scraping but is **not risk-free** and must be treated as subject to app/platform rules.

Planned behavior includes:

- sender identification
- personalized responses using person notes
- multiple-chat queuing
- provider fallback
- user takeover/notification path for important conversations

The exact timeout language/behavior must be designed before implementation rather than embedded arbitrarily in code.

### 3. Part 15 — Automation Engine

### 4. Part 11 — Web Dashboard

### 5. Part 12 — Voice

### 6. Part 16 — Advanced Multi-Step Agent

**Part 16 is last.** Do not build autonomous multi-step execution until the core, security, memory, API, integrations, and confirmation architecture are stable.

---

## 9. Error / Failure Contracts

The following behaviors must be defined consistently across the system as it grows:

### Tool validation failure

Return a structured validation error. Never execute the tool with partially valid or guessed inputs.

### Security DENY

Do not execute the tool. Return a structured denial and audit the decision.

### REQUIRE_CONFIRMATION

Do not execute the tool. Create/return a pending confirmation state once a confirmation channel exists.

### Tool timeout/failure

Return a structured tool failure containing the correlation/request ID. Do not silently retry destructive or side-effecting operations.

### Device unavailable/untrusted

Do not attempt remote execution through an untrusted/unverified device.

### LLM provider failure

Use the configured provider fallback/queue policy. Never bypass FIRDAY Core or Security Engine just because the preferred provider failed.

### Rate-limit exhaustion

Queue or fail gracefully according to Part 9 policy. Do not spam providers with uncontrolled retries.

### Memory failure

Memory/storage failure must not silently turn into unrestricted tool access. The system should fail safely and report the missing context/state.

---

## 10. Current File Structure

```text
Friday/
├── app/
│   ├── core/
│   │   ├── context.py        # RequestContext, ToolExecutionContext
│   │   ├── models.py         # Request/response models
│   │   ├── orchestrator.py   # FIRDAY Core orchestration
│   │   ├── registry.py       # ToolRegistry/build_default_registry
│   │   └── planner.py        # Planner abstraction + mock implementation
│   ├── devices/
│   │   ├── models.py         # Device, DeviceCapability, TailscaleIdentity, etc.
│   │   ├── registry.py       # DeviceRegistry
│   │   ├── tailscale.py      # Tailscale trust anchor
│   │   ├── trust.py          # TrustPolicy/TailnetTrustPolicy
│   │   ├── selection.py      # DeviceQuery filters
│   │   ├── transport.py      # LocalTransport + remote stubs
│   │   ├── local.py          # Pi self-registration
│   │   └── service.py         # Device service wiring
│   ├── fs/
│   │   ├── policy.py         # FilesystemPolicy + allowed roots
│   │   ├── audit.py          # Filesystem audit logging
│   │   ├── errors.py         # Filesystem error types
│   │   └── bootstrap.py      # ensure_sandbox_ready()
│   ├── security/
│   │   ├── models.py         # PermissionLevel, SecurityDecision, SecurityContext
│   │   ├── policy.py         # DefaultSecurityPolicy
│   │   ├── audit.py          # Security audit logging
│   │   └── engine.py         # SecurityEngine.authorize()
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── echo.py
│   │   ├── filesystem/       # filesystem tools
│   │   └── system/           # process/service/docker/network/git tools
│   ├── memory/                # PART 8: MemoryService, MemoryNote, frontmatter, errors
│   ├── llm/                   # PART 9: hybrid LLM layer
│   │   ├── privacy.py         # deterministic sensitivity gate + output redaction
│   │   ├── providers.py       # OllamaClient (local), OmniRouteClient (cloud)
│   │   ├── planner.py         # LLMPlanner (Planner protocol) + finalize()
│   │   └── errors.py          # LLMProviderError, LLMMalformedResponseError
│   ├── config.py             # Settings/environment loading
│   └── main.py               # FastAPI app/lifespan/routes
├── tests/
│   ├── test_health.py
│   ├── test_core.py
│   ├── test_request_endpoint.py
│   ├── test_tools.py
│   ├── test_registry_wiring.py
│   ├── test_filesystem.py
│   ├── test_bootstrap.py
│   ├── test_devices.py
│   ├── test_system_tools.py
│   ├── test_security_engine.py
│   ├── test_memory.py
│   ├── test_llm.py            # PART 9 tests
│   └── test_python_version.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── mise.toml
└── .env.example
```

> This is a milestone handoff snapshot, not a substitute for checking the actual repository tree. A future agent should inspect `git status`, `git log`, and the filesystem before making assumptions about files that may have been added since this snapshot.

---

## 11. Verification Checklist — Required for Every Milestone

A milestone is not DONE until all applicable checks pass.

### Local

- [ ] Full test suite passes: `pytest -v`
- [ ] Python version is exactly 3.12
- [ ] No new secret/config files are accidentally tracked by Git
- [ ] Existing architecture/security tests still pass

### Real Pi

- [ ] `docker compose up -d --build` succeeds
- [ ] Container starts cleanly
- [ ] `GET /health` returns HTTP 200 on the Pi
- [ ] `/health` works over Tailscale from laptop
- [ ] New functionality is tested against the real running container
- [ ] Relevant audit logs are inspected
- [ ] Failure/deny paths are tested, not only success paths

### Git

- [ ] `git status` is clean except intentional files
- [ ] Work is committed to `master` unless there is a deliberate documented exception
- [ ] Commit message clearly identifies the milestone/change
- [ ] Release tag created for a completed milestone
- [ ] Tag pushed to origin

Suggested tag pattern:

```text
v0.X-partY
```

Do not invent a historical tag for a milestone that was never actually tagged.

---

## 12. Common Commands

```bash
# Deploy to Pi
ssh sherlock@100.104.228.90
cd ~/Friday
git pull
docker compose up -d --build

# Container logs
docker compose logs -f

# Run local tests
source .venv/bin/activate
pytest -v

# Security audit logs
docker compose logs | grep "firday.security.audit"

# List tools
curl localhost:8000/tools | python3 -m json.tool

# List devices
curl localhost:8000/devices

# Test request endpoint
curl -X POST localhost:8000/request \
  -H "Content-Type: application/json" \
  -d '{"input":"hello"}'

# Syncthing status (Pi user service)
systemctl --user status syncthing

# Check git state
cd ~/Friday
git status
git branch --show-current
git log --oneline -10
```

---

## 13. Syncthing / Obsidian Status

Current Pi status:

- Syncthing installed
- Syncthing running as a user service
- SSH tunnel for the Syncthing web UI has been used:

```bash
ssh -L 8384:127.0.0.1:8384 sherlock@100.104.228.90
```

Then:

```text
http://localhost:8384
```

Pi Syncthing device name:

```text
sherlock-void
```

Still to do:

- add `~/firday/vault` as a shared Syncthing folder
- pair laptop
- pair Android phone
- configure Obsidian to open the synced vault

---

## 14. Key Decisions Reference

| Decision | Choice | Reason / Constraint |
|----------|--------|---------------------|
| Platform | FIRDAY on Raspberry Pi | Central personal AI gateway |
| LLM architecture | Provider-independent | Avoid lock-in |
| Generic shell | Forbidden | Fixed named-tool attack surface |
| Filesystem sandbox | `/data/workspace` | Prevent uncontrolled filesystem access |
| Vault | Separate allowed root | Different handling for memory vs scratch |
| Device trust | Tailscale identity | Existing private-network trust anchor |
| Security gate | Central SecurityEngine | Single authorization boundary |
| Confirmation | Explicit pending-action flow | No boolean bypasses |
| Audit | Structured decision logging | Traceability/correlation |
| Memory | Obsidian Markdown | Human-readable, simple, no vector DB required yet |
| Sync | Syncthing | Self-hosted peer-to-peer sync |
| Local model | Ollama + small Q4 model | Classification/privacy role, low resource use |
| Cloud routing | OmniRoute | One OpenAI-compatible routing layer |
| Cloud providers | Groq → Gemini → Cerebras | $0 target/free-tier strategy |
| OmniRoute MCP/A2A/Agents | Forbidden | Prevent tool-path bypass |
| Cloud privacy | Gate input and tool output | Avoid leaking private data to cloud models |
| Python | 3.12 exactly | Matches Docker/runtime/tests |
| Docker SDK | Not used | Direct Unix-socket implementation already established |
| Chat automation | Android Notification Listener + RemoteInput | Avoid UI scraping/accessibility automation |
| Advanced agent | Part 16, last | Core/security/memory must mature first |

---

## 15. Things That Will Bite You If Forgotten

1. Create `~/firday/workspace` on the Pi before first Docker deployment.
2. Python must remain 3.12; do not casually upgrade the local venv to 3.14+.
3. Check the active branch before committing; the intended branch is `master`.
4. `.env` lives on the Pi and is not committed.
5. OmniRoute must remain a routing pipe; no MCP/A2A/Cloud Agent tool paths.
6. `git.clone` uses its dedicated permission class, not `GIT_WRITE`.
7. Syncthing is a user service: use `systemctl --user`.
8. A `REQUIRE_CONFIRMATION` result must never become an automatic execution path.
9. Never send raw sensitive tool results to a cloud model just because the original user request appeared harmless.
10. Never add a generic shell tool.
11. Do not declare a milestone complete because unit tests pass; verify the deployed container on the actual Pi.
12. Do not silently redesign the architecture to fit a particular AI provider, framework, or coding-agent preference.

---

## 16. How a New AI Agent Should Start

When another coding AI takes over FIRDAY, it should do this before changing code:

```text
1. Read docs/FIRDAY_HANDOFF.md completely.
2. Inspect git status and current branch.
3. Inspect the latest git log and milestone tag.
4. Run the existing test suite.
5. Inspect the actual repository tree.
6. Compare the handoff against the current code/tests.
7. Identify the next unfinished milestone.
8. Make only the smallest changes required for that milestone.
9. Test locally.
10. Deploy to the real Pi.
11. Test against the running container.
12. Commit to master.
13. Tag the milestone.
14. Update this handoff document with what actually changed and what remains.
```

### AI-agent instruction

**Do not infer that a feature exists because it is listed here as planned. Planned items are not implemented until the code/tests/deployed Pi verify them.**

**Do not remove or weaken a security rule because a provider, framework, or agent workflow makes implementation more convenient.**

**When uncertain, inspect existing code and tests first. Prefer a small, reversible, testable change over architectural expansion.**

---

## 17. Current Next Milestone

**Next milestone:** finish Part 10's real-Pi verification, then move to Part 11+ (per section 8's priority order).

Part 10 (FIRDAY API / Gateway) is implemented and locally tested (466 passing:
456 Part 9 baseline + 10 new). **Not yet verified on the real Pi** - SSH access
to `sherlock@100.104.228.90` was not available during this implementation
session (publickey/password auth both rejected), so this must not be read as
"deployed". A future session (or the operator) must:

- SSH to the Pi, `git pull`, `docker compose up -d --build`.
- Confirm `/health` returns 200 over Tailscale.
- From the laptop, hit `POST /files/list` (or another new Part 10 endpoint)
  against the real container and confirm it responds correctly through the
  full API → Core → Security Engine → Tool path.
- Update this section once verified.

Do not jump to Parts 11–16 until Part 10 has been implemented, tested, deployed, and documented.

---

## 18. Handoff Maintenance Rule

Update this document after every completed milestone.

At minimum update:

- status/tag
- test count
- file structure when it materially changes
- new environment variables
- new security decisions
- new external dependencies
- new deployment commands
- new failure modes / gotchas
- next milestone

The handoff should describe **what is actually true in the repository**, not what was intended but never implemented.
