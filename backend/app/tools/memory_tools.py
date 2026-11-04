"""Memory tools: save/search/list/delete with secret rejection."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import MemoryKind, ToolPermission
from app.tools.base import ToolContext, ToolRegistry, build_tool, fail, ok


class SaveMemoryArgs(BaseModel):
    key: str = Field(description="Short identifier, e.g. 'preferred_download_folder'.")
    value: str = Field(description="The fact or preference to remember. Never credentials.")
    kind: Literal["preference", "fact", "workflow", "website", "instruction"] = "preference"
    tags: str = ""
    pinned: bool = False


class SearchMemoryArgs(BaseModel):
    query: str
    limit: int = Field(default=8, ge=1, le=50)


class ListMemoryArgs(BaseModel):
    kind: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class MemoryIdArgs(BaseModel):
    memory_id: str


def register_memory_tools(registry: ToolRegistry) -> None:
    async def save(args: SaveMemoryArgs, ctx: ToolContext):
        try:
            memory = await ctx.services.memory.add(
                ctx.session,
                kind=args.kind,
                key=args.key,
                value=args.value,
                tags=args.tags,
                source=f"agent:{ctx.task_id}",
                pinned=args.pinned,
            )
        except ValueError as exc:
            return fail(str(exc))
        return ok(
            {"memory_id": memory.id, "key": memory.key, "kind": memory.kind},
            evidence=f"Saved {memory.kind} memory '{memory.key}'",
        )

    async def search(args: SearchMemoryArgs, ctx: ToolContext):
        memories = await ctx.services.memory.search(ctx.session, args.query, args.limit)
        return ok(
            {"memories": [{"id": item.id, "kind": item.kind, "key": item.key, "value": item.value, "pinned": item.pinned} for item in memories]},
            evidence=f"{len(memories)} matching memories",
        )

    async def list_memories(args: ListMemoryArgs, ctx: ToolContext):
        memories = await ctx.services.memory.list(ctx.session, kind=args.kind, limit=args.limit)
        return ok(
            {"memories": [{"id": item.id, "kind": item.kind, "key": item.key, "value": item.value, "pinned": item.pinned} for item in memories]},
            evidence=f"{len(memories)} stored memories",
        )

    async def delete(args: MemoryIdArgs, ctx: ToolContext):
        deleted = await ctx.services.memory.delete(ctx.session, args.memory_id)
        if not deleted:
            return fail("Memory not found.")
        return ok({"deleted": args.memory_id}, evidence="Memory deleted")

    registry.register(build_tool(
        name="memory.save", description="Remember a durable user preference or fact (never secrets).",
        input_model=SaveMemoryArgs, handler=save, permission=ToolPermission.WRITE, timeout=20, tags=("memory",),
    ))
    registry.register(build_tool(
        name="memory.search", description="Search long-term memory by keywords.",
        input_model=SearchMemoryArgs, handler=search, permission=ToolPermission.READ, timeout=20, tags=("memory",),
    ))
    registry.register(build_tool(
        name="memory.list", description="List stored memories, optionally by kind.",
        input_model=ListMemoryArgs, handler=list_memories, permission=ToolPermission.READ, timeout=20, tags=("memory",),
    ))
    registry.register(build_tool(
        name="memory.delete", description="Delete a stored memory. Requires approval.",
        input_model=MemoryIdArgs, handler=delete, permission=ToolPermission.DESTRUCTIVE, timeout=20,
        requires_approval=True, approval_reason="Deleting memory is irreversible.", tags=("memory",),
    ))
