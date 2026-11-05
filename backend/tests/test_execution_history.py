"""Persistent tool execution history: storage, API, migration and recovery."""

from __future__ import annotations

import json

from sqlalchemy import create_engine, select, text

from app.core.enums import ToolPermission
from app.database.migrations import apply_schema_migrations
from app.database.models import Approval, AuditLog, Message, Task, ToolExecution
from app.providers.base import ProviderResponse, ToolCall
from app.tools.base import EmptyArgs, build_tool, ok

from tests.helpers import (
    ScriptedProvider,
    install_provider,
    tool_response,
    wait_for_task,
    wait_for_task_status,
)


async def _run_in_conversation(api, services, provider, message: str) -> tuple[str, str]:
    await install_provider(services, provider)
    created = await api.post("/api/chat/conversations", json={"title": "Execution history test"})
    conversation_id = created.json()["id"]
    sent = await api.post("/api/chat", json={"message": message, "conversation_id": conversation_id})
    task_id = sent.json()["task_id"]
    await wait_for_task(services, task_id)
    return conversation_id, task_id


async def test_successful_execution_is_persisted_and_returned(api, services, sandbox):
    target = sandbox / "history.txt"
    target.write_text("persisted evidence", encoding="utf-8")
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(id="call_1", name="files.read_file", arguments={"path": str(target)})
            ),
            ProviderResponse(content="The file says: persisted evidence."),
        ]
    )
    conversation_id, task_id = await _run_in_conversation(
        api, services, provider, "Read the history file"
    )

    response = await api.get(f"/api/chat/conversations/{conversation_id}/executions")
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert body["total"] == 1
    execution = body["executions"][0]
    assert execution["tool_name"] == "files.read_file"
    assert execution["task_id"] == task_id
    assert execution["status"] == "completed"
    assert execution["ok"] is True
    assert execution["verified"] is True
    assert execution["evidence"]
    assert execution["started_at"] and execution["completed_at"]
    # The full tool result payload is not part of the public history payload.
    assert "result" not in execution

    async with services.db.session() as session:
        stored = (await session.execute(select(ToolExecution))).scalars().all()
        assert len(stored) == 1
        assert stored[0].conversation_id == conversation_id
        assert stored[0].result and stored[0].result["verified"] is True
        messages = (await session.execute(select(Message))).scalars().all()
        assert messages[0].task_id == task_id
        assert any(message.role == "assistant" and message.task_id == task_id for message in messages)

    # Reload twice; both requests must return the same deterministic history.
    first = (await api.get(f"/api/chat/conversations/{conversation_id}/executions")).json()
    second = (await api.get(f"/api/chat/conversations/{conversation_id}/executions")).json()
    assert [item["id"] for item in first["executions"]] == [
        item["id"] for item in second["executions"]
    ]


async def test_failed_execution_is_persisted_with_error(api, services):
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(id="call_1", name="files.read_file", arguments={"path": "missing-file.txt"})
            ),
            ProviderResponse(content=""),
        ]
    )
    conversation_id, task_id = await _run_in_conversation(
        api, services, provider, "Read a missing file"
    )

    response = await api.get(f"/api/chat/conversations/{conversation_id}/executions")
    execution = response.json()["executions"][0]
    assert execution["status"] == "failed"
    assert execution["ok"] is False
    assert execution["error"]


async def test_unknown_tool_attempt_is_persisted(api, services):
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="fake.unknown_tool", arguments={})),
            ProviderResponse(content=""),
        ]
    )
    conversation_id, task_id = await _run_in_conversation(
        api, services, provider, "Call a tool that does not exist"
    )

    executions = (
        await api.get(f"/api/chat/conversations/{conversation_id}/executions")
    ).json()["executions"]
    assert len(executions) == 1
    assert executions[0]["tool_name"] == "fake.unknown_tool"
    assert executions[0]["status"] == "failed"
    assert "Unknown tool" in executions[0]["error"]

    # An unknown tool is a failed action, not a silent success.
    async with services.db.session() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.metadata_json["statistics"]["failed"] == 1
        assert task.metadata_json["statistics"]["succeeded"] == 0
        assert "fake.unknown_tool" in (task.result or "")

    async with services.db.session() as session:
        audited = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.task_id == task_id, AuditLog.event == "tool.rejected"
                )
            )
        ).scalars().all()
        assert audited and audited[0].outcome == "failed"


