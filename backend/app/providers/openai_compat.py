"""OpenAI-compatible chat-completions provider using raw HTTP + SSE.

Works with OpenAI, OpenRouter, Groq, LM Studio, vLLM, llama.cpp server and any
other endpoint implementing `/chat/completions`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from app.core.errors import ConfigurationError, ProviderError
from app.core.logging import get_logger
from app.providers.base import ChatMessage, LLMProvider, ModelInfo, ProviderResponse, TextCallback, ToolCall

log = get_logger("provider.openai")


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        provider_name: str = "openai_compatible",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = provider_name
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = extra_headers or {}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=20.0))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

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
                "No model is configured for this provider.",
                hint="Open Settings → AI Provider and select or enter a model name.",
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_openai() for message in messages],
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.name == "openrouter":
            payload["stream_options"] = {"include_usage": True}

        content_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason = "stop"
        usage: dict[str, Any] = {}

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:1200]
                    hint = ""
                    if response.status_code in (401, 403):
                        hint = "Check the API key in Settings → AI Provider."
                    elif response.status_code == 404:
                        hint = "Check the base URL and model name; the endpoint or model was not found."
                    elif response.status_code == 429:
                        hint = "The provider is rate-limiting requests. Wait or choose another provider."
                    raise ProviderError(
                        f"LLM request failed ({response.status_code}): {body}",
                        hint=hint,
                        details={"status": response.status_code},
                    )

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or choice.get("message") or {}
                    text = delta.get("content")
                    if text:
                        content_parts.append(text)
                        if on_text:
                            await on_text(text)
                    for tool_call in delta.get("tool_calls") or []:
                        index = int(tool_call.get("index", 0))
                        entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if tool_call.get("id"):
                            entry["id"] = str(tool_call["id"])
                        function = tool_call.get("function") or {}
                        if function.get("name"):
                            entry["name"] = str(function["name"])
                        if function.get("arguments"):
                            entry["arguments"] += str(function["arguments"])
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"Cannot reach the LLM endpoint at {self.base_url}.",
                hint="Verify the base URL and that the local server (e.g. Ollama) is running.",
                details={"error": str(exc)},
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("The LLM request timed out.", details={"error": str(exc)}) from exc

        parsed_calls: list[ToolCall] = []
        for _, entry in sorted(calls.items()):
            if not entry["name"]:
                continue
            arguments: dict[str, Any] = {}
            raw = entry["arguments"].strip()
            if raw:
                try:
                    loaded = json.loads(raw)
                    if isinstance(loaded, dict):
                        arguments = loaded
                    else:
                        arguments = {"value": loaded}
                except json.JSONDecodeError:
                    log.warning("Malformed tool arguments from provider: %s", raw[:400])
                    arguments = {}
            parsed_calls.append(
                ToolCall(
                    id=entry["id"] or f"call_{uuid.uuid4().hex[:12]}",
                    name=entry["name"],
                    arguments=arguments,
                )
            )

        return ProviderResponse(
            content="".join(content_parts),
            tool_calls=parsed_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=self.model,
        )

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=20.0)
            response.raise_for_status()
            data = response.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            models: list[ModelInfo] = []
            for item in items or []:
                if isinstance(item, dict) and item.get("id"):
                    models.append(ModelInfo(id=str(item["id"]), name=str(item.get("name") or item["id"])))
            return sorted(models, key=lambda model: model.id.lower())
        except Exception as exc:
            raise ProviderError(f"Could not list models: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
