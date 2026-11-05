"""Agent orchestrator: scripted provider, real tools, real permission engine."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.core.enums import TaskStatus
from app.core.errors import ProviderError
from app.database.models import Message
from app.orchestrator.planner import create_plan
from app.providers.base import ProviderResponse, ToolCall
from app.tools.base import EmptyArgs, build_tool, fail

from app.core.enums import ToolPermission
from tests.helpers import (
    ScriptedProvider,
    install_provider,
    tool_response,
    wait_for_task,
    wait_for_task_status,
    wait_until,
)


async def _start(services, instruction: str) -> str:
    task = await services.tasks.start(instruction=instruction, trigger="test")
    return task.id


async def test_text_encoded_tool_call_is_recovered_and_executed(services, sandbox):
    """qwen3 sometimes prints a tool call as JSON text instead of calling natively."""
    target = sandbox / "recovered.txt"
    target.write_text("recovered body", encoding="utf-8")
    provider = ScriptedProvider(
        [
            ProviderResponse(
                content=json.dumps(
                    {"name": "files.read_file", "arguments": {"path": str(target)}}
                )
            ),
            ProviderResponse(content="The file contains: recovered body."),
        ],
        plan={
            "objective": "Read a file",
            "steps": [{"title": "Read", "tools": ["files.read_file"]}],
        },
    )
    await install_provider(services, provider)
    task = await wait_for_task(services, await _start(services, "Read the recovered file"))

    assert task.status == "completed"
    assert task.metadata_json["statistics"]["tools"] == 1
    assert task.metadata_json["statistics"]["succeeded"] == 1
    assert task.metadata_json["executions"][0]["tool"] == "files.read_file"
    assert task.metadata_json["executions"][0]["verified"] is True
    # The text-encoded call is not shown as the assistant's answer.
    assert '"arguments"' not in (task.result or "")
    assert "recovered body" in (task.result or "")

    # It is also never persisted as a visible chat message.
    async with services.db.session() as session:
        stored = (
            await session.execute(
                select(Message).where(Message.conversation_id == task.conversation_id)
            )
        ).scalars().all()
    contents = [message.content for message in stored if message.role == "assistant"]
    assert contents, "the final answer should be stored"
    assert not any('"arguments"' in content for content in contents)


async def test_successful_tool_execution(services, sandbox):
    (sandbox / "hello.txt").write_text("hello", encoding="utf-8")
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="files.list_directory", arguments={"path": str(sandbox)})),
            ProviderResponse(content="I listed the folder and found hello.txt."),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "List my sandbox folder")
    task = await wait_for_task(services, task_id)

    assert task.status == TaskStatus.COMPLETED.value
    assert task.result and "hello.txt" in task.result
    assert "Execution evidence" in task.result
    assert task.metadata_json["statistics"]["succeeded"] == 1
    assert task.metadata_json["statistics"]["failed"] == 0


async def test_agent_only_sends_planned_tools(services, sandbox):
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="files.list_directory", arguments={"path": str(sandbox)})),
            ProviderResponse(content="Listed the sandbox."),
        ],
        plan={
            "objective": "List the sandbox",
            "steps": [{"title": "List files", "tools": ["files.list_directory"]}],
        },
    )
    await install_provider(services, provider)
    task_id = await _start(services, "List my sandbox folder")
    await wait_for_task(services, task_id)

    assert provider.tools_seen
    names = {spec["function"]["name"] for spec in provider.tools_seen[0]}
    assert names == {"files.list_directory"}
    assert len(services.registry.specs()) > len(names)


async def test_multiple_sequential_tool_calls(services, sandbox):
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(
                    id="call_1",
                    name="files.create_file",
                    arguments={"path": str(sandbox / "a.txt"), "content": "alpha"},
                )
            ),
            tool_response(
                ToolCall(
                    id="call_2",
                    name="files.read_file",
                    arguments={"path": str(sandbox / "a.txt")},
                )
            ),
            ProviderResponse(content="Created and read the file."),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "Create then read a file")
    task = await wait_for_task(services, task_id)

    assert task.status == TaskStatus.COMPLETED.value
    assert task.metadata_json["statistics"]["tools"] == 2
    assert task.metadata_json["statistics"]["succeeded"] == 2
    assert len(task.metadata_json["executions"]) == 2


async def test_planning_failure_uses_safe_fallback(services):
    provider = ScriptedProvider([], fail_plan=True)
    plan = await create_plan(provider, "Find my notes", services.registry.names())
    assert plan["source"] == "fallback"
    assert plan["objective"] == "Find my notes"
    assert plan["steps"]


def test_planner_tolerates_extra_trailing_brace():
    from app.orchestrator.planner import _extract_json

    plan = _extract_json(
        '{"objective": "Check CPU and RAM", "steps": [{"title": "CPU", "tools": ["system.get_cpu_usage"]}]}}'
    )
    assert plan is not None
    assert plan["steps"][0]["tools"] == ["system.get_cpu_usage"]


def test_planner_extracts_json_from_surrounding_prose():
    from app.orchestrator.planner import _extract_json

    plan = _extract_json(
        'Here is the plan:\n```json\n{"objective": "List files", "steps": [{"title": "List"}]}\n```\nDone.'
    )
    assert plan is not None
    assert plan["objective"] == "List files"


async def test_provider_error_marks_task_failed(services):
    provider = ScriptedProvider([ProviderError("model endpoint unreachable")])
    await install_provider(services, provider)
    task_id = await _start(services, "Do something")
    task = await wait_for_task(services, task_id)
    assert task.status == TaskStatus.FAILED.value
    assert task.error and "unreachable" in task.error


async def test_tool_failure_prevents_false_success(services):
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(id="call_1", name="files.read_file", arguments={"path": "definitely-missing.txt"})
            ),
            ProviderResponse(content="Everything succeeded! The file contents are ready."),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "Read a file that does not exist")
    task = await wait_for_task(services, task_id)

    assert task.status == TaskStatus.FAILED.value
    assert "not reported as complete" in (task.error or "")
    assert not (task.result or "").startswith("Everything succeeded")


async def test_invalid_tool_arguments_are_rejected(services):
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(id="call_1", name="files.read_file", arguments={"path": 123})
            ),
            ProviderResponse(content=""),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "Call a tool with invalid args")
    task = await wait_for_task(services, task_id)

    assert task.status == TaskStatus.FAILED.value
    executions = task.metadata_json["executions"]
    assert executions and "Invalid arguments" in executions[0]["error"]


async def test_retry_limit_for_retryable_tool(services):
    attempts = {"count": 0}

    async def flaky_handler(args, ctx):
        attempts["count"] += 1
        return fail("temporary failure", retryable=True)

    services.registry.register(
        build_tool(
            name="test.flaky",
            description="Always fails, for retry testing.",
            input_model=EmptyArgs,
            handler=flaky_handler,
            permission=ToolPermission.READ,
        )
    )
    services.app_settings.max_retries = 2
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="test.flaky", arguments={})),
            ProviderResponse(content=""),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "Retry a flaky action")
    task = await wait_for_task(services, task_id)

    assert attempts["count"] == 3
    assert task.metadata_json["executions"][0]["attempts"] == 3
    assert task.status == TaskStatus.FAILED.value


async def test_approval_required_operation(services, sandbox):
    target = sandbox / "delete-me.txt"
    target.write_text("bye", encoding="utf-8")
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(id="call_1", name="files.delete", arguments={"path": str(target)})
            ),
            ProviderResponse(content="Moved the file to trash after approval."),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "Delete a file")

    from app.database.models import Approval
    from sqlalchemy import select

    approval_id = None

    async def find_approval():
        nonlocal approval_id
        async with services.db.session() as session:
            row = (
                await session.execute(select(Approval).where(Approval.task_id == task_id))
            ).scalars().first()
            if row is not None:
                approval_id = row.id
                return True
        return False

    await wait_until(lambda: find_approval(), timeout=10)
    task = await wait_for_task_status(services, task_id, {"waiting_for_approval"})
    assert task.status == "waiting_for_approval"
    assert target.exists()  # not executed before the decision

    async with services.db.session() as session:
        await services.permissions.decide(session, approval_id, approved=True, note="ok")

    task = await wait_for_task(services, task_id)
    assert task.status == TaskStatus.COMPLETED.value
    assert not target.exists()


async def test_denied_approval_skips_operation(services, sandbox):
    target = sandbox / "keep-me.txt"
    target.write_text("safe", encoding="utf-8")
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="files.delete", arguments={"path": str(target)})),
            ProviderResponse(content=""),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "Delete a file")

    from app.database.models import Approval
    from sqlalchemy import select

    approval_id = None

    async def find_approval():
        nonlocal approval_id
        async with services.db.session() as session:
            row = (
                await session.execute(select(Approval).where(Approval.task_id == task_id))
            ).scalars().first()
            if row is not None:
                approval_id = row.id
                return True
        return False

    await wait_until(find_approval)
    async with services.db.session() as session:
        await services.permissions.decide(session, approval_id, approved=False, note="no")

    task = await wait_for_task(services, task_id)
    assert target.exists()
    assert task.status == TaskStatus.FAILED.value
    assert task.metadata_json["executions"][0]["ok"] is False


async def test_user_cancellation(services):
    release = asyncio.Event()

    async def hang(messages):
        await release.wait()
        return ProviderResponse(content="late")

    provider = ScriptedProvider([hang])
    await install_provider(services, provider)
    task_id = await _start(services, "Long running task")
    await wait_until(lambda: provider.agent_calls >= 1)

    assert await services.tasks.cancel(task_id) is True
    task = await wait_for_task(services, task_id)
    assert task.status == TaskStatus.CANCELLED.value
    assert task.cancelled_at is not None


async def test_emergency_stop_blocks_new_tasks(services):
    services.emergency.engage()
    try:
        task_id = await _start(services, "Should not run")
        task = await wait_for_task(services, task_id)
        assert task.status == TaskStatus.CANCELLED.value
        assert "Emergency stop" in (task.error or "")
    finally:
        services.emergency.clear()


async def test_emergency_stop_halts_active_task(services):
    started = asyncio.Event()

    async def hang(messages):
        started.set()
        await asyncio.sleep(30)
        return ProviderResponse(content="late")

    provider = ScriptedProvider([hang])
    await install_provider(services, provider)
    task_id = await _start(services, "Long task interrupted by emergency stop")
    await asyncio.wait_for(started.wait(), timeout=10)

    result = await services.tasks.emergency_stop()
    assert result["stopped"] is True
    assert result["cancelled_tasks"] >= 1
    try:
        task = await wait_for_task(services, task_id)
        assert task.status == TaskStatus.CANCELLED.value
        assert task.cancelled_at is not None
    finally:
        services.emergency.clear()


async def test_task_completion_requires_verification_evidence(services, sandbox):
    (sandbox / "evidence.txt").write_text("proof", encoding="utf-8")
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(id="call_1", name="files.read_file", arguments={"path": str(sandbox / "evidence.txt")})
            ),
            ProviderResponse(content="Done."),
        ]
    )
    await install_provider(services, provider)
    task_id = await _start(services, "Read evidence")
    task = await wait_for_task(services, task_id)
    assert task.status == TaskStatus.COMPLETED.value
    assert task.metadata_json["statistics"]["unverified"] == 0
    assert "✓" in (task.result or "")