async def test_executions_are_chronological_and_paginated(api, services):
    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(id="call_1", name="system.get_cpu_usage", arguments={})
            ),
            tool_response(
                ToolCall(id="call_2", name="system.get_memory_usage", arguments={})
            ),
            ProviderResponse(content="CPU and memory checked."),
        ]
    )
    conversation_id, _ = await _run_in_conversation(
        api, services, provider, "Check CPU and memory"
    )

    full = (await api.get(f"/api/chat/conversations/{conversation_id}/executions")).json()
    assert full["total"] == 2
    names = [item["tool_name"] for item in full["executions"]]
    assert names == ["system.get_cpu_usage", "system.get_memory_usage"]

    page = (
        await api.get(
            f"/api/chat/conversations/{conversation_id}/executions",
            params={"limit": 1, "offset": 1},
        )
    ).json()
    assert page["total"] == 2
    assert page["limit"] == 1 and page["offset"] == 1
    assert [item["tool_name"] for item in page["executions"]] == ["system.get_memory_usage"]


async def test_execution_history_404_for_unknown_conversation(api):
    response = await api.get("/api/chat/conversations/does-not-exist/executions")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_execution_history_is_scoped_to_its_conversation(api, services, sandbox):
    await install_provider(
        services,
        ScriptedProvider(
            [
                tool_response(
                    ToolCall(
                        id="call_1",
                        name="files.list_directory",
                        arguments={"path": str(sandbox)},
                    )
                ),
                ProviderResponse(content="Listed."),
            ]
        ),
    )
    first = (await api.post("/api/chat/conversations", json={"title": "One"})).json()["id"]
    second = (await api.post("/api/chat/conversations", json={"title": "Two"})).json()["id"]
    sent = await api.post(
        "/api/chat", json={"message": "List my sandbox", "conversation_id": first}
    )
    await wait_for_task(services, sent.json()["task_id"])

    assert (await api.get(f"/api/chat/conversations/{second}/executions")).json()["total"] == 0


async def test_pending_approval_execution_is_visible_and_cancelled(api, services, sandbox):
    target = sandbox / "cancel-me.txt"
    target.write_text("keep", encoding="utf-8")

    async def noop(args, ctx):
        return ok({"done": True}, evidence="test tool executed")

    services.registry.register(
        build_tool(
            name="test.approval_required",
            description="Harmless approval-gated test tool.",
            input_model=EmptyArgs,
            handler=noop,
            permission=ToolPermission.WRITE,
            requires_approval=True,
            approval_reason="Test tool requires explicit approval.",
        )
    )

    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="test.approval_required", arguments={})),
            ProviderResponse(content=""),
        ]
    )
    await install_provider(services, provider)
    created = await api.post("/api/chat/conversations", json={"title": "Approval cancellation"})
    conversation_id = created.json()["id"]
    sent = await api.post(
        "/api/chat", json={"message": "Use the gated test tool", "conversation_id": conversation_id}
    )
    task_id = sent.json()["task_id"]

    await wait_for_task_status(services, task_id, {"waiting_for_approval"})
    history = (await api.get(f"/api/chat/conversations/{conversation_id}/executions")).json()
    assert history["executions"][0]["status"] == "pending_approval"

    assert target.exists()  # nothing was executed before authorization
    await services.tasks.cancel(task_id)
    await wait_for_task_status(services, task_id, {"cancelled"})

    history = (await api.get(f"/api/chat/conversations/{conversation_id}/executions")).json()
    execution = history["executions"][0]
    assert execution["status"] == "cancelled"
    assert execution["ok"] is False
    assert execution["completed_at"]

    async with services.db.session() as session:
        approval = (await session.execute(select(Approval))).scalars().first()
        assert approval is not None
        assert approval.status == "cancelled"
    assert target.exists()


async def test_stale_running_state_is_recovered_on_restart(services):
    """A task left running by a dead backend must not look active forever."""

    async def slow(args, ctx):
        return ok({}, evidence="unused")

    services.registry.register(
        build_tool(
            name="test.slow",
            description="Slow test tool.",
            input_model=EmptyArgs,
            handler=slow,
            permission=ToolPermission.READ,
        )
    )
    task, execution = await _seed_running_task(services)
    recovered = await services.tasks.recover_stale_state()

    from app.database.models import Task

    async with services.db.session() as session:
        refreshed = await session.get(Task, task.id)
        refreshed_execution = await session.get(ToolExecution, execution.id)
        assert refreshed is not None and refreshed.status == "failed"
        assert refreshed.error
        assert refreshed_execution is not None
        assert refreshed_execution.status == "interrupted"
        assert refreshed_execution.error
        assert refreshed_execution.completed_at is not None
    assert recovered >= 1


