# Graph Report - Friday  (2026-09-02)

## Corpus Check
- 18 files · ~45,834 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1593 nodes · 3902 edges · 87 communities (59 shown, 26 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 360 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- App Memory
- Tests Test Filesystem
- App Security
- App Devices
- App System
- App Devices
- App Tools System
- App Tools
- App Core
- Tests Test Devices
- App Devices
- Docs Firday Handoff
- Tests Test Tools
- Tests Test
- App Main
- App System Docker
- App Devices Transport
- App Tools System
- Tests Test System
- Tests Test System
- App Core
- App Core
- Tests Test Devices
- App System Procfs
- Tests Test
- App Tools System
- App Fs Policy
- Tests Test Bootstrap
- App Tools Filesystem
- App Tools Filesystem
- App Devices
- Tests Test
- App Tools System
- Tests Test System
- App Fs Errors
- Tests Test System
- Tests Test Devices
- App Tools Filesystem
- Tests Test System
- App Devices
- App Fs
- App Devices
- Tests Test System
- Tests Test System
- Tests Test Python
- App Tools Filesystem
- App Tools Filesystem
- Tests Test Request
- App Config
- Docs Firday Handoff
- App Core Context
- App Fs Audit
- Requirements Dev
- App Core Orchestrator
- App Tools
- App Security Engine
- Tests Test System
- App Logging Config
- App Devices Models
- Tests Test Devices
- Tests Test Filesystem
- App Core Orchestrator
- App Security Audit
- Tests Test Devices
- App Security Init
- App System Init
- App Tools Filesystem
- Docs
- Docs
- Readme
- Readme
- Tests Test Devices
- Tests Test Devices
- Tests Test Devices
- Tests Test Filesystem
- App
- Docs
- Docs
- Docs
- Docs
- Pkg
- Readme
- Readme
- Tests
- Tests

## God Nodes (most connected - your core abstractions)
1. `ToolExecutionContext` - 110 edges
2. `ExecutionStatus` - 79 edges
3. `call()` - 62 edges
4. `Device` - 50 edges
5. `RequestContext` - 47 edges
6. `call()` - 45 edges
7. `register_tool()` - 45 edges
8. `registration()` - 42 edges
9. `SideEffect` - 41 edges
10. `ToolPermissions` - 40 edges

## Surprising Connections (you probably didn't know these)
- `test_tool_protocol_rejects_non_conforming_object()` --uses--> `Tool`  [INFERRED]
  tests/test_core.py → app/core/tools.py
- `test_derive_permission_level_destructive_scope()` --uses--> `SideEffect`  [INFERRED]
  tests/test_security_engine.py → app/core/tools.py
- `test_derive_permission_level_none()` --uses--> `SideEffect`  [INFERRED]
  tests/test_security_engine.py → app/core/tools.py
- `test_derive_permission_level_read()` --uses--> `SideEffect`  [INFERRED]
  tests/test_security_engine.py → app/core/tools.py
- `test_derive_permission_level_requires_confirmation_system()` --uses--> `SideEffect`  [INFERRED]
  tests/test_security_engine.py → app/core/tools.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Mandatory Request Pipeline (Core to Security to Tools)** — docs_firday_handoff_firday_core, docs_firday_handoff_security_engine, docs_firday_handoff_tool_framework [EXTRACTED 1.00]
- **Part 8 Memory/Sync Architecture** — docs_firday_handoff_obsidian_vault, docs_firday_handoff_syncthing, dockercompose_fs_vault_root [INFERRED 0.85]
- **Filesystem Sandbox Implementation** — readme_fs_sandbox, dockercompose_fs_allowed_roots, docs_firday_handoff_part4_filesystem [INFERRED 0.85]

## Communities (87 total, 26 thin omitted)

### Community 0 - "App Memory"
Cohesion: 0.05
Nodes (64): ConversationContext, Temporary conversation context: in-memory only, never persisted. Scoped to a…, The messages exchanged so far in one session. Gone when the process forgets it., MemoryError, MemoryNotFoundError, MemoryStorageError, Exception, Errors raised by the memory service. (+56 more)

### Community 1 - "Tests Test Filesystem"
Cohesion: 0.07
Nodes (57): ExecutionStatus, Enum, str, ReadFileTool, WriteFileTool, SearchTool, StatTool, CopyTool (+49 more)

### Community 2 - "App Security"
Cohesion: 0.07
Nodes (58): Declared capability requirements for a tool. Metadata only — nothing here is…, ToolPermissions, Audit trail for security / permission decisions. Every tool evaluation -…, Log one security evaluation attempt and return the record that was written., record_security_decision(), Security Engine: the authorization gate between Core and tool execution.…, PermissionLevel, BaseModel (+50 more)

### Community 3 - "App Devices"
Cohesion: 0.06
Nodes (34): A device's identity as Tailscale reports it. This is FIRDAY's trust anchor. It…, True when Tailscale gave us something that actually names a node., TailscaleIdentity, DeviceService, Wiring for device management: registry + trust policy + transports. One object…, Registry, trust policy, identity resolver and transports in one place., Build the service, discovering the tailnet user FIRDAY runs as. When Tailscale…, Ask Tailscale who is calling. (+26 more)

### Community 4 - "App System"
Cohesion: 0.07
Nodes (40): Running an external command, without a shell and without surprises. Part 3 - a…, :func:`run_command` on a worker thread, so a tool never blocks the loop., Resolve ``binary`` on PATH or raise :class:`CommandNotAvailableError`., Refuse a caller-supplied argv value that could be mistaken for an option.…, reject_option_like(), require_available(), run_command_async(), CommandFailedError (+32 more)

### Community 5 - "App Devices"
Cohesion: 0.06
Nodes (24): Device, A machine FIRDAY knows about., True when the device claims ``name`` (and, by default, claims it usable)., DeviceRegistry, datetime, The device carrying this Tailscale node id, if one is registered., Every device, best-first. Not named ``list``: a method called ``list`` shadows…, Serializable summaries, for the API and for logging. (+16 more)

### Community 6 - "App Tools System"
Cohesion: 0.09
Nodes (34): get_default_client(), The Docker Engine API, over its local unix socket. No Docker SDK and no…, The process-wide client built from settings, created on first use., DockerUnavailableError, The Docker Engine could not be reached., ContainerControlInput, ContainerInspectOutput, ContainerListInput (+26 more)

### Community 7 - "App Tools"
Cohesion: 0.09
Nodes (27): Request-scoped context handed to a tool when it runs. Wraps the Part 1…, ToolExecutionContext, get_security_engine(), Get the process-wide Security Engine instance., Audit trail for system operations. The same contract Part 4 established for the…, One system operation attempt, as recorded., Log one system operation attempt and return the record that was written.…, record_attempt() (+19 more)

### Community 8 - "App Core"
Cohesion: 0.07
Nodes (24): Any, Exception, KeyError, Tool-specific error types. Everything raised by the tool framework derives from…, Base class for every tool framework failure., A tool was looked up by a name the registry does not know., A name collision at registration time., ToolAlreadyRegisteredError (+16 more)

### Community 10 - "App Devices"
Cohesion: 0.12
Nodes (30): Device / remote machine management (PART 5). What FIRDAY knows about the…, build_local_device(), capabilities_from_tools(), local_addresses(), Describing the machine FIRDAY is running on. The Pi is the one real device in…, Best-effort local addresses via DNS. Never raises. Off the startup path by…, Turn the registered tools into capability claims for this device., Describe this machine as a device, without contacting Tailscale. The Tailscale… (+22 more)

### Community 11 - "Docs Firday Handoff"
Cohesion: 0.06
Nodes (36): FS_ALLOWED_ROOTS Override, FS_VAULT_ROOT (Part 8 Vault Mount), Tailscale Socket/Binary Mount, Confirmation Lifecycle Design, FIRDAY Core, git.clone Uses Dedicated GIT_CLONE Permission, Obsidian Vault (Markdown Memory), Ollama Local Model (+28 more)

### Community 12 - "Tests Test Tools"
Cohesion: 0.13
Nodes (29): Arguments did not match a tool's input schema., ToolValidationError, EchoInput, EchoOutput, EchoTool, BaseModel, The echo tool: a harmless demonstration of the Part 2 tool framework. It reads…, Built-in FIRDAY tools. Importing this package registers every built-in tool… (+21 more)

### Community 13 - "Tests Test"
Cohesion: 0.11
Nodes (31): Carries request-scoped information through Core, planner and tools., Build a context, reusing a caller-supplied correlation ID when usable., RequestContext, Replace the global Security Engine (primarily for testing)., The decision layer that authorizes every tool execution. Evaluates a tool…, SecurityEngine, set_security_engine(), test_context_falls_back_when_supplied_id_is_blank() (+23 more)

### Community 14 - "App Main"
Cohesion: 0.09
Nodes (30): device_heartbeat(), get_device(), handle_request(), health(), http_exception_handler(), lifespan(), list_devices(), list_tools() (+22 more)

### Community 15 - "App System Docker"
Cohesion: 0.07
Nodes (18): DockerClient, _error_message(), Any, _query_value(), GET an engine endpoint and return its raw body., GET an engine endpoint and parse its JSON body., Docker's query parameters are strings; booleans are ``true``/``false``., Replace the process-wide client. Intended for tests and startup wiring. (+10 more)

### Community 16 - "App Devices Transport"
Cohesion: 0.09
Nodes (19): AgentTransport, LocalTransport, ABC, How FIRDAY would reach a device. Interface only, plus the one implementation…, The result of asking a transport whether a device is reachable., A way to reach a device. Reachability only - nothing executes., Report whether ``device`` is reachable over this transport., The machine FIRDAY is running on. The only real transport in PART 5. (+11 more)

### Community 17 - "App Tools System"
Cohesion: 0.14
Nodes (21): DnsInput, DnsOutput, _first_address(), InterfaceAddress, InterfacesInput, InterfacesOutput, _ip_json(), _last_line() (+13 more)

### Community 18 - "Tests Test System"
Cohesion: 0.10
Nodes (27): ListContainersTool, GitPullTool, Path, The configured nameservers and search domains, best effort., read_resolv_conf(), parse_show(), Parse ``systemctl show`` output into a mapping., parametrize (+19 more)

### Community 19 - "Tests Test System"
Cohesion: 0.13
Nodes (28): GitBranchesTool, GitCloneTool, GitStatusTool, GitTool, A system tool that runs git against a repository inside the sandbox., needs_git, clone_behind_origin(), git() (+20 more)

### Community 20 - "App Core"
Cohesion: 0.12
Nodes (17): FirdayResponse, Plan, PlanStep, BaseModel, Data models for the FIRDAY orchestration flow. Part 1 scope: request in, plan…, One intended tool invocation. Intent only — nothing runs it in Part 1., What a planner decided should happen for a request., What FIRDAY Core returns for a request. (+9 more)

### Community 21 - "App Core"
Cohesion: 0.13
Nodes (14): The outcome of a tool execution. Part 1 emits one of these per planned step…, ToolResult, Look up a tool, returning ``None`` instead of raising., BaseTool, ABC, Any, BaseModel, Protocol (+6 more)

### Community 22 - "Tests Test Devices"
Cohesion: 0.07
Nodes (27): DeviceRegistration, registration(), test_a_device_never_seen_is_stale(), test_a_freshly_seen_device_is_not_stale(), test_a_new_device_starts_unverified_and_unknown(), test_an_unavailable_capability_is_claimed_but_not_usable(), test_device_carries_every_required_field(), test_has_capability_checks_claims() (+19 more)

### Community 23 - "App System Procfs"
Cohesion: 0.13
Nodes (24): boot_time(), clock_ticks(), _iso(), iter_pids(), page_size(), _parse_stat(), ProcessDetail, ProcessInfo (+16 more)

### Community 24 - "Tests Test"
Cohesion: 0.21
Nodes (23): FirdayRequest, A single unit of work submitted to FIRDAY., Core, Receives a request, invokes the planner, returns a response., MockPlanner, Returns a canned, deterministic plan. No reasoning, no LLM calls., Unit tests for the Part 1 orchestration flow (mock planner, no tools)., run() (+15 more)

### Community 25 - "App Tools System"
Cohesion: 0.15
Nodes (20): Class decorator: instantiate a tool and add it to ``default_registry``. This is…, register_tool(), is_available(), True when ``binary`` can be found on PATH., _DeniedServiceTool, _int_or_none(), BaseModel, Service tools: status, start, stop and restart. ``service.status`` asks systemd… (+12 more)

### Community 26 - "App Fs Policy"
Cohesion: 0.13
Nodes (10): FilesystemPolicy, A serializable summary of the sandbox, safe to expose to a caller., Canonicalize ``raw`` and confirm it is inside the sandbox. Returns the…, ``True`` if :meth:`resolve` would accept ``raw``., Resolve for reporting purposes, returning ``None`` when refused., Raise :class:`FileTooLargeError` when ``size`` exceeds ``limit``., The fixed sandbox every filesystem tool resolves its paths against., Path (+2 more)

### Community 27 - "Tests Test Bootstrap"
Cohesion: 0.10
Nodes (12): Replace the process-wide policy. Intended for tests and startup wiring., set_default_policy(), skipif, fixture, Tests for the startup sandbox check. The sandbox is built lazily, so before…, Regression: the Pi reported /data/vault missing from the effective allowed…, Never let a test leak its policy into the process-wide default., restore_default_policy() (+4 more)

### Community 28 - "App Tools Filesystem"
Cohesion: 0.15
Nodes (19): classify(), describe(), FileEntry, _iso(), Shared base class and helpers for the filesystem tools. Every filesystem tool…, Metadata for one path. Symlinks are described, never followed., Build a :class:`FileEntry` from ``lstat`` - the link itself, not its target., ListDirectoryTool (+11 more)

### Community 29 - "App Tools Filesystem"
Cohesion: 0.14
Nodes (19): DeleteInput, DeleteTool, DestructiveFilesystemTool, MoveInput, MoveTool, NotAuthorizedOutput, Any, BaseModel (+11 more)

### Community 30 - "App Devices"
Cohesion: 0.11
Nodes (15): DeviceAlreadyRegisteredError, DeviceError, DeviceNotFoundError, Exception, KeyError, Device management error types. Mirrors :mod:`app.core.errors`: everything…, A device was looked up by an id the registry does not know., An id collision at registration time. (+7 more)

### Community 31 - "Tests Test"
Cohesion: 0.11
Nodes (21): build_default_registry(), Import the built-in tools package and return the populated registry., Enum, str, How far a tool reaches beyond itself., SideEffect, test_all_ten_filesystem_tools_are_registered(), test_destructive_tools_are_still_real_registered_tools() (+13 more)

### Community 32 - "App Tools System"
Cohesion: 0.16
Nodes (16): ProcessNotFoundError, No process with the given PID exists., control_permissions(), Permissions for a tool that only observes., Permissions for a tool that changes system state. Always confirmable., read_permissions(), ProcessInspectInput, ProcessInspectOutput (+8 more)

### Community 33 - "Tests Test System"
Cohesion: 0.14
Nodes (20): TerminateProcessTool, needs_ip, call(), context_for(), The real assertion: the child is spawned, refused, and still running., Execute an already-constructed tool and return its ToolResult., run(), test_a_refusal_is_audited_as_not_authorized() (+12 more)

### Community 34 - "App Fs Errors"
Cohesion: 0.16
Nodes (13): FilesystemPolicyError, FileTooLargeError, OperationNotAuthorizedError, PathNotAllowedError, PathTraversalError, Errors raised by the filesystem policy. All of them derive from ``ToolError``…, A filesystem request was refused before it could touch the disk., A path resolved outside every allowed root. (+5 more)

### Community 35 - "Tests Test System"
Cohesion: 0.15
Nodes (18): PingTool, ListProcessesTool, needs_ping, succeeds(), test_docker_images_lists_images_and_drops_untagged_tags(), test_docker_inspect_describes_a_container(), test_docker_logs_clamps_tail_to_the_configured_cap(), test_docker_logs_returns_demultiplexed_lines() (+10 more)

### Community 36 - "Tests Test Devices"
Cohesion: 0.11
Nodes (19): TailscaleIdentity, identity(), Without Tailscale the answer is 'unverified' - never a substitute check., test_a_different_tailnet_user_is_untrusted(), test_a_tailnet_identity_makes_a_device_trusted(), test_an_unidentifiable_response_leaves_a_device_unverified(), test_describe_is_json_serializable(), test_find_by_name_and_by_tailscale_node() (+11 more)

### Community 37 - "App Tools Filesystem"
Cohesion: 0.20
Nodes (10): A tool failed while running. Tools raise this to report a clean failure., ToolExecutionError, FilesystemTool, Any, BaseModel, Path, Canonicalize and authorize one path, or raise a policy error., Raw paths this invocation is about. Overridden by two-path tools. (+2 more)

### Community 38 - "Tests Test System"
Cohesion: 0.13
Nodes (16): CommandResult, What one external command did., Return self, or raise :class:`CommandFailedError` on a non-zero exit., Run ``argv`` and capture its output. Raises :class:`CommandNotAvailableError`…, run_command(), needs_systemd, Metacharacters are inert because there is no shell to interpret them., A unit that is certainly loaded on any systemd host. (+8 more)

### Community 39 - "App Devices"
Cohesion: 0.15
Nodes (11): datetime, Record an observation: refresh ``last_seen`` and the status., True when we have not observed the device recently enough to be sure., utcnow(), ABC, How a device's trust state is determined and checked. The decision was made…, Explicitly revoke a device's trust. The one way into ``REVOKED``., The outcome of one trust evaluation, with the reason recorded. (+3 more)

### Community 40 - "App Fs"
Cohesion: 0.19
Nodes (14): ensure_sandbox_ready(), _fail(), _prepare_root(), Path, Startup validation for the filesystem sandbox. The sandbox is configured by…, The filesystem sandbox cannot be used as configured. Raised at startup only. It…, Validate the sandbox and return the policy the tools will use. Creates any…, Make one allowed root exist, be a directory, and be writable. (+6 more)

### Community 41 - "App Devices"
Cohesion: 0.14
Nodes (8): Every device matching ``query``, best-first., The best device matching ``query``, or ``None``., DeviceQuery, A filter over devices. Every populated field must match (logical AND)., The common case: a trusted device we believe is reachable right now., A serializable summary, for logging which query produced a choice., What a selection found, and what it was asked for., SelectionResult

### Community 42 - "Tests Test System"
Cohesion: 0.13
Nodes (15): demultiplex(), Turn a Docker log stream into text, unframing it when it is multiplexed. A TTY…, engine(), _framed(), live_child(), policy(), fixture, An allowed root for the tools that resolve paths through the Part 4 policy. (+7 more)

### Community 43 - "Tests Test System"
Cohesion: 0.15
Nodes (11): DnsTool, InspectProcessTool, A PID that is not in use. Racy in principle, stable enough in practice., test_a_successful_system_operation_is_audited_with_the_correlation_id(), test_every_attempt_produces_exactly_one_audit_record(), test_net_dns_reports_a_failed_lookup_without_failing_the_tool(), test_net_dns_resolves_localhost(), test_proc_inspect_describes_this_process() (+3 more)

### Community 44 - "Tests Test Python"
Cohesion: 0.19
Nodes (12): deployed_python_version(), skipif, Guard against the local interpreter drifting from the deployed one. Part 5…, The (major, minor) the Dockerfile builds on., A floating base image would defeat the point of this guard., The interpreter running these tests must be the one that runs in Docker. If…, The declared floor must not drift below what is actually deployed., The exact failure mode the version gap hid. On the deployed interpreter a name… (+4 more)

### Community 45 - "App Tools Filesystem"
Cohesion: 0.23
Nodes (7): CopyInput, CopyOutput, MkdirInput, MkdirOutput, BaseModel, Path, Additive filesystem tools: mkdir and copy. Both only ever add to the…

### Community 46 - "App Tools Filesystem"
Cohesion: 0.27
Nodes (8): PathInput, The common single-path input., BaseModel, File content tools: read and write. Both enforce the policy's size limits…, ReadInput, ReadOutput, WriteInput, WriteOutput

### Community 48 - "App Config"
Cohesion: 0.29
Nodes (8): _float_env(), get_settings(), _int_env(), The effective allowed roots: the workspace root(s) plus the vault. Single…, _roots_env(), Settings, ``fs_all_roots`` is the single source of truth combining the two., test_settings_fs_all_roots_includes_the_vault_root()

### Community 49 - "Docs Firday Handoff"
Cohesion: 0.22
Nodes (10): Docker Socket Mount, Fixed Named-Tool Attack Surface, No Generic Shell Tool (Hard Rule), Part 3: Generic Shell Tool (Skipped), Docker Socket Access and :ro Caveat, git.pull Stays Disabled, Nothing Here Is a Shell, PART 3 Skipped (Generic Shell Tool) (+2 more)

### Community 50 - "App Core Context"
Cohesion: 0.22
Nodes (5): new_request_id(), Request-scoped context threaded through the orchestration flow., Stamps every record with the correlation ID for this request., _RequestLoggerAdapter, LoggerAdapter

### Community 51 - "App Fs Audit"
Cohesion: 0.25
Nodes (5): AuditEvent, Audit trail for filesystem operations. Every attempt - allowed or denied,…, One filesystem operation attempt, as recorded., Log one filesystem attempt and return the record that was written. Denials and…, record_attempt()

### Community 52 - "Requirements Dev"
Cohesion: 0.25
Nodes (9): api Service Definition, httpx, pytest, requirements-dev.txt (dev deps), fastapi, python-dotenv, PyYAML, requirements.txt (runtime deps) (+1 more)

### Community 53 - "App Core Orchestrator"
Cohesion: 0.29
Nodes (6): FirdayRequest, FirdayResponse, RequestContext, Resolve each planned step against the registry without running it. Part 2 goes…, Plan, ToolResult

### Community 54 - "App Tools"
Cohesion: 0.29
Nodes (5): get_default_policy(), The process-wide policy built from settings, created on first use., The sandbox this tool resolves against - the process default unless injected., The Part 4 sandbox - the process default unless one was injected., test_validated_policy_becomes_the_process_default()

### Community 55 - "App Security Engine"
Cohesion: 0.38
Nodes (4): Any, Best-effort extraction of what the tool would act on., Evaluate whether a tool invocation is authorized. Args: tool: The tool being…, Quick authorization check that returns True only for ALLOW decisions. This is…

### Community 57 - "App Logging Config"
Cohesion: 0.40
Nodes (3): configure_logging(), JsonFormatter, LogRecord

### Community 58 - "App Devices Models"
Cohesion: 0.33
Nodes (4): new_device_id(), A device id, in the same shape as Part 1's correlation ids., Build the stored device. Trust stays unverified until evaluated., Best-effort normalization of the many spellings of an OS name. Tailscale…

### Community 59 - "Tests Test Devices"
Cohesion: 0.40
Nodes (6): DeviceRegistry, client(), populated(), fixture, registry(), test_an_unscoped_policy_trusts_any_identified_node()

### Community 60 - "Tests Test Filesystem"
Cohesion: 0.33
Nodes (6): policy(), fixture, An allowed root, with a sibling directory that is deliberately outside it., A policy whose allowed root is '/' - only the system path rules protect it., sandbox(), wide_policy()

### Community 63 - "Tests Test Devices"
Cohesion: 0.67
Nodes (3): parametrize, test_platform_coercion_normalizes_the_many_spellings(), test_remote_transports_are_stubs_that_refuse()

## Knowledge Gaps
- **35 isolated node(s):** `firday`, `PART 0 + 0.5 Foundation Skeleton`, `PART 1 Core Orchestrator`, `Filesystem Audit Trail`, `Sandbox Startup Check (bootstrap.py)` (+30 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 569 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **26 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ToolExecutionContext` connect `App Tools` to `Tests Test Filesystem`, `App Security`, `App System`, `App Tools System`, `Tests Test Tools`, `Tests Test`, `App Tools System`, `Tests Test System`, `Tests Test System`, `App Core`, `App Tools System`, `App Tools Filesystem`, `App Tools Filesystem`, `App Tools System`, `Tests Test System`, `Tests Test System`, `App Tools Filesystem`, `Tests Test System`, `App Tools Filesystem`, `App Tools Filesystem`, `App Core Context`, `App Fs Audit`, `App Security Engine`, `App Logging Config`?**
  _High betweenness centrality (0.120) - this node is a cross-community bridge._
- **Why does `build_local_device()` connect `App Devices` to `Tests Test System`, `App Devices`, `App Devices`, `Tests Test`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Why does `FilesystemPolicy` connect `App Fs Policy` to `App Memory`, `Tests Test Filesystem`, `App Tools Filesystem`, `App Tools`, `App Fs`, `Tests Test System`, `Tests Test Filesystem`, `Tests Test System`, `App Tools`, `Tests Test Bootstrap`, `App Tools Filesystem`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Are the 45 inferred relationships involving `ToolExecutionContext` (e.g. with `BaseTool` and `Tool`) actually correct?**
  _`ToolExecutionContext` has 45 INFERRED edges - model-reasoned connections that need verification._
- **Are the 67 inferred relationships involving `ExecutionStatus` (e.g. with `BaseTool` and `DestructiveFilesystemTool`) actually correct?**
  _`ExecutionStatus` has 67 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `RequestContext` (e.g. with `MockPlanner` and `Planner`) actually correct?**
  _`RequestContext` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `firday`, `PART 0 + 0.5 Foundation Skeleton`, `PART 1 Core Orchestrator` to the rest of the system?**
  _35 weakly-connected nodes found - possible documentation gaps or missing edges._