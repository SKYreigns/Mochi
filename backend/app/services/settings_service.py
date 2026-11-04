"""Runtime settings persisted in SQLite (never secrets)."""

from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.events import EventBus
from app.core.logging import get_logger
from app.database.models import ApplicationSetting

log = get_logger("settings")

DEFAULT_LMS_SELECTORS: dict[str, str] = {
    "login_indicator": "",
    "course_links": "",
    "assignment_links": "",
    "assignment_title": "",
    "assignment_due": "",
    "assignment_description": "",
    "attachments": "",
    "submit_button": "",
    "submission_receipt": "",
}


class SettingsService:
    """Layered settings: environment defaults → SQLite overrides → live cache."""

    def __init__(self, app_settings: Settings, events: EventBus) -> None:
        self._app = app_settings
        self._events = events
        self._cache: dict[str, Any] = self._defaults()
        self._loaded = False

    def _defaults(self) -> dict[str, Any]:
        app = self._app
        return {
            "permission_mode": app.permission_mode,
            "llm": {
                "provider": app.llm_provider,
                "model": app.llm_model,
                "base_url": app.llm_base_url,
                "temperature": app.llm_temperature,
            },
            "allowed_directories": [str(p) for p in app.allowed_directories],
            "browser": {"headless": app.browser_headless},
            "voice": {
                "enabled": app.voice_enabled,
                "whisper_model": app.whisper_model,
                "whisper_device": app.whisper_device,
                "push_to_talk": True,
                "speak_responses": True,
                "wake_word": "",
            },
            "lms": {
                "base_url": "",
                "institution": "",
                "courses": [],
                "download_dir": str(app.data_dir / "downloads" / "lms"),
                "document_format": "pdf",
                "selectors": dict(DEFAULT_LMS_SELECTORS),
            },
            "safety": {
                "auto_approve_tools": [],
                "confirm_destructive": True,
                "blocked_websites": [],
            },
            "app": {"onboarded": False},
        }

    async def load(self, session: AsyncSession) -> None:
        rows = (await session.execute(select(ApplicationSetting))).scalars().all()
        merged = self._defaults()
        for row in rows:
            if isinstance(row.value, dict) and isinstance(merged.get(row.key), dict):
                merged[row.key] = _deep_merge(merged[row.key], row.value)
            else:
                merged[row.key] = row.value
        self._cache = merged
        self._loaded = True

    @property
    def app(self) -> Settings:
        return self._app

    def get(self, key: str, default: Any = None) -> Any:
        value: Any = self._cache
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return copy.deepcopy(value)

    async def set(self, session: AsyncSession, key: str, value: Any) -> None:
        row = await session.get(ApplicationSetting, key)
        if row is None:
            row = ApplicationSetting(key=key, value=value)
            session.add(row)
        else:
            row.value = value
        await session.commit()
        top = key.split(".")[0]
        if isinstance(value, dict):
            current = self._cache.get(top)
            base = current if isinstance(current, dict) else self._defaults().get(top, {})
            self._cache[top] = _deep_merge(base, value)
        else:
            self._cache[key] = value
            if "." in key:
                top_key, sub = key.split(".", 1)
                current = self._cache.get(top_key)
                if isinstance(current, dict):
                    current[sub] = value
        await self._events.publish("settings.updated", {"key": key})
        log.info("Setting updated: %s", key)

    async def replace(self, session: AsyncSession, values: dict[str, Any]) -> None:
        for key, value in values.items():
            await self.set(session, key, value)

    # Typed accessors -----------------------------------------------------
    @property
    def permission_mode(self) -> str:
        return str(self._cache.get("permission_mode", "assisted"))

    @property
    def llm(self) -> dict[str, Any]:
        return dict(self._cache.get("llm", {}))

    @property
    def allowed_directories(self) -> list[str]:
        return list(self._cache.get("allowed_directories", []))

    @property
    def lms(self) -> dict[str, Any]:
        return dict(self._cache.get("lms", {}))

    @property
    def voice(self) -> dict[str, Any]:
        return dict(self._cache.get("voice", {}))

    @property
    def safety(self) -> dict[str, Any]:
        return dict(self._cache.get("safety", {}))

    @property
    def browser_headless(self) -> bool:
        return bool(self._cache.get("browser", {}).get("headless", False))

    def public_snapshot(self) -> dict[str, Any]:
        """Settings safe to send to the UI (no secrets, ever)."""
        snapshot = copy.deepcopy(self._cache)
        snapshot["llm"]["has_api_key"] = bool(
            self._app.provider_api_key(str(snapshot["llm"].get("provider", "")))
        )
        snapshot["runtime"] = {
            "data_dir": str(self._app.data_dir),
            "log_dir": str(self._app.log_dir),
            "host": self._app.host,
            "port": self._app.port,
            "version": "1.0.0",
            "permission_mode": self.permission_mode,
        }
        return snapshot

    def validate_allowed_directory(self, raw: str) -> str:
        path = self._app.data_dir if not raw else None
        candidate = raw.strip()
        if not candidate:
            raise ValueError("Directory path is empty")
        parsed = urlparse(candidate)
        if parsed.scheme and parsed.scheme not in ("file",):
            raise ValueError("Only local filesystem paths are allowed")
        return candidate


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