async def test_open_execution_is_recovered_after_restart(services):
    from app.database.models import Conversation, Task

    async with services.db.session() as session:
        conversation = Conversation(title="Recovery", user_id="usr_local")
        session.add(conversation)
        await session.flush()
        task = Task(
            conversation_id=conversation.id,
            instruction="Recover me",
            status="running",
            permission_mode="assisted",
            trigger="test",
            metadata_json={},
        )
        session.add(task)
        await session.flush()
        execution = ToolExecution(
            task_id=task.id,
            conversation_id=conversation.id,
            step_index=1,
            tool_name="files.list_directory",
            arguments={},
            status="running",
            permission="read",
        )
        session.add(execution)
        await session.commit()
        execution_id = execution.id
        task_id = task.id

    await services.tasks.recover_stale_state()

    async with services.db.session() as session:
        execution = await session.get(ToolExecution, execution_id)
        assert execution is not None
        assert execution.status == "interrupted"
        recovered_task = await session.get(Task, task_id)
        assert recovered_task is not None and recovered_task.status == "failed"


async def _seed_running_task(services):
    from app.database.models import Conversation, Task

    async with services.db.session() as session:
        conversation = Conversation(title="Stale", user_id="usr_local")
        session.add(conversation)
        await session.flush()
        task = Task(
            conversation_id=conversation.id,
            instruction="Stale task",
            status="running",
            permission_mode="assisted",
            trigger="test",
            metadata_json={},
        )
        session.add(task)
        await session.flush()
        execution = ToolExecution(
            task_id=task.id,
            conversation_id=conversation.id,
            step_index=1,
            tool_name="files.list_directory",
            arguments={},
            status="running",
            permission="read",
        )
        session.add(execution)
        await session.commit()
        return task, execution


def test_migration_backfills_legacy_executions(tmp_path):
    """Old databases gain the history columns without losing data."""
    database = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE tasks (
                    id VARCHAR(40) PRIMARY KEY,
                    conversation_id VARCHAR(40)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE messages (
                    id VARCHAR(40) PRIMARY KEY,
                    conversation_id VARCHAR(40),
                    role VARCHAR(20),
                    content TEXT,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tool_executions (
                    id VARCHAR(40) PRIMARY KEY,
                    task_id VARCHAR(40),
                    step_index INTEGER,
                    tool_name VARCHAR(120),
                    arguments JSON,
                    result JSON,
                    ok BOOLEAN,
                    error TEXT,
                    permission VARCHAR(20),
                    duration_ms FLOAT,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text("INSERT INTO tasks (id, conversation_id) VALUES ('tsk_legacy', 'cnv_legacy')")
        )
        connection.execute(
            text(
                """
                INSERT INTO tool_executions
                    (id, task_id, step_index, tool_name, arguments, result, ok, error,
                     permission, duration_ms, created_at)
                VALUES
                    ('exe_legacy', 'tsk_legacy', 1, 'files.read_file', '{"path": "a.txt"}',
                     :result, 1, NULL, 'read', 12.5, '2026-01-01 10:00:00')
                """
            ),
            {"result": json.dumps({"ok": True, "verified": True, "evidence": "read it"})},
        )
        apply_schema_migrations(connection)

        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(tool_executions)"))
        }
        assert {
            "conversation_id",
            "status",
            "verified",
            "evidence",
            "hint",
            "approval_status",
            "started_at",
            "completed_at",
        } <= columns

        row = connection.execute(
            text("SELECT conversation_id, status, verified, started_at, completed_at FROM tool_executions")
        ).fetchone()
        assert row[0] == "cnv_legacy"
        assert row[1] == "completed"
        assert row[2] == 1
        assert row[3] is not None and row[4] is not None

        message_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(messages)"))
        }
        assert "task_id" in message_columns

    # Running the migration again must be a no-op.
    with engine.begin() as connection:
        apply_schema_migrations(connection)
        assert (
            connection.execute(text("SELECT COUNT(*) FROM tool_executions")).scalar_one() == 1
        )


async def test_migration_is_idempotent_on_current_schema(services):
    """create_all + migration on an already-current database adds nothing."""
    from sqlalchemy import inspect

    # ``services`` already ran ``create_all`` and the migration once. Running the
    # same migration again through a fresh sync engine must not alter anything.
    database = services.db.url.replace("sqlite+aiosqlite:///", "")
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        before = {
            table: {column["name"] for column in inspect(connection).get_columns(table)}
            for table in ("messages", "tool_executions")
        }
        apply_schema_migrations(connection)
        after = {
            table: {column["name"] for column in inspect(connection).get_columns(table)}
            for table in ("messages", "tool_executions")
        }
    assert before == after
