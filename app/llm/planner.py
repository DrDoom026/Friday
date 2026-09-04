"""PART 9 real LLM planner.

Implements the PART 1 :class:`~app.core.planner.Planner` protocol. Produces a
structured :class:`~app.core.models.Plan` only - it never touches the tool
registry, the Security Engine, or a tool's ``execute``. FIRDAY Core remains
the only caller of those.

Flow: privacy gate -> memory retrieval -> cloud LLM -> structured plan.
"""

import json
import logging
from typing import Any

from app.core.context import RequestContext
from app.core.models import ExecutionStatus, FirdayRequest, Plan, PlanStep, ToolResult
from app.core.registry import ToolRegistry
from app.llm.errors import LLMProviderError
from app.llm.privacy import is_sensitive, redact
from app.llm.providers import OllamaClient, OmniRouteClient
from app.memory.service import MemoryService

logger = logging.getLogger("firday.llm.planner")

_INTENT_CATEGORIES = ("tool_request", "chat", "question")

_SYSTEM_PROMPT = (
    "You are FIRDAY's planner. Given a user request and optional memory context, "
    "decide whether a tool should run. Reply with ONLY a JSON object of the form "
    '{"tool_name": <tool name string or null>, "arguments": <object>, "summary": '
    "<one sentence>}. Never invent a tool name that was not listed. Do not include "
    "any text outside the JSON object.\n\n"
    "If tool_name is null, this is a plain conversational reply and \"summary\" MUST "
    "be the exact, final message to send the user - written directly to them, in "
    "natural language, ready to send as-is. It must NOT be an instruction, an action "
    "description, a plan, or a third-person description (never \"say X\", \"tell the "
    "user X\", or \"respond that X\").\n"
    'User: hello -> {"tool_name": null, "arguments": {}, "summary": "Hello! How can '
    'I help you today?"} (NOT "Greet the user with a friendly hello.")\n'
    'User: how are you? -> {"tool_name": null, "arguments": {}, "summary": "I\'m '
    'doing well. What can I help you with?"} (NOT "Tell the user you are doing '
    "well.\")\n"
    "If tool_name is not null, \"summary\" remains a concise description of the "
    "planned tool action, as before."
)


class LLMPlanner:
    """Wires the local classifier, memory, and cloud router into one planner."""

    name = "llm"

    def __init__(
        self,
        cloud: OmniRouteClient,
        registry: ToolRegistry,
        *,
        local: OllamaClient | None = None,
        memory: MemoryService | None = None,
        memory_top_k: int = 3,
        max_context_chars: int = 4000,
    ) -> None:
        self._cloud = cloud
        self._registry = registry
        self._local = local
        self._memory = memory
        self._memory_top_k = memory_top_k
        self._max_context_chars = max_context_chars

    async def plan(self, request: FirdayRequest, context: RequestContext) -> Plan:
        log = context.logger("firday.llm.planner")

        if is_sensitive(request.input):
            log.warning("request blocked from cloud routing by privacy gate")
            return Plan(
                planner_name=self.name,
                summary=(
                    "This request contains private information (a filesystem path, "
                    "credential, or secret) and was not sent to a cloud model."
                ),
            )

        intent = "unknown"
        if self._local is not None:
            intent = await self._local.classify_intent(request.input, _INTENT_CATEGORIES)
        log.info("local intent classification (intent=%s)", intent)

        memory_context = await self._retrieve_memory(request, context)

        messages = self._build_messages(request, memory_context)
        try:
            raw = await self._cloud.complete(messages)
        except LLMProviderError as exc:
            log.warning("cloud planning call failed: %s", exc)
            return Plan(
                planner_name=self.name,
                summary="The planning model is currently unavailable. Please try again later.",
            )

        return self._parse_plan(raw, log)

    async def finalize(self, results: list[ToolResult], context: RequestContext) -> str:
        """Turn tool results into a final natural-language response.

        Tool Result -> Output Privacy Filter -> LLM -> Final Response. Every
        result is redacted before it can reach the cloud model.
        """
        log = context.logger("firday.llm.planner")
        filtered = [self._filter_result(result) for result in results]

        messages = [
            {
                "role": "system",
                "content": (
                    "Summarize these tool results for the user in one or two short "
                    "sentences. Do not invent information not present in the results."
                ),
            },
            {"role": "user", "content": json.dumps(filtered)[: self._max_context_chars]},
        ]
        try:
            return (await self._cloud.complete(messages)).strip()
        except LLMProviderError as exc:
            log.warning("cloud finalize call failed: %s", exc)
            return "; ".join(
                f"{item['tool_name']}: {item['status']}" for item in filtered
            ) or "No tool results to report."

    # --- internals -----------------------------------------------------

    async def _retrieve_memory(
        self, request: FirdayRequest, context: RequestContext
    ) -> list[str]:
        """Relevant memory snippets only - never the whole vault. Fails safe."""
        if self._memory is None:
            return []
        try:
            # Part 8's search is a literal substring filter, not semantic
            # ranking, so matching the whole free-form request rarely hits.
            # Pull the most recent notes across categories instead and cap
            # to top_k - still never the whole vault.
            notes = await self._memory.search(request=context)
            notes = sorted(notes, key=lambda n: n.updated_at, reverse=True)
        except Exception:
            logger.warning("memory retrieval failed; continuing without memory context")
            return []
        return [redact(note.body)[:500] for note in notes[: self._memory_top_k]]

    def _build_messages(
        self, request: FirdayRequest, memory_context: list[str]
    ) -> list[dict[str, str]]:
        tools = [
            f"{tool.name}: {tool.description}" for tool in self._registry
        ]
        system = _SYSTEM_PROMPT + "\n\nAvailable tools:\n" + "\n".join(tools)
        if memory_context:
            system += "\n\nRelevant memory:\n" + "\n".join(memory_context)
        return [
            {"role": "system", "content": system[: self._max_context_chars]},
            {"role": "user", "content": request.input},
        ]

    def _parse_plan(self, raw: str, log: logging.LoggerAdapter) -> Plan:
        try:
            data: Any = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("response is not a JSON object")
            summary = str(data.get("summary") or "")
            tool_name = data.get("tool_name")
            arguments = data.get("arguments") or {}
            if tool_name is not None and not isinstance(tool_name, str):
                raise ValueError("tool_name must be a string or null")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("malformed LLM response, no action taken: %s", exc)
            return Plan(
                planner_name=self.name,
                summary="The planner returned an unrecognized response; no action was taken.",
            )

        if not tool_name:
            return Plan(planner_name=self.name, summary=summary or "Acknowledged.")

        # An unknown tool name is not a malformed response - Core's registry
        # lookup already turns it into a structured ERROR result without
        # executing anything, so no duplicate check is needed here.
        return Plan(
            planner_name=self.name,
            summary=summary or f"Run {tool_name}.",
            steps=[PlanStep(tool_name=tool_name, arguments=arguments, rationale=summary)],
        )

    @staticmethod
    def _filter_result(result: ToolResult) -> dict[str, Any]:
        output = result.output
        if output is not None:
            output = redact(json.dumps(output, default=str))
        return {
            "tool_name": result.tool_name,
            "status": result.status.value
            if isinstance(result.status, ExecutionStatus)
            else str(result.status),
            "output": output,
            "error": redact(result.error) if result.error else None,
        }
