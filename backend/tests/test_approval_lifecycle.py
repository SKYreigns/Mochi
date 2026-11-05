"""Approval lifecycle: a gated action never runs without a real decision.

The gated tool is registered only inside these tests. Production startup never
registers ``test.*`` tools, so this fixture cannot leak into normal operation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.enums import ToolPermission
from app.database.models import Approval, AuditLog, Task, ToolExecution
from app.providers.base import ProviderResponse, ToolCall
from app.tools.base import EmptyArgs, build_tool, ok

from tests.helpers import (
    ScriptedProvider,
    install_provider,
    tool_response,
    wait_for_task,
    wait_for_task_status,
)

TOOL = "test.gated_action"


def _register_gated_tool(services, calls: dict[str, int]) -> None:
    async def handler(args, ctx):
        calls["count"] += 1
        return ok({"performed": True}, evidence="gated action executed exactly once")

    # Only registered in this test module, never in the production registry.
    assert services.registry.get(TOOL) is None
    services.registry.register(
        build_tool(
            name=TOOL,
            description="Harmless approval-gated test action.",
            input_model=EmptyArgs,
            handler=handler,
            permission=ToolPermission.WRITE,
            requires_approval=True,
            approval_reason="Test action requires explicit approval.",
        )
    )


def _provider() -> ScriptedProvider:
    return ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name=TOOL, arguments={})),
            ProviderResponse(content="The approved action finished."),
        ]
    )


async def _start(services) -> str:
    task = await services.tasks.start(instruction="Run the gated test action", trigger="test")
    return task.id


async def _pending_approval(services, task_id: str) -> Approval:
    for _ in range(200):
        async with services.db.session() as session:
            approval = (
                await session.execute(
                    select(Approval).where(
                        Approval.task_id == task_id, Approval.status == "pending"
                    )
                )
            ).scalars().first()
            if approval is not None:
                return approval
        import asyncio

        await asyncio.sleep(0.02)
    raise AssertionError("approval was never created")


async def _execution(services, task_id: str) -> ToolExecution:
    async with services.db.session() as session:
        return (
            await session.execute(select(ToolExecution).where(ToolExecution.task_id == task_id))
        ).scalars().first()


async def _audit_events(services, task_id: str) -> list[str]:
    async with services.db.session() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.task_id == task_id))
        ).scalars().all()
        return [row.event for row in rows]


async def test_approved_action_executes_once_and_is_audited(api, services):
    calls = {"count": 0}
    _register_gated_tool(services, calls)
    await install_provider(services, _provider())
    task_id = await _start(services)

    await wait_for_task_status(services, task_id, {"waiting_for_approval"})
    approval = await _pending_approval(services, task_id)
    assert calls["count"] == 0  # blocked before the decision

    pending = await api.get("/api/approvals", params={"status": "pending"})
    assert pending.status_code == 200
    assert any(item["id"] == approval.id for item in pending.json()["approvals"])

    execution = await _execution(services, task_id)
    assert execution is not None and execution.status == "pending_approval"

    decided = await api.post(
        f"/api/approvals/{approval.id}/decide", json={"approved": True, "note": "go ahead"}
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    # A decided approval is single-use.
    replay = await api.post(
        f"/api/approvals/{approval.id}/decide", json={"approved": True, "note": "again"}
    )
    assert replay.status_code == 409

    task = await wait_for_task(services, task_id)
    assert task.status == "completed"
    assert calls["count"] == 1

    execution = await _execution(services, task_id)
    assert execution is not None
    assert execution.status == "completed"
    assert execution.ok is True
    assert execution.verified is True
    assert execution.approval_status == "approved"
    assert execution.evidence == "gated action executed exactly once"
    assert execution.started_at and execution.completed_at

    async with services.db.session() as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None
        assert stored.status == "approved"
        assert stored.decision_note == "go ahead"
        assert stored.decided_at is not None

    events = await _audit_events(services, task_id)
    assert "approval.requested" in events
    assert "approval.resolved" in events
    assert "tool.executed" in events

    audited = await api.get("/api/audit")
    assert audited.status_code == 200
    resolved = [row for row in audited.json()["logs"] if row["event"] == "approval.resolved"]
    assert resolved and resolved[0]["outcome"] == "approved"


async def test_denied_action_never_executes(api, services):
    calls = {"count": 0}
    _register_gated_tool(services, calls)
    await install_provider(services, _provider())
    task_id = await _start(services)

    await wait_for_task_status(services, task_id, {"waiting_for_approval"})
    approval = await _pending_approval(services, task_id)
    denied = await api.post(
        f"/api/approvals/{approval.id}/decide", json={"approved": False, "note": "not now"}
    )
    assert denied.status_code == 200 and denied.json()["status"] == "denied"

    task = await wait_for_task(services, task_id)
    assert task.status == "failed"
    assert calls["count"] == 0

    execution = await _execution(services, task_id)
    assert execution is not None
    assert execution.status == "skipped"
    assert execution.ok is False
    assert execution.approval_status == "denied"
    assert execution.error and "denied" in execution.error.lower()

    async with services.db.session() as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None and stored.status == "denied"

    events = await _audit_events(services, task_id)
    assert "approval.resolved" in events
    assert "tool.skipped" in events
    assert "tool.executed" not in events


async def test_expired_approval_never_executes(services):
    calls = {"count": 0}
    _register_gated_tool(services, calls)
    await install_provider(services, _provider())
    services.app_settings.approval_timeout_seconds = 0.05
    task_id = await _start(services)

    task = await wait_for_task(services, task_id)
    assert task.status == "failed"
    assert calls["count"] == 0

    execution = await _execution(services, task_id)
    assert execution is not None
    assert execution.status == "failed"
    assert execution.approval_status == "expired"

    async with services.db.session() as session:
        approval = (await session.execute(select(Approval))).scalars().first()
        assert approval is not None
        assert approval.status == "expired"
        assert approval.decided_at is not None

    events = await _audit_events(services, task_id)
    assert "approval.expired" in events


async def test_invalid_and_unknown_approvals_are_rejected(api, services):
    missing = await api.post(
        "/api/approvals/exe-does-not-exist/decide", json={"approved": True}
    )
    assert missing.status_code == 409
    assert "not found" in missing.json()["detail"].lower()


async def test_stale_pending_approval_is_cancelled_on_restart(api, services):
    async with services.db.session() as session:
        task = Task(instruction="stale", status="waiting_for_approval", permission_mode="assisted")
        session.add(task)
        await session.flush()
        approval = Approval(
            task_id=task.id,
            tool_name=TOOL,
            arguments={},
            permission="write",
            reason="stale",
            status="pending",
            requested_at=datetime.now(timezone.utc),
        )
        session.add(approval)
        await session.commit()
        approval_id = approval.id
        task_id = task.id

    async with services.db.session() as session:
        cancelled = await services.permissions.mark_stale_approvals(session)
    assert cancelled == 1

    async with services.db.session() as session:
        stored = await session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "cancelled"
        assert stored.decided_at is not None

    # The stale record remains recoverable for the audit trail.
    history = await api.get("/api/approvals", params={"status": "cancelled"})
    assert any(item["id"] == approval_id for item in history.json()["approvals"])
    events = await _audit_events(services, task_id)
    assert "approval.stale" in events

    # Deciding it after recovery is refused, so it cannot be reused.
    refused = await api.post(f"/api/approvals/{approval_id}/decide", json={"approved": True})
    assert refused.status_code == 409


async def test_test_tool_is_not_part_of_production_registry(services):
    """The approval fixture is test-only and must never ship enabled."""
    assert services.registry.get(TOOL) is None
    assert not any(name.startswith("test.") for name in services.registry.names())
