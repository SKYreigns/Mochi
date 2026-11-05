"""Async SQLite engine, session factory, schema bootstrap and FTS5 indexes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.logging import get_logger
from app.database.migrations import apply_schema_migrations
from app.database.models import Base, User

log = get_logger("database")

FTS_SCHEMA = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        content, conversation_id UNINDEXED, message_id UNINDEXED, tokenize='unicode61'
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        key, value, tags, memory_id UNINDEXED, tokenize='unicode61'
    )""",
]


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_async_engine(url, echo=False, future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        @event.listens_for(self.engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(apply_schema_migrations)
            for statement in FTS_SCHEMA:
                try:
                    await conn.execute(text(statement))
                except Exception as exc:  # pragma: no cover - FTS5 missing is rare
                    log.warning("FTS5 unavailable: %s", exc)
        async with self.session_factory() as session:
            existing = await session.get(User, "usr_local")
            if existing is None:
                session.add(User(id="usr_local", username="local", display_name="Local User"))
                await session.commit()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def file_size(self) -> int:
        path = self.url.split("///")[-1]
        p = Path(path)
        return p.stat().st_size if p.exists() else 0
