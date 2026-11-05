"""Chat, conversations and the live event WebSocket."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import execution_to_dict, services_from, task_to_dict
from app.database.models import Conversation, Message, ToolExecution, Task

router = APIRouter(tags=["chat"])
ws_router = APIRouter()


class Attachment(BaseModel):
    name: str
    path: str
    mime: str = "application/octet-stream"
    size: int = 0


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    title: str = "New conversation"


@router.get("/api/chat/conversations")
async def list_conversations(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        conversations = (
            await session.execute(select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit))
        ).scalars().all()
        out = []
        for conversation in conversations:
            last = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalars().first()
            count = (
                await session.execute(
                    select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
                )
            ).scalar_one()
            out.append(
                {
                    "id": conversation.id,
                    "title": conversation.title,
                    "created_at": conversation.created_at.isoformat(),
                    "updated_at": conversation.updated_at.isoformat(),
                    "message_count": count,
                    "preview": (last.content[:140] if last else ""),
                }
            )
        return {"conversations": out}


@router.post("/api/chat/conversations")
async def create_conversation(request: Request, body: ConversationCreate) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        conversation = Conversation(user_id="usr_local", title=body.title[:120] or "New conversation")
        session.add(conversation)
        await session.commit()
        return {"id": conversation.id, "title": conversation.title}


@router.get("/api/chat/conversations/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.role.in_(["user", "assistant"]))
                .order_by(Message.created_at.asc())
                .limit(limit)
            )
        ).scalars().all()
        return {
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "created_at": conversation.created_at.isoformat(),
            },
            "messages": [
                {
                    "id": message.id,
                    "task_id": message.task_id,
                    "role": message.role,
                    "content": message.content,
                    "attachments": message.attachments or [],
                    "created_at": message.created_at.isoformat(),
                }
                for message in messages
            ],
        }


@router.get("/api/chat/conversations/{conversation_id}/executions")
async def conversation_executions(
    conversation_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Persisted tool execution history for a conversation.

    Ordered chronologically by start time, then step and id so the frontend can
    rebuild a deterministic timeline and reconcile it with live events.
    """
    services = services_from(request)
    async with services.db.session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        statement = (
            select(ToolExecution)
            .where(ToolExecution.conversation_id == conversation_id)
            .order_by(
                func.coalesce(ToolExecution.started_at, ToolExecution.created_at).asc(),
                ToolExecution.step_index.asc(),
                ToolExecution.id.asc(),
            )
        )
        total = (
            await session.execute(
                select(func.count(ToolExecution.id)).where(
                    ToolExecution.conversation_id == conversation_id
                )
            )
        ).scalar_one()
        rows = (await session.execute(statement.offset(offset).limit(limit))).scalars().all()
        return {
            "conversation_id": conversation_id,
            "total": total,
            "limit": limit,
            "offset": offset,
            "executions": [execution_to_dict(row) for row in rows],
        }


@router.delete("/api/chat/conversations/{conversation_id}")
async def archive_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation.is_archived = True
        await session.commit()
        return {"archived": conversation_id}


@router.post("/api/chat")
async def send_message(request: Request, body: ChatRequest) -> dict[str, Any]:
    services = services_from(request)
    instruction = body.message.strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="Message is empty")

    conversation_id = body.conversation_id
    async with services.db.session() as session:
        conversation = await session.get(Conversation, conversation_id) if conversation_id else None
        if conversation is None:
            conversation = Conversation(user_id="usr_local", title=instruction[:80])
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id
        conversation.updated_at = datetime.now(timezone.utc)
        message = Message(
            conversation_id=conversation_id,
            role="user",
            content=instruction,
            attachments=[item.model_dump() for item in body.attachments],
        )
        session.add(message)
        await session.flush()
        await services.memory.index_message(session, message)
        await session.commit()
        message_id = message.id

    task = await services.tasks.start(
        instruction=instruction,
        conversation_id=conversation_id,
        user_message_id=message_id,
        trigger="manual",
    )
    return {
        "task_id": task.id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "task": task_to_dict(task),
    }


@router.get("/api/chat/tasks/{task_id}")
async def chat_task(task_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"task": task_to_dict(task)}


# --- WebSocket event stream ------------------------------------------------
@ws_router.websocket("/ws/events")
async def events_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    services = websocket.app.state.services
    queue = services.events.subscribe()
    disconnected = asyncio.Event()

    async def watch_disconnect() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
        except Exception:
            pass
        finally:
            disconnected.set()

    watcher = asyncio.create_task(watch_disconnect(), name="ws-watch")
    try:
        await websocket.send_json({"type": "hello", "payload": services.snapshot()})
        for event in services.events.history(80):
            await websocket.send_json(event)
        while not disconnected.is_set():
            getter = asyncio.create_task(queue.get(), name="ws-event-get")
            closer = asyncio.create_task(disconnected.wait(), name="ws-closed-wait")
            done, pending = await asyncio.wait(
                {getter, closer}, timeout=20, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if closer in done or disconnected.is_set():
                getter.cancel()
                break
            if getter not in done:
                await websocket.send_json({"type": "ping", "payload": {}})
                continue
            await websocket.send_json(getter.result())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):
            pass
        services.events.unsubscribe(queue)
