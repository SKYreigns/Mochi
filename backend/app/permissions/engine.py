"""Permission and approval engine — enforced independently of the LLM."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PermissionMode, ToolPermission
from app.core.errors import ApprovalTimeoutError, EmergencyStopError, PermissionDeniedError
from app.core.events import EventBus
from app.core.logging import audit, get_logger, redact_value
from app.database.models import Approval, AuditLog, Task
from app.services.settings_service import SettingsService

log = get_logger("permissions")

_READ_ONLY = {ToolPermission.READ, ToolPermission.NAVIGATE}
_CONSQUENTIAL = {ToolPermission.DESTRUCTIVE, ToolPermission.ADMIN, ToolPermission.SENSITIVE}


class PermissionedTool(Protocol):
    name: str
    permission: ToolPermission
    requires_approval: bool
    approval_reason: str


class EmergencyState:
    """Global kill switch shared by every running task."""

    def __init__(self) -> None:
        self._stopped = False
        self.stopped_at: datetime | None = None

    @property
    def stopped(self) -> bool:
        return self._stopped

    def engage(self) -> None:
        self._stopped = True
        self.stopped_at = datetime.now(timezone.utc)

    def clear(self) -> None:
        self._stopped = False
        self.stopped_at = None

    def check(self) -> None:
        if self._stopped:
            raise EmergencyStopError("Emergency stop is engaged. All autonomous execution is halted.")


@dataclass(slots=True)
class Decision:
    allowed: bool
    requires_approval: bool
    reason: str
    mode: str


class PermissionEngine:
    def __init__(self, runtime_settings: SettingsService, events: EventBus, emergency: EmergencyState) -> None:
        self.settings = runtime_settings
        self.events = events
        self.emergency = emergency
        self._waiters: dict[str, asyncio.Future[tuple[bool, str]]] = {}

    async def evaluate(
        self,
        tool: PermissionedTool,
        *,
        auto_approved: set[str] | tuple[str, ...] = (),
    ) -> Decision:
        mode = PermissionMode(self.settings.permission_mode)
        permission = ToolPermission(tool.permission)
        exempt = tool.name in set(auto_approved)
        auto_tools = set(self.settings.safety.get("auto_approve_tools", []))

        if self.emergency.stopped:
            return Decision(False, False, "Emergency stop engaged.", mode.value)

        if mode == PermissionMode.OBSERVE:
            if permission in _READ_ONLY:
                return Decision(True, False, "Read-only action allowed in observe mode.", mode.value)
            return Decision(
                False,
                False,
                f"'{tool.name}' is a {permission.value} action; observe mode is read-only.",
                mode.value,
            )

        if mode == PermissionMode.ASSISTED:
            if permission in _READ_ONLY or permission == ToolPermission.WRITE:
                if tool.requires_approval and not exempt:
                    return Decision(True, True, tool.approval_reason or "This write action requires approval.", mode.value)
                return Decision(True, False, "Reversible action allowed in assisted mode.", mode.value)
            return Decision(True, True, tool.approval_reason or f"{permission.value} action requires approval.", mode.value)

        # Autonomous
        if permission in _READ_ONLY or permission == ToolPermission.WRITE:
            if tool.requires_approval and not exempt:
                return Decision(True, True, tool.approval_reason or "This action requires approval.", mode.value)
            return Decision(True, False, "Action allowed in autonomous mode.", mode.value)
        if permission == ToolPermission.SENSITIVE:
            return Decision(True, True, tool.approval_reason or "Sensitive action always requires approval.", mode.value)
        if tool.requires_approval and not exempt and tool.name not in auto_tools:
            return Decision(True, True, tool.approval_reason or "Action requires approval.", mode.value)
        if exempt or tool.name in auto_tools:
            return Decision(True, False, "Pre-authorized workflow action.", mode.value)
        return Decision(True, False, "Action allowed in autonomous mode within configured limits.", mode.value)

    async def request_approval(
        self,
        session: AsyncSession,
        *,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        permission: str,
        reason: str,
        timeout: float,
    ) -> tuple[bool, str]:
        self.emergency.check()
        approval = Approval(
            task_id=task_id,
            tool_name=tool_name,
            arguments=redact_value(arguments),
            permission=permission,
            reason=reason,
            status="pending",
        )
        session.add(approval)
        await session.flush()
        # Persist the audit record alongside the request: surviving a restart
        # matters more than a log line for an operation that can change state.
        session.add(
            AuditLog(
                event="approval.requested",
                actor="agent",
                action=tool_name,
                target=str(redact_value(arguments))[:400],
                outcome="pending",
                detail={
                    "approval_id": approval.id,
                    "permission": permission,
                    "reason": reason,
                },
                task_id=task_id,
            )
        )
        await session.commit()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[bool, str]] = loop.create_future()
        self._waiters[approval.id] = future

        task = await session.get(Task, task_id)
        if task is not None:
            task.status = "waiting_for_approval"
            await session.commit()

        audit(
            "approval.requested",
            task_id=task_id,
            approval_id=approval.id,
            tool=tool_name,
            arguments=redact_value(arguments),
            permission=permission,
        )
        await self.events.publish(
            "approval.requested",
            {
                "approval_id": approval.id,
                "task_id": task_id,
                "tool_name": tool_name,
                "arguments": redact_value(arguments),
                "permission": permission,
                "reason": reason,
            },
        )

        try:
            approved, note = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            approval.status = "expired"
            approval.decided_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    event="approval.expired",
                    actor="system",
                    action=tool_name,
                    target=str(redact_value(arguments))[:400],
                    outcome="expired",
                    detail={"approval_id": approval.id, "reason": reason},
                    task_id=task_id,
                )
            )
            await session.commit()
            await self.events.publish(
                "approval.resolved",
                {"approval_id": approval.id, "task_id": task_id, "approved": False, "reason": "expired"},
            )
            raise ApprovalTimeoutError(
                "The approval request timed out.",
                hint="No approval was received. Re-run the task when you are ready to review it.",
            )
        except asyncio.CancelledError:
            approval.status = "cancelled"
            approval.decided_at = datetime.now(timezone.utc)
            await session.commit()
            raise
        finally:
            self._waiters.pop(approval.id, None)
            if task is not None:
                task.status = "running"
                await session.commit()

        if not approved:
            raise PermissionDeniedError(
                "The user denied this action.",
                hint=note or "Choose a different approach or ask the user for guidance.",
            )
        return approved, note

    async def decide(
        self,
        session: AsyncSession,
        approval_id: str,
        *,
        approved: bool,
        note: str = "",
    ) -> Approval:
        approval = await session.get(Approval, approval_id)
        if approval is None:
            raise PermissionDeniedError("Approval request not found.")
        if approval.status != "pending":
            raise PermissionDeniedError(f"Approval is already {approval.status}.")
        approval.status = "approved" if approved else "denied"
        approval.decision_note = note
        approval.decided_at = datetime.now(timezone.utc)
        session.add(
            AuditLog(
                event="approval.resolved",
                actor="user",
                action=approval.tool_name,
                target=str(redact_value(approval.arguments))[:400],
                outcome=approval.status,
                detail={
                    "approval_id": approval.id,
                    "approved": approved,
                    "note": note,
                },
                task_id=approval.task_id,
            )
        )
        await session.commit()

        waiter = self._waiters.get(approval_id)
        if waiter and not waiter.done():
            waiter.set_result((approved, note))

        audit(
            "approval.resolved",
            approval_id=approval_id,
            task_id=approval.task_id,
            approved=approved,
            note=note,
        )
        await self.events.publish(
            "approval.resolved",
            {
                "approval_id": approval_id,
                "task_id": approval.task_id,
                "approved": approved,
                "reason": "user",
                "note": note,
            },
        )
        return approval

    async def cancel_task_approvals(self, session: AsyncSession, task_id: str) -> None:
        approvals = (
            await session.execute(select(Approval).where(Approval.task_id == task_id, Approval.status == "pending"))
        ).scalars().all()
        for approval in approvals:
            approval.status = "cancelled"
            approval.decided_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    event="approval.cancelled",
                    actor="system",
                    action=approval.tool_name,
                    target=str(redact_value(approval.arguments))[:400],
                    outcome="cancelled",
                    detail={"approval_id": approval.id, "reason": "task cancelled"},
                    task_id=task_id,
                )
            )
            waiter = self._waiters.pop(approval.id, None)
            if waiter and not waiter.done():
                waiter.cancel()
        await session.commit()

    async def cancel_all_waiters(self) -> None:
        for waiter in list(self._waiters.values()):
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()

    async def mark_stale_approvals(self, session: AsyncSession) -> int:
        approvals = (
            await session.execute(select(Approval).where(Approval.status == "pending"))
        ).scalars().all()
        for approval in approvals:
            approval.status = "cancelled"
            approval.decided_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    event="approval.stale",
                    actor="system",
                    action=approval.tool_name,
                    target=str(redact_value(approval.arguments))[:400],
                    outcome="cancelled",
                    detail={"approval_id": approval.id, "reason": "backend restarted"},
                    task_id=approval.task_id,
                )
            )
        if approvals:
            await session.commit()
        return len(approvals)
