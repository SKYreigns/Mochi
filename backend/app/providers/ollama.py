"""Local Ollama provider (streaming NDJSON, native tool calling)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from app.core.errors import ConfigurationError, ProviderError
from app.providers.base import ChatMessage, LLMProvider, ModelInfo, ProviderResponse, TextCallback, ToolCall


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, *, base_url: str, model: str) -> None:
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=15.0))

    def _message_payload(self, message: ChatMessage) -> dict[str, Any]:
        data: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            data["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.arguments}} for call in message.tool_calls
            ]
        if message.role == "tool":
            data["role"] = "tool"
        return data

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.2,
        on_text: TextCallback | None = None,
    ) -> ProviderResponse:
        if not self.model:
            raise ConfigurationError(
                "No Ollama model is configured.",
                hint="Start Ollama, pull a model, then select it in Settings → AI Provider.",
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_payload(message) for message in messages],
            "stream": True,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools

        content_parts: list[str] = []
        calls: list[ToolCall] = []
        finish_reason = "stop"
        usage: dict[str, Any] = {}

        try:
            async with self._client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:1200]
                    raise ProviderError(
                        f"Ollama request failed ({response.status_code}): {body}",
                        hint="Verify the model is pulled (`ollama list`) and the server is running.",
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise ProviderError(f"Ollama error: {chunk['error']}")
                    message = chunk.get("message") or {}
                    text = message.get("content") or ""
                    if text:
                        content_parts.append(text)
                        if on_text:
                            await on_text(text)
                    for tool_call in message.get("tool_calls") or []:
                        function = tool_call.get("function") or {}
                        name = str(function.get("name") or "")
                        arguments = function.get("arguments") or {}
                        if isinstance(arguments, str):
                            try:
                                arguments = json.loads(arguments)
                            except json.JSONDecodeError:
                                arguments = {}
                        if name:
                            calls.append(
                                ToolCall(
                                    id=str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                                    name=name,
                                    arguments=arguments if isinstance(arguments, dict) else {},
                                )
                            )
                    if chunk.get("done"):
                        finish_reason = "tool_calls" if calls else "stop"
                        if chunk.get("prompt_eval_count") or chunk.get("eval_count"):
                            usage = {
                                "prompt_tokens": chunk.get("prompt_eval_count", 0),
                                "completion_tokens": chunk.get("eval_count", 0),
                            }
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"Ollama is not reachable at {self.base_url}.",
                hint="Install Ollama (https://ollama.com), run `ollama serve`, and configure a model.",
                details={"error": str(exc)},
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("Ollama did not respond in time.", details={"error": str(exc)}) from exc

        return ProviderResponse(
            content="".join(content_parts),
            tool_calls=calls,
            finish_reason="tool_calls" if calls else finish_reason,
            usage=usage,
            model=self.model,
        )

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get(f"{self.base_url}/api/tags", timeout=15.0)
            response.raise_for_status()
            data = response.json()
            models = [
                ModelInfo(
                    id=str(item.get("name") or item.get("model")),
                    name=str(item.get("name") or item.get("model")),
                    size=item.get("size"),
                )
                for item in data.get("models", [])
                if item.get("name") or item.get("model")
            ]
            return sorted(models, key=lambda model: model.id.lower())
        except Exception as exc:
            raise ProviderError(
                f"Could not list Ollama models: {exc}",
                hint="Confirm Ollama is running at the configured base URL.",
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()
