"""Offline provider used for UI smoke tests and automated tests.

It never pretends to reason. It reports that no model is configured, so it can
never produce a false claim of completed work.
"""

from __future__ import annotations

from typing import Any

from app.providers.base import ChatMessage, LLMProvider, ModelInfo, ProviderResponse, TextCallback


class MockProvider(LLMProvider):
    name = "mock"

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.2,
        on_text: TextCallback | None = None,
    ) -> ProviderResponse:
        tool_names = [tool["function"]["name"] for tool in (tools or []) if "function" in tool]
        text = (
            "**Offline test provider is active.** No language model is connected, so JARVIS "
            "will not attempt to reason about or execute this request.\n\n"
            f"Registered tools available once a model is configured: {len(tool_names)}.\n\n"
            "Open **Settings → AI Provider** and configure Ollama (local) or an "
            "OpenAI-compatible endpoint, then select a model."
        )
        if on_text:
            for chunk in (text[i : i + 48] for i in range(0, len(text), 48)):
                await on_text(chunk)
        return ProviderResponse(content=text, finish_reason="stop", model="mock")

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock", name="Offline test provider (no reasoning)")]
