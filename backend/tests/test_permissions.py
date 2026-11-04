"""Permission matrix and approval handshake — enforced without the model."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select

from app.core.enums import ToolPermission
from app.core.errors import ApprovalTimeoutError, PermissionDeniedError
from app.database.models import Approval, Task


@dataclass
class FakeTool:
    name: str
    permission: ToolPermission
    requires_approval: bool = False
    approval_reason: str = ""


async def _set_mode(services, mode: str) -> None:
    async with services.db.session() as session:
        await services.settings.set(session, "permission_mode", mode)


async def test_observe_mode_is_read_only(services):
    await _set_mode(services, "observe")
    read = await services.permissions.evaluate(FakeTool("files.list_directory", ToolPermission.READ))
    write = await services.permissions.evaluate(FakeTool("files.create_file", ToolPermission.WRITE))
    assert read.allowed and not read.requires_approval
    assert not write.allowed


async def test_assisted_mode_allows_reversible_writes(services):
    await _set_mode(services, "assisted")
    decision = await services.permissions.evaluate(FakeTool("files.create_file", ToolPermission.WRITE))
    assert decision.allowed and not decision.requires_approval
    sensitive = await services.permissions.evaluate(FakeTool("lms.submit_assignment", ToolPermission.SENSITIVE))
    assert sensitive.allowed and sensitive.requires_approval


async def test_destructive_actions_require_approval(services):
    await _set_mode(services, "autonomous")
    decision = await services.permissions.evaluate(
        FakeTool("files.delete", ToolPermission.DESTRUCTIVE, requires_approval=True, approval_reason="Permanent")
    )
    assert decision.allowed and decision.requires_approval


async def test_emergency_stop_denies_everything(services):
    services.emergency.engage()
    try:
        decision = await services.permissions.evaluate(FakeTool("files.list_directory", ToolPermission.READ))
        assert not decision.allowed
        assert "Emergency" in decision.reason
    finally:
        services.emergency.clear()


async def _create_task(services) -> str:
    async with services.db.session() as session:
        task = Task(instruction="test", status="running", permission_mode="assisted")
        session.add(task)
        await session.commit()
        return task.id


async def test_approval_approved_flow(services):
    task_id = await _create_task(services)

    async def run_request():
        async with services.db.session() as session:
            return await services.permissions.request_approval(
                session,
                task_id=task_id,
                tool_name="files.delete",
                arguments={"path": "test.txt"},
                permission="destructive",
                reason="Delete a file",
                timeout=10,
            )

    waiter = asyncio.create_task(run_request())
    approval_id = None
    for _ in range(100):
        async with services.db.session() as session:
            approval = (
                await session.execute(select(Approval).where(Approval.task_id == task_id))
            ).scalars().first()
            if approval is not None:
                approval_id = approval.id
                break
        await asyncio.sleep(0.02)
    assert approval_id, "approval was not persisted"

    async with services.db.session() as session:
        await services.permissions.decide(session, approval_id, approved=True, note="ok")

    approved, note = await asyncio.wait_for(waiter, timeout=5)
    assert approved is True and note == "ok"

    # The task must return to running after the decision.
    async with services.db.session() as session:
        task = await session.get(Task, task_id)
        assert task is not None and task.status == "running"


async def test_approval_denied_flow(services):
    task_id = await _create_task(services)

    async def run_request():
        async with services.db.session() as session:
            await services.permissions.request_approval(
                session,
                task_id=task_id,
                tool_name="lms.submit_assignment",
                arguments={},
                permission="sensitive",
                reason="Submit",
                timeout=10,
            )

    waiter = asyncio.create_task(run_request())
    approval_id = None
    for _ in range(100):
        async with services.db.session() as session:
            approval = (
                await session.execute(select(Approval).where(Approval.task_id == task_id))
            ).scalars().first()
            if approval is not None:
                approval_id = approval.id
                break
        await asyncio.sleep(0.02)
    async with services.db.session() as session:
        await services.permissions.decide(session, approval_id, approved=False, note="not now")

    try:
        await asyncio.wait_for(waiter, timeout=5)
    except PermissionDeniedError as exc:
        assert "denied" in exc.message.lower()
    else:  # pragma: no cover
        raise AssertionError("denied approval must raise PermissionDeniedError")


async def test_approval_timeout(services):
    services.app_settings.approval_timeout_seconds = 0.05
    task_id = await _create_task(services)
    async with services.db.session() as session:
        try:
            await services.permissions.request_approval(
                session,
                task_id=task_id,
                tool_name="files.delete",
                arguments={},
                permission="destructive",
                reason="Delete",
                timeout=0.05,
            )
        except ApprovalTimeoutError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ApprovalTimeoutError")
