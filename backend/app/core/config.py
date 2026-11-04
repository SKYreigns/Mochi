"""Application configuration.

All secrets come from the environment or Windows Credential Manager; nothing
sensitive is stored in this module or in the repository.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
USER_HOME = Path.home()


def _existing(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]


def default_allowed_directories() -> list[Path]:
    """Conservative default sandbox: the user's everyday folders + app data."""
    candidates = [
        USER_HOME / "Downloads",
        USER_HOME / "Documents",
        USER_HOME / "Desktop",
        PROJECT_ROOT / "data" / "downloads",
        PROJECT_ROOT / "data" / "uploads",
        PROJECT_ROOT / "data" / "workspace",
    ]
    return _existing(candidates)


class Settings(BaseSettings):
    """Environment-driven settings. Runtime-editable values live in SQLite."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_file=(str(PROJECT_ROOT / ".env"), str(PROJECT_ROOT / "backend" / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "JARVIS"
    environment: Literal["development", "production", "test"] = "development"
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"

    data_dir: Path = PROJECT_ROOT / "data"
    log_dir: Path = PROJECT_ROOT / "logs"
    database_url: str = ""

    # Agent limits
    permission_mode: Literal["observe", "assisted", "autonomous"] = "assisted"
    max_steps: int = Field(default=30, ge=1, le=200)
    max_retries: int = Field(default=2, ge=0, le=10)
    tool_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    approval_timeout_seconds: float = Field(default=1800.0, gt=0)
    max_upload_mb: int = Field(default=50, ge=1, le=1024)

    # LLM (no model name is hardcoded)
    llm_provider: str = "ollama"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_temperature: float = Field(default=0.2, ge=0, le=2)

    # Browser
    browser_headless: bool = False
    browser_profile_dir: Path | None = None
    screenshots_dir: Path | None = None

    # Filesystem sandbox
    allowed_directories: list[Path] = Field(default_factory=default_allowed_directories)

    # OCR / voice
    tesseract_cmd: str = ""
    voice_enabled: bool = True
    whisper_model: str = "base"
    whisper_device: str = "cpu"

    # Network origins allowed to call the local API
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "tauri://localhost",
            "https://tauri.localhost",
        ]
    )

    @model_validator(mode="after")
    def _finalize(self) -> "Settings":
        self.data_dir = self.data_dir.expanduser().resolve()
        self.log_dir = self.log_dir.expanduser().resolve()
        for sub in ("downloads", "uploads", "workspace", "screenshots", "trash", "browser", "models"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{(self.data_dir / 'jarvis.db').as_posix()}"
        if self.browser_profile_dir is None:
            self.browser_profile_dir = self.data_dir / "browser" / "profile"
        self.browser_profile_dir = Path(self.browser_profile_dir).expanduser().resolve()
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        if self.screenshots_dir is None:
            self.screenshots_dir = self.data_dir / "screenshots"
        self.screenshots_dir = Path(self.screenshots_dir).expanduser().resolve()
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

        if not self.allowed_directories:
            self.allowed_directories = default_allowed_directories()
        self.allowed_directories = [
            Path(p).expanduser().resolve() for p in self.allowed_directories if str(p).strip()
        ]
        # Always keep the application data directories writable.
        for sub in ("downloads", "uploads", "workspace"):
            p = (self.data_dir / sub).resolve()
            if p not in self.allowed_directories:
                self.allowed_directories.append(p)
        return self

    def ensure_data_dirs(self) -> None:
        for sub in ("downloads", "uploads", "workspace", "screenshots", "trash", "browser", "models"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

    @property
    def static_dir(self) -> Path:
        return PROJECT_ROOT / "backend" / "app" / "static"

    def provider_api_key(self, provider: str) -> str | None:
        """Resolve an API key from keyring first, then environment."""
        env_map = {
            "openai": "OPENAI_API_KEY",
            "openai_compatible": "JARVIS_LLM_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        env_name = env_map.get(provider)
        if env_name:
            value = os.environ.get(env_name)
            if value:
                return value
        try:
            import keyring

            value = keyring.get_password("jarvis", f"llm:{provider}")
            if value:
                return value
        except Exception:
            pass
        return os.environ.get("JARVIS_LLM_API_KEY")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    global _settings
    _settings = None
