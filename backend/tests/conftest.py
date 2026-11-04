"""Shared fixtures: isolated data/log directories and a wired AppServices instance."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.container import AppServices
from app.tools.base import ToolContext


@pytest.fixture
def sandbox(tmp_path):
    path = tmp_path / "sandbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def test_settings(tmp_path, sandbox) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'jarvis-test.db').as_posix()}",
        allowed_directories=[sandbox],
        llm_provider="mock",
        llm_model="mock",
        permission_mode="assisted",
        approval_timeout_seconds=5,
        max_steps=8,
        max_retries=0,
        voice_enabled=False,
        browser_headless=True,
    )


@pytest.fixture
async def services(test_settings):
    app_services = AppServices(test_settings)
    await app_services.init()
    try:
        yield app_services
    finally:
        await app_services.shutdown()


@pytest.fixture
async def tool_context(services):
    async with services.db.session() as session:
        yield ToolContext(session=session, task_id="test-task", conversation_id=None, services=services)


@pytest.fixture
async def api(services):
    """HTTP client bound to the real app, sharing the initialized services."""
    import httpx

    from app.main import create_app

    app = create_app(services.app_settings, services=services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

