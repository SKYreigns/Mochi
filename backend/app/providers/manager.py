"""Builds and caches the configured LLM provider."""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.providers.base import LLMProvider, ModelInfo
from app.providers.mock import MockProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai_compat import OpenAICompatibleProvider
from app.services.settings_service import SettingsService

log = get_logger("provider.manager")

_DEFAULT_BASE_URLS = {
    "ollama": "http://127.0.0.1:11434",
    "openai": "https://api.openai.com/v1",
    "openai_compatible": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
}


class ProviderManager:
    def __init__(self, settings: Settings, runtime_settings: SettingsService) -> None:
        self.settings = settings
        self.runtime_settings = runtime_settings
        self._provider: LLMProvider | None = None
        self._signature: tuple[str, str, str, float] | None = None

    def _config(self) -> dict[str, Any]:
        config = self.runtime_settings.llm
        provider = str(config.get("provider") or "ollama").strip().lower()
        return {
            "provider": provider,
            "model": str(config.get("model") or "").strip(),
            "base_url": str(config.get("base_url") or _DEFAULT_BASE_URLS.get(provider, "")).strip(),
            "temperature": float(config.get("temperature", 0.2)),
        }

    def _build(self, config: dict[str, Any]) -> LLMProvider:
        provider = config["provider"]
        if provider == "mock":
            return MockProvider()
        if provider == "ollama":
            return OllamaProvider(base_url=config["base_url"], model=config["model"])
        if provider not in _DEFAULT_BASE_URLS and not config["base_url"]:
            raise ConfigurationError(
                f"Unknown provider '{provider}' with no base URL.",
                hint="Set an OpenAI-compatible base URL in Settings.",
            )
        headers: dict[str, str] = {}
        if provider == "openrouter":
            headers["HTTP-Referer"] = "http://127.0.0.1:8765"
            headers["X-Title"] = "JARVIS"
        return OpenAICompatibleProvider(
            base_url=config["base_url"] or _DEFAULT_BASE_URLS[provider],
            api_key=self.settings.provider_api_key(provider),
            model=config["model"],
            provider_name=provider,
            extra_headers=headers,
        )

    async def get(self) -> LLMProvider:
        config = self._config()
        signature = (config["provider"], config["model"], config["base_url"], config["temperature"])
        if self._provider is not None and signature == self._signature:
            return self._provider
        if self._provider is not None:
            await self._provider.aclose()
        self._provider = self._build(config)
        self._signature = signature
        log.info("Provider ready: %s (%s)", config["provider"], config["model"] or "no model")
        return self._provider

    async def list_models(self, provider: str | None = None, base_url: str | None = None) -> list[ModelInfo]:
        config = self._config()
        if provider:
            config["provider"] = provider.strip().lower()
        if base_url is not None:
            config["base_url"] = base_url.strip()
        if not config["base_url"]:
            config["base_url"] = _DEFAULT_BASE_URLS.get(config["provider"], "")
        provider_obj = self._build(config)
        try:
            return await provider_obj.list_models()
        finally:
            if provider_obj is not self._provider:
                await provider_obj.aclose()

    async def invalidate(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()
        self._provider = None
        self._signature = None

    async def aclose(self) -> None:
        await self.invalidate()

    def status(self) -> dict[str, Any]:
        config = self._config()
        return {
            "provider": config["provider"],
            "model": config["model"],
            "base_url": config["base_url"],
            "configured": bool(config["model"]),
            "has_api_key": bool(self.settings.provider_api_key(config["provider"])),
        }
