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
from app.database.models import ApplicationSetting, AuditLog, Conversation, Message, Task, ToolExecution
from app.orchestrator.agent import AgentOrchestrator

log = get_logger("tasks")

_EMERGENCY_KEY = "safety.emergency"

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

    async def emergency_stop(self, reason: str = "Emergency stop requested by the user.") -> dict[str, Any]:
        self.services.emergency.engage(reason)
        cancelled = await self.cancel_all()
        async with self.db.session() as session:
            await self.services.permissions.reject_pending_approvals(session, reason="emergency stop")
            await self._write_emergency_record(
                session,
                stopped=True,
                stopped_at=self.services.emergency.stopped_at,
                reason=reason,
                actor="user",
                event="emergency.stop",
            )
        await self.events.publish(
            "emergency.stop",
            {"cancelled_tasks": cancelled, "stopped_at": self.services.emergency.stopped_at.isoformat() if self.services.emergency.stopped_at else None, "reason": reason},
        )
        audit("emergency.stop", cancelled_tasks=cancelled, reason=reason)
        return {"stopped": True, "cancelled_tasks": cancelled, "reason": reason}

    async def resume(self, reason: str = "Explicit user resume.") -> dict[str, Any]:
        self.services.emergency.clear()
        async with self.db.session() as session:
            await self._write_emergency_record(
                session,
                stopped=False,
                stopped_at=None,
                reason=reason,
                actor="user",
                event="emergency.cleared",
            )
        await self.events.publish("emergency.cleared", {"reason": reason})
        audit("emergency.cleared", reason=reason)
        return {"stopped": False, "reason": reason}

    async def _write_emergency_record(
        self,
        session: Any,
        *,
        stopped: bool,
        stopped_at: datetime | None,
        reason: str,
        actor: str,
        event: str,
    ) -> None:
        record = {
            "stopped": stopped,
            "stopped_at": stopped_at.isoformat() if stopped_at else None,
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        row = await session.get(ApplicationSetting, _EMERGENCY_KEY)
        previous = dict(row.value) if row is not None and isinstance(row.value, dict) else None
        if row is None:
            session.add(ApplicationSetting(key=_EMERGENCY_KEY, value=record))
        else:
            row.value = record
        # Do not duplicate an identical activation when startup reconciliation
        # re-persists the state written by stale-task recovery.
        changed = previous is None or previous.get("stopped") != stopped or previous.get("reason") != reason
        if changed:
            session.add(
                AuditLog(
                    event=event,
                    actor=actor,
                    action="emergency_stop",
                    target=reason[:400],
                    outcome="stopped" if stopped else "resumed",
                    detail=record,
                )
            )
        await session.commit()

    async def reconcile_emergency_state(self, *, stale_approvals: bool = False) -> dict[str, Any]:
        """Restore the persisted STOP flag, defaulting to fail-safe on recovery.

        * A STOP written by stale-task/execution recovery stays active.
        * A freshly cancelled stale approval also forces STOP so an interrupted
          operation can never resume silently after a restart.
        * Otherwise the persisted record is restored exactly; a missing record
          on a fresh database means "not stopped".
        """
        async with self.db.session() as session:
            if self.services.emergency.stopped:
                reason = self.services.emergency.reason or "Persisted emergency stop."
                await self._write_emergency_record(
                    session,
                    stopped=True,
                    stopped_at=self.services.emergency.stopped_at,
                    reason=reason,
                    actor="system",
                    event="emergency.stop",
                )
                return self.services.emergency.snapshot()

            if stale_approvals:
                reason = "Backend restarted with interrupted approvals; explicit resume is required."
                self.services.emergency.engage(reason)
                await self._write_emergency_record(
                    session,
                    stopped=True,
                    stopped_at=self.services.emergency.stopped_at,
                    reason=reason,
                    actor="system",
                    event="emergency.stop",
                )
                await self.events.publish("emergency.stop", {"cancelled_tasks": 0, "reason": reason})
                return self.services.emergency.snapshot()

            row = await session.get(ApplicationSetting, _EMERGENCY_KEY)
            record = row.value if row is not None and isinstance(row.value, dict) else None
            if record and record.get("stopped"):
                stopped_at = _parse_datetime(record.get("stopped_at"))
                reason = str(record.get("reason") or "Persisted emergency stop.")
                self.services.emergency.restore(stopped=True, stopped_at=stopped_at, reason=reason)
                log.warning("Restored persisted emergency STOP: %s", reason)
                await self.events.publish(
                    "emergency.restored",
                    {"reason": reason, "stopped_at": stopped_at.isoformat() if stopped_at else None},
                )
            else:
                self.services.emergency.restore(stopped=False)
            return self.services.emergency.snapshot()

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
                reason = (
                    "Backend restarted while tasks or actions were still open. "
                    "Execution stays halted until the user explicitly resumes."
                )
                self.services.emergency.engage(reason)
                await self._write_emergency_record(
                    session,
                    stopped=True,
                    stopped_at=self.services.emergency.stopped_at,
                    reason=reason,
                    actor="system",
                    event="emergency.stop",
                )
                await self.events.publish(
                    "emergency.stop",
                    {
                        "cancelled_tasks": len(tasks),
                        "interrupted_executions": len(executions),
                        "reason": reason,
                    },
                )
                log.warning(
                    "Recovered %d task(s) and %d execution(s) after restart; emergency STOP engaged",
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


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
