"""File content tools: read and write.

Both enforce the policy's size limits before touching the disk - a read checks
the file's size first rather than discovering it while loading, and a write
measures the encoded payload (plus what is already there, when appending).
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.errors import ToolExecutionError
from app.core.registry import register_tool
from app.tools.filesystem.base import (
    READ_PERMISSIONS,
    WRITE_PERMISSIONS,
    FilesystemTool,
    PathInput,
)

# --- fs.read ---------------------------------------------------------------


class ReadInput(PathInput):
    encoding: str = Field("utf-8", min_length=1, description="Text encoding to decode with.")
    max_bytes: int | None = Field(
        None, ge=1, description="Lower the read limit for this call. Never raises it."
    )


class ReadOutput(BaseModel):
    path: str
    content: str
    encoding: str
    size_bytes: int
    line_count: int


@register_tool
class ReadFileTool(FilesystemTool):
    name = "fs.read"
    operation = "read"
    description = "Read the contents of a text file inside an allowed root."
    version = "1.0.0"
    permissions = READ_PERMISSIONS
    input_model = ReadInput
    output_model = ReadOutput

    async def operate(self, payload: ReadInput, context: ToolExecutionContext) -> ReadOutput:
        target = self.require_file(self.resolve(payload.path), payload.path)

        limit = self.policy.limits.max_read_bytes
        if payload.max_bytes is not None:
            limit = min(limit, payload.max_bytes)

        size = target.stat().st_size
        self.policy.enforce_size(target, size, limit)

        raw = target.read_bytes()
        try:
            content = raw.decode(payload.encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise ToolExecutionError(
                self.name, f"cannot decode {payload.path!r} as {payload.encoding!r}: {exc}"
            ) from exc

        return ReadOutput(
            path=str(target),
            content=content,
            encoding=payload.encoding,
            size_bytes=size,
            line_count=content.count("\n") + (1 if content and not content.endswith("\n") else 0),
        )


# --- fs.write --------------------------------------------------------------


class WriteInput(PathInput):
    content: str = Field(..., description="Text to write.")
    encoding: str = Field("utf-8", min_length=1)
    mode: Literal["create", "overwrite", "append"] = Field(
        "create",
        description="'create' refuses an existing file; 'overwrite' replaces it; 'append' adds.",
    )
    create_parents: bool = Field(False, description="Create missing parent directories.")


class WriteOutput(BaseModel):
    path: str
    mode: str
    bytes_written: int
    size_bytes: int
    created: bool


@register_tool
class WriteFileTool(FilesystemTool):
    name = "fs.write"
    operation = "write"
    description = "Write or append text to a file inside an allowed root."
    version = "1.0.0"
    permissions = WRITE_PERMISSIONS
    input_model = WriteInput
    output_model = WriteOutput

    async def operate(self, payload: WriteInput, context: ToolExecutionContext) -> WriteOutput:
        target = self.resolve(payload.path)

        if target.is_dir():
            raise ToolExecutionError(self.name, f"{payload.path!r} is a directory")

        existed = target.exists()
        if existed and payload.mode == "create":
            raise ToolExecutionError(
                self.name,
                f"{payload.path!r} already exists; use mode='overwrite' or mode='append'",
            )

        try:
            encoded = payload.content.encode(payload.encoding)
        except (UnicodeEncodeError, LookupError) as exc:
            raise ToolExecutionError(
                self.name, f"cannot encode content as {payload.encoding!r}: {exc}"
            ) from exc

        limit = self.policy.limits.max_write_bytes
        existing_size = target.stat().st_size if existed and payload.mode == "append" else 0
        self.policy.enforce_size(target, existing_size + len(encoded), limit)

        parent = target.parent
        if not parent.exists():
            if not payload.create_parents:
                raise ToolExecutionError(
                    self.name,
                    f"parent directory {str(parent)!r} does not exist; "
                    "pass create_parents=true to create it",
                )
            # `target` is inside a root, so every parent up to that root is too.
            self.resolve(str(parent))
            parent.mkdir(parents=True, exist_ok=True)

        open_mode = "ab" if payload.mode == "append" else "wb"
        with open(target, open_mode) as handle:
            handle.write(encoded)

        return WriteOutput(
            path=str(target),
            mode=payload.mode,
            bytes_written=len(encoded),
            size_bytes=target.stat().st_size,
            created=not existed,
        )
