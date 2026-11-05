"""Multi-tool orchestration: ordering, failures, loops, cancellation, stop."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.enums import ToolPermission
from app.database.models import ToolExecution
from app.providers.base import ProviderResponse, ToolCall
from app.tools.base import EmptyArgs, build_tool, fail, ok

from tests.helpers import (
    ScriptedProvider,
    install_provider,
    tool_response,
    wait_for_task,
    wait_for_task_status,
    wait_until,
)

PLAN = {
    "objective": "Check CPU and memory",
    "steps": [
        {"title": "Read CPU", "tools": ["system.get_cpu_usage"]},
        {"title": "Read memory", "tools": ["system.get_ram_usage"]},
    ],
}


async def _executions(services, task_id: str) -> list[ToolExecution]:
    async with services.db.session() as session:
        return list(
            (
                await session.execute(
                    select(ToolExecution)
                    .where(ToolExecution.task_id == task_id)
                    .order_by(ToolExecution.step_index)
                )
            )
            .scalars()
            .all()
        )


async def test_two_tool_plan_filters_schemas_and_associates_results(services):
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="system.get_cpu_usage", arguments={})),
            tool_response(ToolCall(id="call_2", name="system.get_ram_usage", arguments={})),
            ProviderResponse(content="CPU and memory look healthy."),
        ],
        plan=PLAN,
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Check my CPU and memory", trigger="test")
    task = await wait_for_task(services, task.id)

    assert task.status == "completed"
    assert task.metadata_json["statistics"]["tools"] == 2
    assert task.metadata_json["statistics"]["succeeded"] == 2
    assert task.metadata_json["statistics"]["llm_calls"] == 3
    assert task.metadata_json["statistics"]["planner_ms"] >= 0

    # Only the planner-selected schemas reached the model.
    first_specs = {spec["function"]["name"] for spec in provider.tools_seen[0]}
    assert first_specs == {"system.get_cpu_usage", "system.get_ram_usage"}
    assert len(services.registry.specs()) > len(first_specs)

    # Each result is attached to its own call name for Ollama serialization.
    tool_messages = [message for message in provider.messages_seen[1] if message.role == "tool"]
    assert [message.name for message in tool_messages] == ["system.get_cpu_usage"]
    tool_messages = [message for message in provider.messages_seen[2] if message.role == "tool"]
    assert [message.name for message in tool_messages] == [
        "system.get_cpu_usage",
        "system.get_ram_usage",
    ]

    executions = await _executions(services, task.id)
    assert [item.tool_name for item in executions] == [
        "system.get_cpu_usage",
        "system.get_ram_usage",
    ]
    assert all(item.status == "completed" and item.ok and item.verified for item in executions)
    assert "CPU and memory look healthy." in (task.result or "")


async def test_second_tool_failure_fails_the_task(services):
    async def broken(args, ctx):
        return fail("memory probe failed")

    services.registry.register(
        build_tool(
            name="test.memory_broken",
            description="Fails for orchestration testing.",
            input_model=EmptyArgs,
            handler=broken,
            permission=ToolPermission.READ,
        )
    )
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="system.get_cpu_usage", arguments={})),
            tool_response(ToolCall(id="call_2", name="test.memory_broken", arguments={})),
            ProviderResponse(content="Everything worked!"),
        ]
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Check CPU then memory", trigger="test")
    task = await wait_for_task(services, task.id)

    assert task.status == "failed"
    assert not (task.result or "").startswith("Everything worked")
    assert task.metadata_json["statistics"]["succeeded"] == 1
    assert task.metadata_json["statistics"]["failed"] == 1

    executions = await _executions(services, task.id)
    assert [item.status for item in executions] == ["completed", "failed"]
    assert executions[1].error and "memory probe failed" in executions[1].error


async def test_crashing_tool_is_isolated_and_reported(services):
    async def crash(args, ctx):
        raise RuntimeError("driver exploded")

    services.registry.register(
        build_tool(
            name="test.crash",
            description="Crashes for isolation testing.",
            input_model=EmptyArgs,
            handler=crash,
            permission=ToolPermission.READ,
        )
    )
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="test.crash", arguments={})),
            ProviderResponse(content=""),
        ]
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Crash a tool", trigger="test")
    task = await wait_for_task(services, task.id)

    assert task.status == "failed"
    execution = (await _executions(services, task.id))[0]
    assert execution.status == "failed"
    assert execution.error and "RuntimeError" in execution.error


async def test_repeated_failing_call_is_bounded(services):
    services.app_settings.max_retries = 0

    async def always_fail(args, ctx):
        return fail("still broken")

    services.registry.register(
        build_tool(
            name="test.always_fail",
            description="Always fails.",
            input_model=EmptyArgs,
            handler=always_fail,
            permission=ToolPermission.READ,
        )
    )
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id=f"call_{index}", name="test.always_fail", arguments={}))
            for index in range(6)
        ]
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Loop forever", trigger="test")
    task = await wait_for_task(services, task.id)

    assert task.status == "failed"
    assert provider.agent_calls == 4  # three repeats after the first failure
    assert "repeated the same failing action" in (task.error or "")
    executions = await _executions(services, task.id)
    assert len(executions) == 4
    assert all(item.status == "failed" for item in executions)


async def test_max_steps_prevents_unbounded_loop(services):
    services.app_settings.max_steps = 2
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="system.get_cpu_usage", arguments={})),
            tool_response(ToolCall(id="call_2", name="system.get_cpu_usage", arguments={})),
        ]
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Keep checking CPU", trigger="test")
    task = await wait_for_task(services, task.id)

    assert task.status == "blocked"
    assert provider.agent_calls == 2
    assert "maximum of 2 steps" in (task.error or "")
    executions = await _executions(services, task.id)
    assert len(executions) == 2


async def test_cancellation_closes_running_execution(services):
    async def slow(args, ctx):
        await asyncio.sleep(30)
        return ok({}, evidence="never reached")

    services.registry.register(
        build_tool(
            name="test.slow",
            description="Sleeps until cancelled.",
            input_model=EmptyArgs,
            handler=slow,
            permission=ToolPermission.READ,
            timeout=60,
        )
    )
    provider = ScriptedProvider([tool_response(ToolCall(id="call_1", name="test.slow", arguments={}))])
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Start a slow tool", trigger="test")

    async def running() -> bool:
        executions = await _executions(services, task.id)
        return bool(executions and executions[0].status == "running")

    await wait_until(running, timeout=10)
    assert await services.tasks.cancel(task.id) is True
    await wait_for_task_status(services, task.id, {"cancelled"})

    execution = (await _executions(services, task.id))[0]
    assert execution.status == "cancelled"
    assert execution.completed_at is not None


async def test_emergency_stop_cancels_before_next_step(services):
    async def stopper(args, ctx):
        services.emergency.engage()
        return ok({"stopped": True}, evidence="stop engaged by tool")

    services.registry.register(
        build_tool(
            name="test.engage_stop",
            description="Engages the kill switch.",
            input_model=EmptyArgs,
            handler=stopper,
            permission=ToolPermission.READ,
        )
    )
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="test.engage_stop", arguments={})),
            tool_response(ToolCall(id="call_2", name="system.get_cpu_usage", arguments={})),
            ProviderResponse(content="Should not be reached."),
        ]
    )
    await install_provider(services, provider)
    try:
        task = await services.tasks.start(instruction="Trigger the emergency stop", trigger="test")
        task = await wait_for_task(services, task.id)
    finally:
        services.emergency.clear()

    assert task.status == "cancelled"
    assert provider.agent_calls == 1
    executions = await _executions(services, task.id)
    assert len(executions) == 1
    assert executions[0].status == "completed"
