"""Phase 1 reliability regressions: STOP persistence, completion and plan checks."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import ApplicationSetting, AuditLog, Conversation, Task
from app.orchestrator.planner import record_plan_execution, summarize_plan, validate_plan
from app.orchestrator.validation import validate_completion
from app.providers.base import ProviderResponse
from tests.helpers import ScriptedProvider, install_provider, wait_for_task


async def _seed_running_task(services) -> str:
    async with services.db.session() as session:
        conversation = Conversation(title="Interrupted", user_id="usr_local")
        session.add(conversation)
        await session.flush()
        task = Task(
            conversation_id=conversation.id,
            instruction="Interrupted work",
            status="running",
            permission_mode="assisted",
            trigger="test",
            metadata_json={},
        )
        session.add(task)
        await session.commit()
        return task.id


async def test_emergency_stop_persists_and_requires_explicit_resume(services):
    result = await services.tasks.emergency_stop("phase-1 test")
    assert result["stopped"] is True
    assert services.emergency.stopped is True

    async with services.db.session() as session:
        row = await session.get(ApplicationSetting, "safety.emergency")
        assert row is not None and row.value["stopped"] is True
        events = (
            await session.execute(select(AuditLog).where(AuditLog.event == "emergency.stop"))
        ).scalars().all()
        assert events

    # Simulate a new process: in-memory state is gone, persisted state remains.
    services.emergency.clear()
    snapshot = await services.tasks.reconcile_emergency_state()
    assert snapshot["stopped"] is True
    assert services.emergency.stopped is True

    resumed = await services.tasks.resume()
    assert resumed["stopped"] is False
    services.emergency.clear()
    snapshot = await services.tasks.reconcile_emergency_state()
    assert snapshot["stopped"] is False
    async with services.db.session() as session:
        row = await session.get(ApplicationSetting, "safety.emergency")
        assert row is not None and row.value["stopped"] is False
        cleared = (
            await session.execute(select(AuditLog).where(AuditLog.event == "emergency.cleared"))
        ).scalars().all()
        assert cleared


async def test_emergency_stop_blocks_actions_and_pending_approvals(services):
    services.emergency.engage("test stop")
    try:
        tool = services.registry.get("files.delete")
        assert tool is not None
        decision = await services.permissions.evaluate(tool)
        assert decision.allowed is False
        assert "Emergency stop" in decision.reason
    finally:
        services.emergency.clear()

    # A recovered interrupted task must engage STOP, not resume silently.
    await _seed_running_task(services)
    await services.tasks.recover_stale_state()
    assert services.emergency.stopped is True
    assert "restarted" in services.emergency.reason.lower() or "halted" in services.emergency.reason.lower()
    services.emergency.clear()


def test_completion_validation_rejects_claim_without_execution():
    result = validate_completion("I opened Notepad and typed Hello Mochi.", [])
    assert result.ok is False
    assert "No tool was executed" in result.corrected_content
    assert "completion rejected" in result.issue.lower()


def test_completion_validation_accepts_matching_execution():
    executions = [
        {"tool": "computer.type_text", "ok": True, "verified": True},
        {"tool": "computer.focus_window", "ok": True, "verified": True},
    ]
    result = validate_completion("I focused Notepad and typed the text.", executions)
    assert result.ok is True


def test_completion_validation_requires_matching_action_category():
    executions = [{"tool": "files.read_file", "ok": True, "verified": True}]
    result = validate_completion("I opened Notepad and typed the requested text.", executions)
    assert result.ok is False
    assert "matching execution" in result.issue


def test_completion_validation_adds_unverified_computer_caveat():
    executions = [{"tool": "computer.click", "ok": True, "verified": False}]
    result = validate_completion("I clicked the button.", executions)
    assert result.ok is True
    assert result.caveats
    assert "could not be independently verified" in result.caveat_text()


async def test_agent_rejects_fabricated_completion(services):
    provider = ScriptedProvider(
        [ProviderResponse(content="I opened Notepad and typed Hello Mochi.")],
        plan={"objective": "Open Notepad", "steps": [{"title": "Open", "tools": []}]},
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Open Notepad and type Hello Mochi", trigger="test")
    finished = await wait_for_task(services, task.id)

    assert finished.status == "failed"
    assert "zero tool executions" in (finished.error or "")
    assert finished.metadata_json["statistics"]["completion_validation"]["ok"] is False
    assert finished.result and "Task failed" in finished.result


def test_plan_validation_prunes_unrelated_tools_and_duplicate_steps(services):
    names = services.registry.names()
    plan = {
        "objective": "Open Notepad and type hello",
        "steps": [
            {
                "title": "Open Notepad",
                "tools": ["desktop.open_application", "browser.navigate"],
            },
            {
                "title": "Open Notepad",
                "tools": ["desktop.open_application"],
            },
            {"title": "Type text", "tools": ["desktop.type_text"]},
        ],
    }
    validated, notes = validate_plan(plan, "Open Notepad and type hello", names)

    first = validated["steps"][0]
    assert first["tools"] == ["desktop.open_application"]
    assert any(item["tool"] == "browser.navigate" for item in notes["removed_tools"])
    # Duplicate step pruned.
    assert len(validated["steps"]) == 2
    assert "desktop" in notes["matched_domains"]


def test_plan_execution_tracking_detects_unused_steps():
    plan = {
        "objective": "x",
        "steps": [
            {"title": "Open", "tools": ["desktop.open_application"], "executed": False, "executed_tools": [], "status": "pending"},
            {"title": "Type", "tools": ["computer.type_text"], "executed": False, "executed_tools": [], "status": "pending"},
        ],
    }
    record_plan_execution(plan, "desktop.open_application", ok=True, verified=True)
    summary = summarize_plan(plan)
    assert summary["executed_steps"] == 1
    assert summary["unused_steps"] == ["Type"]
    assert summary["failed_steps"] == []

    record_plan_execution(plan, "computer.type_text", ok=False, verified=False)
    summary = summarize_plan(plan)
    assert summary["failed_steps"] == ["Type"]
