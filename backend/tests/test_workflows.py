"""Workflow engine: sequential steps, failures, approvals and cancellation."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.enums import ToolPermission
from app.database.models import Approval, Task, WorkflowRun
from app.tools.base import EmptyArgs, build_tool
from tests.helpers import install_provider, wait_until


async def _create(services, steps):
    async with services.db.session() as session:
        workflow = await services.workflows.create_workflow(session, name="Test workflow", steps=steps)
        return workflow.id


async def _run_row(services, run_id: str) -> WorkflowRun:
    async with services.db.session() as session:
        row = await session.get(WorkflowRun, run_id)
        assert row is not None
        return row


async def test_create_edit_and_delete_workflow(services):
    async with services.db.session() as session:
        workflow = await services.workflows.create_workflow(
            session, name="Original", description="first", steps=[{"name": "One", "kind": "tool"}]
        )
        workflow_id = workflow.id
        listed = await services.workflows.list_workflows(session)
        assert any(item["id"] == workflow_id and len(item["steps"]) == 1 for item in listed)

        await services.workflows.update_workflow(
            session,
            workflow_id,
            name="Renamed",
            description="second",
            steps=[{"name": "Step A"}, {"name": "Step B"}],
        )
        listed = await services.workflows.list_workflows(session)
        item = next(row for row in listed if row["id"] == workflow_id)
        assert item["name"] == "Renamed" and len(item["steps"]) == 2

        assert await services.workflows.delete_workflow(session, workflow_id) is True
        assert await services.workflows.update_workflow(session, workflow_id, name="x") is None


async def test_sequential_tool_steps(services, sandbox):
    workflow_id = await _create(
        services,
        [
            {
                "name": "Create",
                "kind": "tool",
                "config": {
                    "tool": "files.create_file",
                    "arguments": {"path": str(sandbox / "wf.txt"), "content": "workflow"},
                },
            },
            {
                "name": "Read",
                "kind": "tool",
                "config": {"tool": "files.read_file", "arguments": {"path": str(sandbox / "wf.txt")}},
            },
        ],
    )
    result = await services.workflows.run(workflow_id)
    assert result["status"] == "completed"
    assert [item["status"] for item in result["results"]] == ["completed", "completed"]
    assert (sandbox / "wf.txt").read_text(encoding="utf-8") == "workflow"
    run = await _run_row(services, result["run_id"])
    assert run.status == "completed"

    async with services.db.session() as session:
        parent = await session.get(Task, result["task_id"])
        assert parent is not None and parent.status == "completed"


async def test_failed_step_stops_by_default(services, sandbox):
    workflow_id = await _create(
        services,
        [
            {
                "name": "Missing read",
                "kind": "tool",
                "config": {"tool": "files.read_file", "arguments": {"path": str(sandbox / "missing.txt")}},
            },
            {
                "name": "Never runs",
                "kind": "tool",
                "config": {"tool": "files.create_file", "arguments": {"path": str(sandbox / "after.txt"), "content": "x"}},
            },
        ],
    )
    result = await services.workflows.run(workflow_id)
    assert result["status"] == "failed"
    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "completed"  # step executed; the tool itself failed
    assert result["results"][0]["result"]["ok"] is False
    assert not (sandbox / "after.txt").exists()


async def test_failed_step_can_continue(services, sandbox):
    workflow_id = await _create(
        services,
        [
            {
                "name": "Missing read",
                "kind": "tool",
                "config": {
                    "tool": "files.read_file",
                    "arguments": {"path": str(sandbox / "missing2.txt")},
                    "on_error": "continue",
                },
            },
            {
                "name": "Still runs",
                "kind": "tool",
                "config": {"tool": "files.create_file", "arguments": {"path": str(sandbox / "after2.txt"), "content": "x"}},
            },
        ],
    )
    result = await services.workflows.run(workflow_id)
    assert result["status"] == "completed"
    assert len(result["results"]) == 2
    assert result["results"][0]["result"]["ok"] is False
    assert (sandbox / "after2.txt").exists()


async def test_approval_checkpoint_approved(services):
    workflow_id = await _create(
        services,
        [{"name": "Review", "kind": "approval", "config": {"reason": "Approve this checkpoint"}}],
    )
    runner = asyncio.create_task(services.workflows.run(workflow_id))
    approval_id = None

    async def find():
        nonlocal approval_id
        async with services.db.session() as session:
            row = (
                await session.execute(select(Approval).where(Approval.tool_name == "workflow.approval"))
            ).scalars().first()
            if row is not None:
                approval_id = row.id
                return True
        return False

    await wait_until(find, timeout=10)
    async with services.db.session() as session:
        await services.permissions.decide(session, approval_id, approved=True, note="go")
    result = await asyncio.wait_for(runner, timeout=10)
    assert result["status"] == "completed"
    assert result["results"][0]["result"]["ok"] is True


async def test_approval_checkpoint_denied_blocks(services):
    workflow_id = await _create(
        services,
        [{"name": "Review", "kind": "approval", "config": {"reason": "Approve this checkpoint"}}],
    )
    runner = asyncio.create_task(services.workflows.run(workflow_id))
    approval_id = None

    async def find():
        nonlocal approval_id
        async with services.db.session() as session:
            row = (
                await session.execute(select(Approval).where(Approval.tool_name == "workflow.approval"))
            ).scalars().first()
            if row is not None:
                approval_id = row.id
                return True
        return False

    await wait_until(find, timeout=10)
    async with services.db.session() as session:
        await services.permissions.decide(session, approval_id, approved=False, note="no")
    result = await asyncio.wait_for(runner, timeout=10)
    assert result["status"] == "blocked"
    assert "denied" in (result["error"] or "").lower()


async def test_workflow_cancellation(services):
    started = asyncio.Event()

    async def slow_handler(args, ctx):
        started.set()
        await asyncio.sleep(30)
        from app.tools.base import ok

        return ok({})

    services.registry.register(
        build_tool(
            name="test.slow-workflow",
            description="Slow tool for cancellation testing.",
            input_model=EmptyArgs,
            handler=slow_handler,
            permission=ToolPermission.READ,
        )
    )
    workflow_id = await _create(
        services,
        [{"name": "Slow", "kind": "tool", "config": {"tool": "test.slow-workflow", "arguments": {}}}],
    )
    runner = asyncio.create_task(services.workflows.run(workflow_id))
    await asyncio.wait_for(started.wait(), timeout=10)

    async with services.db.session() as session:
        run = (
            await session.execute(select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id))
        ).scalars().first()
        run_id = run.id
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
    run = await _run_row(services, run_id)
    assert run.status == "cancelled"
    async with services.db.session() as session:
        tasks = (await session.execute(select(Task))).scalars().all()
        parents = [task for task in tasks if (task.metadata_json or {}).get("workflow_run_id") == run_id]
        assert parents and parents[0].status in ("cancelled", "failed")
