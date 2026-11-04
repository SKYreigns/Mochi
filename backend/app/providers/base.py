"""Provider-neutral chat types and the LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

TextCallback = Callable[[str], Awaitable[None]]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: str
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role}
        if self.role == "assistant" and self.tool_calls:
            data["content"] = self.content or None
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": _json_dumps(call.arguments)},
                }
                for call in self.tool_calls
            ]
        else:
            data["content"] = self.content
        if self.role == "tool":
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data


class ProviderResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, Any] = Field(default_factory=dict)
    model: str = ""


class ModelInfo(BaseModel):
    id: str
    name: str
    size: int | None = None


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.2,
        on_text: TextCallback | None = None,
    ) -> ProviderResponse:
        ...

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def aclose(self) -> None:
        return None


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
