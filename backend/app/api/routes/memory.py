"""Long-term memory CRUD and history search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import services_from

router = APIRouter(tags=["memory"])


class MemoryCreate(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=8000)
    kind: str = "preference"
    tags: str = ""
    pinned: bool = False


class MemoryUpdate(BaseModel):
    key: str | None = None
    value: str | None = None
    kind: str | None = None
    tags: str | None = None
    pinned: bool | None = None


def _serialize(memory: Any) -> dict[str, Any]:
    return {
        "id": memory.id,
        "kind": memory.kind,
        "key": memory.key,
        "value": memory.value,
        "tags": memory.tags,
        "source": memory.source,
        "pinned": memory.pinned,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
    }


@router.get("/api/memory")
async def list_memory(
    request: Request,
    query: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        if query:
            memories = await services.memory.search(session, query, limit=limit)
        else:
            memories = await services.memory.list(session, kind=kind, limit=limit)
        return {"memories": [_serialize(memory) for memory in memories]}


@router.post("/api/memory")
async def create_memory(body: MemoryCreate, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        try:
            memory = await services.memory.add(
                session,
                kind=body.kind,
                key=body.key,
                value=body.value,
                tags=body.tags,
                source="user",
                pinned=body.pinned,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"memory": _serialize(memory)}


@router.patch("/api/memory/{memory_id}")
async def update_memory(memory_id: str, body: MemoryUpdate, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        try:
            memory = await services.memory.update(session, memory_id, **body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if memory is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"memory": _serialize(memory)}


@router.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        deleted = await services.memory.delete(session, memory_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"deleted": memory_id}


@router.get("/api/memory/history/search")
async def search_history(
    request: Request,
    query: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        results = await services.memory.search_history(session, query, limit=limit)
        return {"results": results}
