"""Test helpers: scripted providers and task polling."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.database.models import Task
from app.providers.base import ChatMessage, LLMProvider, ProviderResponse, ToolCall, TextCallback

TERMINAL = {"completed", "failed", "cancelled", "blocked"}


class ScriptedProvider(LLMProvider):
    """Deterministic provider: plan requests get JSON, agent requests get a script.

    Each scripted item is either a ProviderResponse, an exception instance, or a
    callable that receives the message list and returns a ProviderResponse.
    """

    name = "scripted"

    def __init__(self, responses: list[Any], *, plan: dict[str, Any] | None = None, fail_plan: bool = False):
        self.responses = list(responses)
        self.plan = plan or {"objective": "Test objective", "steps": [{"title": "Use a tool"}]}
        self.fail_plan = fail_plan
        self.plan_calls = 0
        self.agent_calls = 0
        self.messages_seen: list[list[ChatMessage]] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.2,
        on_text: TextCallback | None = None,
    ) -> ProviderResponse:
        if tools is None:
            self.plan_calls += 1
            if self.fail_plan:
                raise RuntimeError("planner unavailable")
            return ProviderResponse(content=json.dumps(self.plan), finish_reason="stop")
        self.agent_calls += 1
        self.messages_seen.append(list(messages))
        if not self.responses:
            return ProviderResponse(content="No further scripted response.", finish_reason="stop")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = await item(messages)
        if on_text and item.content:
            await on_text(item.content)
        return item

    async def list_models(self):
        return []


def tool_response(*calls: ToolCall, content: str = "") -> ProviderResponse:
    return ProviderResponse(content=content, tool_calls=list(calls), finish_reason="tool_calls")


async def wait_for_task(services, task_id: str, timeout: float = 20.0) -> Task:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with services.db.session() as session:
            task = await session.get(Task, task_id)
            if task is not None and task.status in TERMINAL:
                return task
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"task {task_id} did not finish in {timeout}s")
        await asyncio.sleep(0.05)


async def wait_for_task_status(services, task_id: str, statuses: set[str], timeout: float = 10.0) -> Task:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with services.db.session() as session:
            task = await session.get(Task, task_id)
            if task is not None and task.status in statuses:
                return task
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"task {task_id} never reached {statuses}")
        await asyncio.sleep(0.02)


async def install_provider(services, provider: LLMProvider) -> None:
    async def _get() -> LLMProvider:
        return provider

    services.provider_manager.get = _get  # type: ignore[method-assign]


async def wait_until(predicate, timeout: float = 10.0, interval: float = 0.02) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met in time")
        await asyncio.sleep(interval)
