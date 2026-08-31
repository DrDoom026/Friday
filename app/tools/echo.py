"""The echo tool: a harmless demonstration of the Part 2 tool framework.

It reads nothing, writes nothing and touches no network. Its only job is to
prove the interface, the schemas, validation and the registry all work.
"""

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.registry import register_tool
from app.core.tools import BaseTool, SideEffect, ToolPermissions


class EchoInput(BaseModel):
    message: str = Field(..., min_length=1, description="Text to echo back.")


class EchoOutput(BaseModel):
    message: str = Field(..., description="The text that was sent in.")
    length: int = Field(..., description="Character count of the echoed text.")


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
