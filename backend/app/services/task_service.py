"""Task lifecycle manager: spawn, track, cancel and stop agent runs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.enums import TaskStatus
from app.core.events import EventBus
from app.core.logging import audit, get_logger
from app.database.database import Database
from app.database.models import Conversation, Message, Task, ToolExecution
from app.orchestrator.agent import AgentOrchestrator

log = get_logger("tasks")

_TERMINAL = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.BLOCKED.value,
}


class TaskService:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.orchestrator: AgentOrchestrator = services.orchestrator
        self._running: dict[str, asyncio.Task[None]] = {}

    @property
    def db(self) -> Database:
        return self.services.db

    @property
    def events(self) -> EventBus:
        return self.services.events

    async def start(
        self,
        *,
        instruction: str,
        conversation_id: str | None = None,
        trigger: str = "manual",
        user_message_id: str | None = None,
        auto_approved: set[str] | None = None,
        title: str | None = None,
    ) -> Task:
        auto_approved = auto_approved or set()
        async with self.db.session() as session:
            if not conversation_id:
                conversation = Conversation(
                    user_id="usr_local",
                    title=(title or instruction.strip())[:80] or "JARVIS task",
                )
                session.add(conversation)
                await session.flush()
                conversation_id = conversation.id
                message = Message(conversation_id=conversation_id, role="user", content=instruction)
                session.add(message)
                await session.flush()
                await self.services.memory.index_message(session, message)
                user_message_id = message.id
            task = Task(
                conversation_id=conversation_id,
                instruction=instruction,
                status=TaskStatus.QUEUED.value,
                permission_mode=self.services.settings.permission_mode,
                trigger=trigger,
                metadata_json={},
            )
            session.add(task)
            await session.flush()
            task_id = task.id
            if user_message_id:
                message = await session.get(Message, user_message_id)
                if message is not None:
                    message.task_id = task_id
            await session.commit()

        worker = asyncio.create_task(
            self._runner(
                task_id,
                instruction,
                conversation_id,
                user_message_id,
                auto_approved,
            ),
            name=f"jarvis-task-{task_id}",
        )
        self._running[task_id] = worker

        def _cleanup(finished: asyncio.Task[None]) -> None:
            self._running.pop(task_id, None)
            if not finished.cancelled() and finished.exception():
                log.error("Task %s worker raised: %s", task_id, finished.exception())

        worker.add_done_callback(_cleanup)
        await self.events.publish(
            "task.queued",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "instruction": instruction,
                "trigger": trigger,
            },
        )
        async with self.db.session() as session:
            stored = await session.get(Task, task_id)
            assert stored is not None
            return stored

    async def _runner(
        self,
        task_id: str,
        instruction: str,
        conversation_id: str,
        user_message_id: str | None,
        auto_approved: set[str],
    ) -> None:
        try:
            async with self.db.session() as session:
                await self.orchestrator.run(
                    session,
                    task_id=task_id,
                    instruction=instruction,
                    conversation_id=conversation_id,
                    user_message_id=user_message_id,
                    auto_approved=auto_approved,
                )
        except asyncio.CancelledError:
            await self._ensure_cancelled(task_id)
            raise
        except Exception:
            log.exception("Unhandled task worker error for %s", task_id)

    async def _ensure_cancelled(self, task_id: str) -> None:
        try:
            async with self.db.session() as session:
                task = await session.get(Task, task_id)
                if task is not None and task.status not in _TERMINAL:
                    task.status = TaskStatus.CANCELLED.value
                    task.cancelled_at = datetime.now(timezone.utc)
                    task.error = task.error or "Cancelled."
                    await session.commit()
        except Exception as exc:  # pragma: no cover
            log.warning("Could not mark task %s cancelled: %s", task_id, exc)

    async def run_and_wait(
        self,
        *,
        instruction: str,
        trigger: str = "workflow",
        auto_approved: set[str] | None = None,
        title: str | None = None,
    ) -> Task:
        task = await self.start(
            instruction=instruction,
            trigger=trigger,
            auto_approved=auto_approved,
            title=title,
        )
        worker = self._running.get(task.id)
        if worker is not None:
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        async with self.db.session() as session:
            refreshed = await session.get(Task, task.id)
            assert refreshed is not None
            return refreshed

    async def cancel(self, task_id: str) -> bool:
        worker = self._running.get(task_id)
        if worker is not None and not worker.done():
            worker.cancel()
            async with self.db.session() as session:
                await self.services.permissions.cancel_task_approvals(session, task_id)
            await self.events.publish("task.cancel_requested", {"task_id": task_id})
            return True
        async with self.db.session() as session:
            task = await session.get(Task, task_id)
            if task is not None and task.status not in _TERMINAL:
                task.status = TaskStatus.CANCELLED.value
                task.cancelled_at = datetime.now(timezone.utc)
                await session.commit()
                await self.events.publish("task.cancelled", {"task_id": task_id, "status": "cancelled"})
                return True
        return False

    async def cancel_all(self) -> int:
        task_ids = list(self._running)
        for task_id in task_ids:
            await self.cancel(task_id)
        await self.services.permissions.cancel_all_waiters()
        return len(task_ids)

    async def emergency_stop(self) -> dict[str, Any]:
        self.services.emergency.engage()
        cancelled = await self.cancel_all()
        await self.events.publish("emergency.stop", {"cancelled_tasks": cancelled})
        audit("emergency.stop", cancelled_tasks=cancelled)
        return {"stopped": True, "cancelled_tasks": cancelled}

    async def resume(self) -> dict[str, Any]:
        self.services.emergency.clear()
        await self.events.publish("emergency.cleared", {})
        audit("emergency.cleared")
        return {"stopped": False}

    def running_ids(self) -> list[str]:
        return [task_id for task_id, worker in self._running.items() if not worker.done()]

    async def recent(self, limit: int = 50) -> list[Task]:
        async with self.db.session() as session:
            rows = await session.execute(select(Task).order_by(Task.created_at.desc()).limit(limit))
            return list(rows.scalars().all())

    async def recover_stale_state(self) -> int:
        """Mark work left behind by a previous backend process.

        A task cannot still be executing after the process that owned it exited.
        Leaving it as ``running`` would make the UI wait forever and would hide
        the interrupted execution from conversation history.
        """
        now = datetime.now(timezone.utc)
        message = "Mochi restarted before this task finished."
        async with self.db.session() as session:
            tasks = (
                await session.execute(select(Task).where(Task.status.not_in(_TERMINAL)))
            ).scalars().all()
            for task in tasks:
                task.status = TaskStatus.FAILED.value
                task.error = task.error or message
                task.completed_at = now
            executions = (
                await session.execute(
                    select(ToolExecution).where(
                        ToolExecution.status.in_(["running", "pending_approval"])
                    )
                )
            ).scalars().all()
            for execution in executions:
                execution.status = "interrupted"
                execution.completed_at = now
                execution.error = execution.error or message
            await session.commit()
            if tasks or executions:
                log.info(
                    "Recovered %d task(s) and %d execution(s) after restart",
                    len(tasks),
                    len(executions),
                )
            return len(tasks)

    async def shutdown(self) -> None:
        workers = list(self._running.items())
        for _, worker in workers:
            worker.cancel()
        for _, worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._running.clear()
