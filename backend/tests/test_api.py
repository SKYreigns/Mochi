"""HTTP API: every route group, validation and error responses."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database.models import Assignment, Conversation, Task


async def test_health_tools_and_root(api):
    health = await api.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["services"]["tools"] >= 70

    tools = await api.get("/api/tools")
    assert tools.status_code == 200
    body = tools.json()
    assert body["count"] == len(body["tools"])
    names = {tool["name"] for tool in body["tools"]}
    assert {"files.read_file", "browser.open_url", "documents.create_pdf"} <= names

    root = await api.get("/")
    assert root.status_code == 200 and root.json()["app"] == "JARVIS"


async def test_settings_get_and_patch(api, sandbox):
    response = await api.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["permission_mode"] in ("observe", "assisted", "autonomous")
    assert "has_api_key" in body["llm"]

    unknown = await api.patch("/api/settings", json={"updates": {"nonsense": True}})
    assert unknown.status_code == 422

    bad_mode = await api.patch("/api/settings", json={"updates": {"permission_mode": "god"}})
    assert bad_mode.status_code == 422

    bad_dir = await api.patch("/api/settings", json={"updates": {"allowed_directories": ["Z:/does/not/exist"]}})
    assert bad_dir.status_code == 422

    ok = await api.patch(
        "/api/settings",
        json={"updates": {"permission_mode": "observe", "allowed_directories": [str(sandbox)]}},
    )
    assert ok.status_code == 200
    assert ok.json()["settings"]["permission_mode"] == "observe"

    restored = await api.patch("/api/settings", json={"updates": {"permission_mode": "assisted"}})
    assert restored.json()["settings"]["permission_mode"] == "assisted"


async def test_settings_provider_and_models(api):
    provider = await api.get("/api/settings/provider")
    assert provider.status_code == 200
    assert provider.json()["provider"]["provider"] == "mock"

    models = await api.get("/api/settings/models")
    assert models.status_code == 200
    assert models.json()["models"][0]["id"] == "mock"


async def test_memory_api_crud(api):
    created = await api.post(
        "/api/memory",
        json={"key": "preferred_format", "value": "Use docx for essays", "kind": "preference", "tags": "writing"},
    )
    assert created.status_code == 200
    memory = created.json()["memory"]
    assert memory["key"] == "preferred_format"

    listed = await api.get("/api/memory")
    assert listed.status_code == 200 and any(item["id"] == memory["id"] for item in listed.json()["memories"])

    searched = await api.get("/api/memory", params={"query": "essays"})
    assert any(item["id"] == memory["id"] for item in searched.json()["memories"])

    updated = await api.patch(f"/api/memory/{memory['id']}", json={"value": "Use pdf for essays", "pinned": True})
    assert updated.status_code == 200
    assert updated.json()["memory"]["pinned"] is True

    deleted = await api.delete(f"/api/memory/{memory['id']}")
    assert deleted.status_code == 200
    assert (await api.delete(f"/api/memory/{memory['id']}")).status_code == 404

    empty = await api.post("/api/memory", json={"key": "", "value": ""})
    assert empty.status_code == 422

    secret = await api.post("/api/memory", json={"key": "key", "value": "api_key=sk-abcdefghijklmnopqrstuvwxyz"})
    assert secret.status_code == 422


async def test_chat_conversation_flow(api, services):
    empty = await api.post("/api/chat", json={"message": ""})
    assert empty.status_code == 422

    created = await api.post("/api/chat/conversations", json={"title": "Physics help"})
    assert created.status_code == 200
    conversation_id = created.json()["id"]

    response = await api.post(
        "/api/chat",
        json={"message": "What can you do?", "conversation_id": conversation_id},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    from tests.helpers import wait_for_task

    task = await wait_for_task(services, task_id)
    assert task.status in ("completed", "failed")

    conversations = await api.get("/api/chat/conversations")
    assert conversations.status_code == 200
    assert any(item["id"] == conversation_id for item in conversations.json()["conversations"])

    messages = await api.get(f"/api/chat/conversations/{conversation_id}/messages")
    assert messages.status_code == 200
    assert any(message["content"] == "What can you do?" for message in messages.json()["messages"])

    chat_task = await api.get(f"/api/chat/tasks/{task_id}")
    assert chat_task.status_code == 200
    assert chat_task.json()["task"]["id"] == task_id

    archived = await api.delete(f"/api/chat/conversations/{conversation_id}")
    assert archived.status_code == 200
    assert (await api.get("/api/chat/conversations/does-not-exist/messages")).status_code == 404


async def test_files_api(api, sandbox):
    target = sandbox / "api-note.txt"
    target.write_text("api file body", encoding="utf-8")

    roots = await api.get("/api/files/roots")
    assert roots.status_code == 200
    assert str(sandbox) in roots.json()["roots"]

    listing = await api.get("/api/files/list", params={"path": str(sandbox)})
    assert listing.status_code == 200
    assert any(item["name"] == "api-note.txt" for item in listing.json()["data"]["entries"])

    read = await api.get("/api/files/read", params={"path": str(target)})
    assert read.status_code == 200 and "api file body" in read.json()["data"]["content"]

    metadata = await api.get("/api/files/metadata", params={"path": str(target)})
    assert metadata.status_code == 200 and metadata.json()["data"]["is_file"] is True

    download = await api.get("/api/files/download", params={"path": str(target)})
    assert download.status_code == 200 and download.content == b"api file body"

    upload = await api.post("/api/files/upload", files={"file": ("upload.txt", b"uploaded", "text/plain")})
    assert upload.status_code == 200
    assert upload.json()["size"] == 8

    search = await api.get("/api/files/search", params={"query": "api-note", "directory": str(sandbox)})
    assert search.status_code == 200
    assert search.json()["data"]["count"] >= 1

    preview = await api.post("/api/files/organize/preview", json={"directory": str(sandbox)})
    assert preview.status_code == 200

    outside = await api.get("/api/files/read", params={"path": "C:/Windows/win.ini"})
    assert outside.status_code in (400, 403)

    sensitive = sandbox / ".env"
    sensitive.write_text("TOKEN=123", encoding="utf-8")
    blocked = await api.get("/api/files/download", params={"path": str(sensitive)})
    assert blocked.status_code == 403


async def test_tasks_approvals_and_audit_api(api, services):
    tasks = await api.get("/api/tasks")
    assert tasks.status_code == 200 and "tasks" in tasks.json()

    missing = await api.get("/api/tasks/does-not-exist")
    assert missing.status_code == 404

    stop = await api.post("/api/tasks/stop")
    assert stop.status_code == 200 and stop.json()["stopped"] is True
    resume = await api.post("/api/tasks/resume")
    assert resume.status_code == 200 and resume.json()["stopped"] is False

    approvals = await api.get("/api/approvals")
    assert approvals.status_code == 200 and "approvals" in approvals.json()

    audit = await api.get("/api/audit")
    assert audit.status_code == 200 and "logs" in audit.json()


async def test_task_detail_api(api, services):
    task = await services.tasks.start(instruction="API detail task", trigger="test")
    from tests.helpers import wait_for_task

    await wait_for_task(services, task.id)
    detail = await api.get(f"/api/tasks/{task.id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["task"]["id"] == task.id
    assert isinstance(body["steps"], list) and isinstance(body["executions"], list)

    cancel = await api.post(f"/api/tasks/{task.id}/cancel")
    assert cancel.status_code == 409  # already finished


async def test_workflow_api_crud_and_run(api, services):
    created = await api.post(
        "/api/workflows",
        json={
            "name": "API workflow",
            "description": "created by tests",
            "steps": [{"name": "List files", "kind": "tool", "config": {"tool": "files.allowed_roots", "arguments": {}}}],
        },
    )
    assert created.status_code == 200
    workflow_id = created.json()["id"]

    listed = await api.get("/api/workflows")
    assert any(item["id"] == workflow_id for item in listed.json()["workflows"])

    updated = await api.put(f"/api/workflows/{workflow_id}", json={"description": "edited"})
    assert updated.status_code == 200

    run = await api.post(f"/api/workflows/{workflow_id}/run")
    assert run.status_code == 200 and run.json()["started"] is True

    import asyncio

    from sqlalchemy import select

    from app.database.models import WorkflowRun

    for _ in range(100):
        async with services.db.session() as session:
            runs = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id))
            ).scalars().all()
            if runs and runs[0].status != "running":
                assert runs[0].status == "completed"
                break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("workflow run did not finish")

    bad = await api.put("/api/workflows/does-not-exist", json={"name": "x"})
    assert bad.status_code == 404

    deleted = await api.delete(f"/api/workflows/{workflow_id}")
    assert deleted.status_code == 200
    assert (await api.delete(f"/api/workflows/{workflow_id}")).status_code == 404


async def test_schedule_api(api, services):
    invalid = await api.post("/api/schedules", json={"name": "bad", "schedule_type": "daily"})
    assert invalid.status_code == 422

    past = await api.post(
        "/api/schedules",
        json={"name": "past", "schedule_type": "once", "run_at": "2020-01-01T00:00:00Z"},
    )
    assert past.status_code == 422

    created = await api.post(
        "/api/schedules",
        json={"name": "interval", "instruction": "Say hello", "schedule_type": "interval", "interval_minutes": 30},
    )
    assert created.status_code == 200
    job_id = created.json()["id"]

    listed = await api.get("/api/schedules")
    assert any(item["id"] == job_id for item in listed.json()["schedules"])

    patched = await api.patch(f"/api/schedules/{job_id}", json={"enabled": False})
    assert patched.status_code == 200

    ran = await api.post(f"/api/schedules/{job_id}/run")
    assert ran.status_code == 200

    await api.delete(f"/api/schedules/{job_id}")
    assert (await api.delete(f"/api/schedules/{job_id}")).status_code == 404


async def test_voice_system_and_live_api(api):
    voice = await api.get("/api/voice/status")
    assert voice.status_code == 200 and "enabled" in voice.json()

    metrics = await api.get("/api/system/metrics")
    assert metrics.status_code == 200 and "cpu_percent" in metrics.json()

    os_info = await api.get("/api/system/os")
    assert os_info.status_code == 200 and os_info.json()["data"]

    live = await api.get("/api/live/state")
    assert live.status_code == 200 and "permission_mode" in live.json()

    browser = await api.get("/api/browser/state")
    assert browser.status_code == 200

    tabs = await api.get("/api/browser/tabs")
    assert tabs.status_code == 200
    assert tabs.json()["running"] is False

    screenshot = await api.get("/api/live/screenshots/..%2Fsecret.png")
    assert screenshot.status_code in (400, 404)


async def test_assignments_api(api, services):
    async with services.db.session() as session:
        assignment = Assignment(subject="Physics", title="Lab 1", instructions="Do the lab")
        session.add(assignment)
        await session.commit()
        assignment_id = assignment.id

    listed = await api.get("/api/assignments")
    assert listed.status_code == 200
    assert any(item["id"] == assignment_id for item in listed.json()["assignments"])

    updated = await api.patch(f"/api/assignments/{assignment_id}", json={"status": "in_progress"})
    assert updated.status_code == 200
    assert updated.json()["assignment"]["status"] == "in_progress"

    no_url = await api.post(f"/api/assignments/{assignment_id}/open")
    assert no_url.status_code == 422

    deleted = await api.delete(f"/api/assignments/{assignment_id}")
    assert deleted.status_code == 200
    assert (await api.patch(f"/api/assignments/{assignment_id}", json={"status": "x"})).status_code == 404
