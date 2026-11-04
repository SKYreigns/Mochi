"""Memory manager: curated long-term memory + FTS search over history."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MemoryKind
from app.core.logging import get_logger, redact
from app.core.security import contains_secret
from app.database.models import Memory, Message

log = get_logger("memory")


class MemoryManager:
    """Stores user-approved, non-sensitive facts and retrieves context."""

    MAX_VALUE_LENGTH = 8000

    async def add(
        self,
        session: AsyncSession,
        *,
        kind: str = MemoryKind.FACT,
        key: str,
        value: str,
        tags: str = "",
        source: str = "user",
        pinned: bool = False,
    ) -> Memory:
        if not key.strip():
            raise ValueError("Memory key is required")
        value = redact(value.strip(), max_length=self.MAX_VALUE_LENGTH)
        if contains_secret(value) or contains_secret(key):
            raise ValueError(
                "This looks like a credential or secret. JARVIS does not store passwords, tokens or API keys in memory."
            )
        if kind not in {item.value for item in MemoryKind}:
            kind = MemoryKind.FACT

        existing = (
            await session.execute(select(Memory).where(Memory.kind == kind, Memory.key == key.strip()))
        ).scalars().first()
        if existing:
            existing.value = value
            existing.tags = tags
            existing.source = source
            existing.pinned = pinned or existing.pinned
            memory = existing
        else:
            memory = Memory(kind=kind, key=key.strip(), value=value, tags=tags, source=source, pinned=pinned)
            session.add(memory)
        await session.flush()
        await self._sync_fts(session, memory)
        await session.commit()
        return memory

    async def _sync_fts(self, session: AsyncSession, memory: Memory) -> None:
        try:
            await session.execute(text("DELETE FROM memories_fts WHERE memory_id = :id"), {"id": memory.id})
            await session.execute(
                text("INSERT INTO memories_fts(key, value, tags, memory_id) VALUES (:key, :value, :tags, :id)"),
                {"key": memory.key, "value": memory.value, "tags": memory.tags, "id": memory.id},
            )
        except Exception as exc:  # pragma: no cover - FTS fallback
            log.warning("Memory FTS sync failed: %s", exc)

    async def search(self, session: AsyncSession, query: str, limit: int = 8) -> list[Memory]:
        query = query.strip()
        if not query:
            return await self.list(session, limit=limit)
        try:
            rows = await session.execute(
                text(
                    """
                    SELECT memory_id FROM memories_fts
                    WHERE memories_fts MATCH :q
                    ORDER BY rank LIMIT :limit
                    """
                ),
                {"q": _fts_query(query), "limit": limit},
            )
            ids = [row[0] for row in rows]
            if not ids:
                return await self._like_search(session, query, limit)
            memories = (await session.execute(select(Memory).where(Memory.id.in_(ids)))).scalars().all()
            order = {memory_id: index for index, memory_id in enumerate(ids)}
            return sorted(memories, key=lambda item: order.get(item.id, 999))
        except Exception as exc:
            log.debug("FTS search unavailable, falling back to LIKE: %s", exc)
            return await self._like_search(session, query, limit)

    async def _like_search(self, session: AsyncSession, query: str, limit: int) -> list[Memory]:
        needle = f"%{query}%"
        rows = await session.execute(
            select(Memory)
            .where(Memory.key.ilike(needle) | Memory.value.ilike(needle) | Memory.tags.ilike(needle))
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def list(self, session: AsyncSession, *, kind: str | None = None, limit: int = 200) -> list[Memory]:
        statement = select(Memory).order_by(Memory.pinned.desc(), Memory.updated_at.desc()).limit(limit)
        if kind:
            statement = statement.where(Memory.kind == kind)
        return list((await session.execute(statement)).scalars().all())

    async def update(self, session: AsyncSession, memory_id: str, **changes: Any) -> Memory | None:
        memory = await session.get(Memory, memory_id)
        if memory is None:
            return None
        for field in ("kind", "key", "value", "tags", "pinned"):
            if field in changes and changes[field] is not None:
                if field == "value" and contains_secret(str(changes[field])):
                    raise ValueError("Secrets cannot be stored in memory.")
                setattr(memory, field, changes[field])
        await session.flush()
        await self._sync_fts(session, memory)
        await session.commit()
        return memory

    async def delete(self, session: AsyncSession, memory_id: str) -> bool:
        memory = await session.get(Memory, memory_id)
        if memory is None:
            return False
        await session.delete(memory)
        try:
            await session.execute(text("DELETE FROM memories_fts WHERE memory_id = :id"), {"id": memory_id})
        except Exception:
            pass
        await session.commit()
        return True

    async def context_for(self, session: AsyncSession, instruction: str, limit: int = 6) -> str:
        pinned = (
            await session.execute(select(Memory).where(Memory.pinned.is_(True)).order_by(Memory.updated_at.desc()).limit(10))
        ).scalars().all()
        relevant = await self.search(session, instruction, limit=limit)
        seen: set[str] = set()
        lines: list[str] = []
        for memory in list(pinned) + relevant:
            if memory.id in seen:
                continue
            seen.add(memory.id)
            lines.append(f"- [{memory.kind}] {memory.key}: {memory.value}")
        return "\n".join(lines)

    async def search_history(self, session: AsyncSession, query: str, limit: int = 10) -> list[dict[str, Any]]:
        try:
            rows = await session.execute(
                text(
                    """
                    SELECT message_id, conversation_id, snippet(messages_fts, 0, '[', ']', '…', 12)
                    FROM messages_fts WHERE messages_fts MATCH :q
                    ORDER BY rank LIMIT :limit
                    """
                ),
                {"q": _fts_query(query), "limit": limit},
            )
            return [
                {"message_id": row[0], "conversation_id": row[1], "snippet": row[2]}
                for row in rows
            ]
        except Exception:
            needle = f"%{query}%"
            messages = (
                await session.execute(
                    select(Message).where(Message.content.ilike(needle)).order_by(Message.created_at.desc()).limit(limit)
                )
            ).scalars().all()
            return [
                {
                    "message_id": message.id,
                    "conversation_id": message.conversation_id,
                    "snippet": message.content[:240],
                }
                for message in messages
            ]

    async def index_message(self, session: AsyncSession, message: Message) -> None:
        if not message.content.strip():
            return
        try:
            await session.execute(
                text(
                    "INSERT INTO messages_fts(content, conversation_id, message_id) "
                    "VALUES (:content, :conversation_id, :message_id)"
                ),
                {
                    "content": message.content,
                    "conversation_id": message.conversation_id,
                    "message_id": message.id,
                },
            )
        except Exception as exc:  # pragma: no cover
            log.debug("Message FTS index failed: %s", exc)


def _fts_query(query: str) -> str:
    terms = [term for term in query.replace('"', " ").split() if term.strip()]
    return " OR ".join(f'"{term}"' for term in terms[:12]) or '""'
