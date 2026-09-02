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

### 🔲 Part 8 — Memory and Context

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

### 🔲 Part 9 — Hybrid LLM Layer ($0 target)

**Goal:** use free/local inference without creating an alternative tool-execution path.

#### Local model

Planned stack:

- Ollama on the Pi
- Qwen2.5 0.5B–1.5B or TinyLlama 1.1B
- Q4 quantized
- Role: intent classification + privacy/sensitivity classification only
- Keep local prompts short

#### Cloud routing

Planned router:

```text
OmniRoute
  ↓
Groq → Gemini (AI Studio) → Cerebras
```

Use only the OpenAI-compatible:

```text
/v1/chat/completions
```

#### OmniRoute hard restrictions

Do **NOT** connect FIRDAY to:

- OmniRoute bundled MCP server
- A2A features
- Cloud Agent features
- any router feature that gives a model direct access to tools outside FIRDAY

Treat OmniRoute as a **dumb model-routing pipe**.

#### Sensitivity / privacy gate

The first planned gate is deterministic/regex-based, not a paid LLM call.

Detect at minimum:

- home-directory/private file paths
- credential/token patterns
- raw private file contents
- other obvious secrets/private data

For sensitive data:

```text
route to local-only model
OR
block cloud routing and require explicit policy-approved confirmation
```

Do not weaken this rule merely to improve convenience.

#### Cloud tool-result boundary

The sensitivity gate must also apply **after tools execute** and before a tool result is included in a cloud LLM request.

#### Provider configuration contract

Before Part 9 is considered complete, document the exact names/semantics of:

- provider API key environment variables
- provider/model configuration
- request timeout
- retry count
- rate-limit behavior
- fallback conditions
- context/token limits
- provider health/error logging
- queue behavior under free-tier exhaustion

Do not hardcode provider secrets.

#### Planned model/tool flow

```text
User
 ↓
FIRDAY API
 ↓
FIRDAY Core
 ↓
Sensitivity/Privacy Gate
 ↓
Memory retrieval
 ↓
LLM / Planner
 ↓
Structured tool/action request
 ↓
Security Engine
 ↓
Tool Framework
 ↓
Named Tool
 ↓
Tool Result
 ↓
Output Privacy Filter
 ↓
LLM
 ↓
Final Response
```

---

### 🔲 Part 10 — FIRDAY API / Gateway

Expose a clean API for:

- health
- chat/request interaction
- tool activity
- device status
- file operations
- future confirmation/pending actions

Rules:

- routes contain no business logic
- authorization is centralized/abstracted
- API authentication and tool authorization must remain conceptually separate
- Tailscale remains the current network/device trust anchor

Part 10 must define clearly:

```text
network authentication
    ≠
device trust
    ≠
API authorization
    ≠
tool authorization
```

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

**Next milestone:** Part 8 — Memory and Context.

The immediate objective is to create the first real FIRDAY memory layer using the Obsidian vault while preserving:

- existing filesystem security
- separate vault/workspace roots
- correlation/audit behavior
- provider independence
- privacy boundaries
- no-secret storage
- real-Pi verification

Do not jump to Parts 9–16 until Part 8 has been implemented, tested, deployed, and documented.

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
