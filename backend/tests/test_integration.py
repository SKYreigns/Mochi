"""End-to-end backend scenarios using real services, tools and a local test site."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.enums import TaskStatus, ToolPermission
from app.database.models import Approval, Task, ToolExecution
from app.providers.base import ProviderResponse, ToolCall
from app.tools.base import EmptyArgs, build_tool, ok
from tests.helpers import (
    ScriptedProvider,
    install_provider,
    tool_response,
    wait_for_task,
    wait_for_task_status,
    wait_until,
)


# --- controlled LMS test site ------------------------------------------------
_PAGES = {
    "/": (
        "<html><head><title>Test Learning Portal</title></head><body>"
        "<h1>My courses</h1>"
        "<a href='/course/physics'>Course: Physics 101</a>"
        "<a href='/profile'>Profile</a>"
        "</body></html>"
    ),
    "/course/physics": (
        "<html><head><title>Physics 101</title></head><body>"
        "<h1>Physics 101</h1>"
        "<a href='/assignments/thermo'>Assignment: Thermodynamics Lab</a>"
        "<p>Due: Friday, 5:00 PM</p>"
        "</body></html>"
    ),
    "/assignments/thermo": (
        "<html><head><title>Thermodynamics Lab</title></head><body>"
        "<h1>Thermodynamics Lab</h1>"
        "<p>Due: Friday, 5:00 PM</p>"
        "<p>Measure the heat capacity of water and submit a short lab report.</p>"
        "<a href='/files/lab-instructions.pdf'>Download lab instructions (PDF)</a>"
        "</body></html>"
    ),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = _PAGES.get(self.path.split("?")[0])
        if body is None:
            self.send_error(404)
            return
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # silence test server logging
        return


@pytest.fixture
def lms_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --- Scenario 1 --------------------------------------------------------------
async def test_file_search_end_to_end(services, sandbox):
    folder = sandbox / "documents"
    folder.mkdir()
    (folder / "quarterly_report.txt").write_text("revenue grew", encoding="utf-8")
    (folder / "unrelated.md").write_text("notes", encoding="utf-8")

    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(
                    id="call_1",
                    name="files.search",
                    arguments={"directory": str(folder), "query": "quarterly"},
                )
            ),
            ProviderResponse(content="I found quarterly_report.txt in your documents folder."),
        ]
    )
    await install_provider(services, provider)

    task = await services.tasks.start(instruction="Find my quarterly report", trigger="test")
    finished = await wait_for_task(services, task.id)

    assert finished.status == TaskStatus.COMPLETED.value
    assert finished.result and "quarterly_report.txt" in finished.result
    assert "Execution evidence" in finished.result
    stats = finished.metadata_json["statistics"]
    assert stats["succeeded"] == 1 and stats["failed"] == 0

    async with services.db.session() as session:
        executions = (
            await session.execute(select(ToolExecution).where(ToolExecution.task_id == task.id))
        ).scalars().all()
    assert len(executions) == 1
    assert executions[0].tool_name == "files.search" and executions[0].ok is True


# --- Scenario 2 --------------------------------------------------------------
async def test_destructive_action_blocked_then_approved_via_api(api, services, sandbox):
    target = sandbox / "old-draft.txt"
    target.write_text("obsolete", encoding="utf-8")

    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="files.delete", arguments={"path": str(target)})),
            ProviderResponse(content="Deleted the obsolete draft."),
        ]
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Delete my old draft", trigger="test")

    approval_id = None

    async def pending_approval():
        nonlocal approval_id
        response = await api.get("/api/approvals", params={"status": "pending"})
        rows = [item for item in response.json()["approvals"] if item["task_id"] == task.id]
        if rows:
            approval_id = rows[0]["id"]
            return True
        return False

    await wait_until(pending_approval, timeout=10)
    waiting = await wait_for_task_status(services, task.id, {"waiting_for_approval"})
    assert waiting.status == "waiting_for_approval"
    assert target.exists(), "the destructive action must not run before approval"

    detail = await api.get(f"/api/tasks/{task.id}")
    assert detail.status_code == 200
    assert any(item["id"] == approval_id for item in detail.json()["approvals"])

    decided = await api.post(f"/api/approvals/{approval_id}/decide", json={"approved": True, "note": "ok"})
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    finished = await wait_for_task(services, task.id)
    assert finished.status == TaskStatus.COMPLETED.value
    assert not target.exists()
    trash = services.app_settings.data_dir / "trash"
    assert any(trash.rglob("old-draft.txt")), "deletion should be recoverable from trash"


# --- Scenario 3 --------------------------------------------------------------
async def test_workflow_recovers_after_failed_step(services, sandbox):
    async with services.db.session() as session:
        workflow = await services.workflows.create_workflow(
            session,
            name="Recovery workflow",
            steps=[
                {
                    "name": "Read a missing file",
                    "kind": "tool",
                    "config": {
                        "tool": "files.read_file",
                        "arguments": {"path": str(sandbox / "missing-input.txt")},
                        "on_error": "continue",
                    },
                },
                {
                    "name": "Create fallback output",
                    "kind": "tool",
                    "config": {
                        "tool": "files.create_file",
                        "arguments": {
                            "path": str(sandbox / "recovered.txt"),
                            "content": "recovered after failure",
                        },
                    },
                },
            ],
        )
        workflow_id = workflow.id

    result = await services.workflows.run(workflow_id)
    assert result["status"] == "completed"
    statuses = [step["status"] for step in result["results"]]
    assert statuses == ["completed", "completed"]
    assert result["results"][0]["result"]["ok"] is False
    assert result["results"][1]["result"]["ok"] is True
    assert (sandbox / "recovered.txt").read_text(encoding="utf-8") == "recovered after failure"


async def test_workflow_reports_failure_without_recovery(services, sandbox):
    async with services.db.session() as session:
        workflow = await services.workflows.create_workflow(
            session,
            name="Failing workflow",
            steps=[
                {
                    "name": "Missing read",
                    "kind": "tool",
                    "config": {
                        "tool": "files.read_file",
                        "arguments": {"path": str(sandbox / "nowhere.txt")},
                    },
                },
                {
                    "name": "Should not run",
                    "kind": "tool",
                    "config": {
                        "tool": "files.create_file",
                        "arguments": {"path": str(sandbox / "never.txt"), "content": "x"},
                    },
                },
            ],
        )
        workflow_id = workflow.id

    result = await services.workflows.run(workflow_id)
    assert result["status"] == "failed"
    assert len(result["results"]) == 1
    assert not (sandbox / "never.txt").exists()


# --- Scenario 4 --------------------------------------------------------------
async def test_task_cancellation_via_api_stops_execution(api, services):
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_handler(args, ctx):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            finished.set()
        return ok({"unreachable": True})

    services.registry.register(
        build_tool(
            name="test.integration-slow",
            description="Long-running tool used to verify cancellation.",
            input_model=EmptyArgs,
            handler=slow_handler,
            permission=ToolPermission.READ,
            timeout=120,
        )
    )
    provider = ScriptedProvider(
        [
            tool_response(ToolCall(id="call_1", name="test.integration-slow", arguments={})),
            ProviderResponse(content="This should never be reached."),
        ]
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Run a slow operation", trigger="test")

    await asyncio.wait_for(started.wait(), timeout=10)
    response = await api.post(f"/api/tasks/{task.id}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] is True

    finished_task = await wait_for_task(services, task.id, timeout=10)
    assert finished_task.status == TaskStatus.CANCELLED.value
    assert finished_task.cancelled_at is not None
    await asyncio.wait_for(finished.wait(), timeout=5)

    async with services.db.session() as session:
        executions = (
            await session.execute(select(ToolExecution).where(ToolExecution.task_id == task.id))
        ).scalars().all()
    assert not any(item.ok for item in executions), "a cancelled task must not report a successful action"


# --- Scenario 5 --------------------------------------------------------------
async def test_lms_lookup_against_controlled_site(services, lms_site):
    course_url = f"{lms_site}/course/physics"
    assignment_url = f"{lms_site}/assignments/thermo"
    async with services.db.session() as session:
        await services.settings.set(
            session,
            "lms",
            {
                "base_url": lms_site,
                "institution": "Controlled Test Site",
                "courses": [],
                "selectors": {},
            },
        )

    provider = ScriptedProvider(
        [
            tool_response(
                ToolCall(
                    id="call_1",
                    name="lms.list_assignments",
                    arguments={"course_url": course_url},
                )
            ),
            tool_response(
                ToolCall(
                    id="call_2",
                    name="lms.read_assignment",
                    arguments={"url": assignment_url},
                )
            ),
            ProviderResponse(
                content="The Thermodynamics Lab is due Friday at 5:00 PM and includes a PDF attachment."
            ),
        ]
    )
    await install_provider(services, provider)
    task = await services.tasks.start(instruction="Check my LMS for the Thermodynamics Lab", trigger="test")
    finished = await wait_for_task(services, task.id, timeout=45)

    assert finished.status == TaskStatus.COMPLETED.value, finished.error
    assert "Thermodynamics Lab" in (finished.result or "")
    assert "Friday, 5:00 PM" in (finished.result or "")

    async with services.db.session() as session:
        executions = (
            await session.execute(
                select(ToolExecution).where(ToolExecution.task_id == task.id).order_by(ToolExecution.created_at)
            )
        ).scalars().all()
    assert [item.tool_name for item in executions] == ["lms.list_assignments", "lms.read_assignment"]
    assert all(item.ok for item in executions)

    listing = executions[0].result
    assert listing["data"]["count"] >= 1
    found = listing["data"]["assignments"][0]
    assert "Thermodynamics Lab" in found["title"]

    reading = executions[1].result["data"]
    assert reading["title"] == "Thermodynamics Lab"
    assert "Friday" in reading["due"]
    assert reading["attachments"] and reading["attachments"][0]["url"].endswith("lab-instructions.pdf")

    # The local test server was used; this never touches a real institution.
    assert "127.0.0.1" in found["url"]
