"""Tool primitives: typed results, execution context, registry.

Every capability exposed to the LLM is a Tool with a Pydantic input schema, a
permission class, a timeout and a structured result. The model never calls
Python directly.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ToolPermission
from app.core.errors import JarvisError
from app.core.logging import get_logger, redact_value

log = get_logger("tools")


class EmptyArgs(BaseModel):
    """Tools that take no arguments."""


class ToolResult(BaseModel):
    ok: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    hint: str | None = None
    verified: bool = False
    evidence: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    retryable: bool = False

    def as_message(self) -> str:
        import json

        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, default=str)


def ok(
    data: dict[str, Any] | None = None,
    *,
    evidence: str = "",
    verified: bool = True,
    artifacts: list[dict[str, Any]] | None = None,
) -> ToolResult:
    return ToolResult(
        ok=True,
        data=data or {},
        evidence=evidence,
        verified=verified,
        artifacts=artifacts or [],
    )


def fail(error: str, *, hint: str = "", retryable: bool = False) -> ToolResult:
    return ToolResult(ok=False, error=error, hint=hint, retryable=retryable)


@dataclass(slots=True)
class ToolContext:
    session: AsyncSession
    task_id: str
    conversation_id: str | None
    services: Any  # AppServices container (avoids a circular import)
    auto_approved: set[str] = field(default_factory=set)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        await self.services.events.publish(event_type, payload)


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    permission: ToolPermission = ToolPermission.READ
    timeout: float = 60.0
    requires_approval: bool = False
    approval_reason: str = ""
    retryable: bool = False
    tags: tuple[str, ...] = ()
    enabled: bool = True

    def schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def spec(self) -> dict[str, Any]:
        schema = self.schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "requires_approval": self.requires_approval,
            "timeout": self.timeout,
            "tags": list(self.tags),
            "schema": self.schema(),
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool_def: Tool) -> None:
        if tool_def.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool_def.name}")
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool_def = self._tools.get(name)
        if tool_def is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool_def

    def all(self) -> list[Tool]:
        return [tool for tool in self._tools.values() if tool.enabled]

    def names(self) -> list[str]:
        return sorted(tool.name for tool in self._tools.values())

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values() if tool.enabled]

    def catalog(self) -> list[dict[str, Any]]:
        return [tool.summary() for tool in sorted(self._tools.values(), key=lambda item: item.name)]

    async def execute(self, name: str, raw_arguments: dict[str, Any], ctx: ToolContext) -> tuple[ToolResult, float]:
        started = time.perf_counter()
        tool_def = self._tools.get(name)
        if tool_def is None:
            return fail(f"Unknown tool '{name}'."), 0.0
        try:
            arguments = tool_def.input_model.model_validate(raw_arguments or {})
        except ValidationError as exc:
            return (
                fail(
                    f"Invalid arguments for '{name}': {exc.errors(include_url=False)}",
                    hint="The tool was not executed. Correct the arguments and try again.",
                ),
                0.0,
            )

        try:
            result = await asyncio.wait_for(tool_def.handler(arguments, ctx), timeout=tool_def.timeout)
            if not isinstance(result, ToolResult):
                result = ok({"result": result})
        except asyncio.TimeoutError:
            result = fail(
                f"Tool '{name}' timed out after {tool_def.timeout:.0f}s.",
                hint="The operation may still be running on the system; inspect the target before retrying.",
                retryable=False,
            )
        except JarvisError as exc:
            result = fail(exc.message, hint=exc.hint, retryable=exc.retryable)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - tool isolation boundary
            log.exception("Tool '%s' crashed", name)
            result = fail(
                f"Tool '{name}' failed: {exc.__class__.__name__}: {exc}",
                hint="Inspect the tool arguments and system state before retrying.",
            )
        duration = (time.perf_counter() - started) * 1000
        if result.data:
            result.data = redact_value(result.data)
        return result, duration


def build_tool(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    handler: ToolHandler,
    permission: ToolPermission = ToolPermission.READ,
    timeout: float = 60.0,
    requires_approval: bool = False,
    approval_reason: str = "",
    retryable: bool = False,
    tags: tuple[str, ...] = (),
) -> Tool:
    if not inspect.iscoroutinefunction(handler):
        raise TypeError(f"Tool handler for '{name}' must be async")
    return Tool(
        name=name,
        description=description,
        input_model=input_model,
        handler=handler,
        permission=permission,
        timeout=timeout,
        requires_approval=requires_approval,
        approval_reason=approval_reason,
        retryable=retryable,
        tags=tags,
    )
