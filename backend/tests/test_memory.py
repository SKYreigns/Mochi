"""Long-term memory: CRUD, FTS search, secret rejection and persistence."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.container import AppServices
from app.database.models import Conversation, Message


async def test_create_and_retrieve_memory(services):
    async with services.db.session() as session:
        memory = await services.memory.add(
            session, kind="preference", key="download_folder", value="Use Downloads for PDFs", tags="files"
        )
        assert memory.id
        found = await services.memory.search(session, "Downloads")
        assert any(item.id == memory.id for item in found)
        listed = await services.memory.list(session)
        assert any(item.id == memory.id for item in listed)


async def test_update_memory(services):
    async with services.db.session() as session:
        memory = await services.memory.add(session, kind="fact", key="deadline", value="Friday")
        updated = await services.memory.update(session, memory.id, value="Monday", pinned=True)
        assert updated is not None
        assert updated.value == "Monday" and updated.pinned is True
        found = await services.memory.search(session, "Monday")
        assert any(item.id == memory.id for item in found)


async def test_delete_memory_removes_fts_entry(services):
    async with services.db.session() as session:
        memory = await services.memory.add(session, kind="fact", key="temp", value="ephemeral zebra")
        assert await services.memory.delete(session, memory.id) is True
        assert not await services.memory.search(session, "zebra")
        assert await services.memory.delete(session, memory.id) is False


async def test_fts_search_matches_words_and_falls_back(services):
    async with services.db.session() as session:
        await services.memory.add(session, kind="fact", key="project", value="Project Aurora launches in spring")
        matches = await services.memory.search(session, "Aurora")
        assert any("Aurora" in item.value for item in matches)
        fallback = await services.memory.search(session, "spring")
        assert any("spring" in item.value for item in fallback)


async def test_secrets_are_rejected(services):
    async with services.db.session() as session:
        with pytest.raises(ValueError, match="credential|secret"):
            await services.memory.add(session, kind="fact", key="token", value="api_key=sk-abcdefghijklmnopqrstuvwxyz")

        memory = await services.memory.add(session, kind="fact", key="safe", value="prefers dark mode")
        with pytest.raises(ValueError, match="Secrets"):
            await services.memory.update(session, memory.id, value="password = hunter2")


async def test_context_includes_pinned_and_relevant(services):
    async with services.db.session() as session:
        await services.memory.add(session, kind="preference", key="style", value="Use concise bullet points", pinned=True)
        await services.memory.add(session, kind="fact", key="subject", value="Physics assignment due Friday")
        context = await services.memory.context_for(session, "Help me with the physics assignment", limit=5)
        assert "concise bullet points" in context
        assert "Physics" in context


async def test_history_search(services):
    async with services.db.session() as session:
        conversation = Conversation(user_id="usr_local", title="Physics")
        session.add(conversation)
        await session.flush()
        message = Message(conversation_id=conversation.id, role="user", content="Find the thermodynamics notes")
        session.add(message)
        await session.flush()
        await services.memory.index_message(session, message)
        await session.commit()
        results = await services.memory.search_history(session, "thermodynamics")
        assert results and results[0]["message_id"] == message.id


async def test_memory_survives_restart(tmp_path, sandbox):
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "persist-data",
        log_dir=tmp_path / "persist-logs",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'persist.db').as_posix()}",
        allowed_directories=[sandbox],
        llm_provider="mock",
        voice_enabled=False,
    )
    first = AppServices(settings)
    await first.init()
    try:
        async with first.db.session() as session:
            await first.memory.add(session, kind="fact", key="persisted", value="Memory survives restarts")
    finally:
        await first.shutdown()

    second = AppServices(settings)
    await second.init()
    try:
        async with second.db.session() as session:
            results = await second.memory.search(session, "restarts")
            assert results and results[0].value == "Memory survives restarts"
    finally:
        await second.shutdown()
