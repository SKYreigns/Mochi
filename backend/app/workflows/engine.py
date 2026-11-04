"""Workflow engine: reusable, conditional, approval-aware multi-step runs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import TaskStatus, ToolPermission
from app.core.errors import EmergencyStopError, PermissionDeniedError
from app.core.events import EventBus
from app.core.logging import audit, get_logger, redact
from app.database.database import Database
from app.database.models import ScheduledJob, Task, Workflow, WorkflowRun, WorkflowStep
from app.permissions.engine import PermissionEngine
from app.tools.base import ToolContext, ToolRegistry, fail

log = get_logger("workflows")


class WorkflowEngine:
    def __init__(self, services: Any) -> None:
        self.services = services
        self._running: set[str] = set()

    @property
    def db(self) -> Database:
        return self.services.db

    @property
    def events(self) -> EventBus:
        return self.services.events

    async def list_workflows(self, session: AsyncSession) -> list[dict[str, Any]]:
        workflows = (await session.execute(select(Workflow).order_by(Workflow.created_at.desc()))).scalars().all()
        result = []
        for workflow in workflows:
            steps = (
                await session.execute(
                    select(WorkflowStep).where(WorkflowStep.workflow_id == workflow.id).order_by(WorkflowStep.index)
                )
            ).scalars().all()
            result.append(
                {
                    "id": workflow.id,
                    "name": workflow.name,
                    "description": workflow.description,
                    "enabled": workflow.enabled,
                    "steps": [
                        {"id": step.id, "index": step.index, "name": step.name, "kind": step.kind, "config": step.config}
                        for step in steps
                    ],
                    "created_at": workflow.created_at.isoformat(),
                }
            )
        return result

    async def create_workflow(
        self,
        session: AsyncSession,
        *,
        name: str,
        description: str = "",
        steps: list[dict[str, Any]] | None = None,
        enabled: bool = True,
    ) -> Workflow:
        workflow = Workflow(name=name, description=description, definition={}, enabled=enabled)
        session.add(workflow)
        await session.flush()
        for index, step in enumerate(steps or []):
            session.add(
                WorkflowStep(
                    workflow_id=workflow.id,
                    index=index,
                    name=str(step.get("name") or f"Step {index + 1}")[:200],
                    kind=str(step.get("kind") or "instruction"),
                    config=step.get("config") or {},
                )
            )
        await session.commit()
        return workflow

    async def update_workflow(
        self,
        session: AsyncSession,
        workflow_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> Workflow | None:
        workflow = await session.get(Workflow, workflow_id)
        if workflow is None:
            return None
        if name is not None:
            workflow.name = name
        if description is not None:
            workflow.description = description
        if enabled is not None:
            workflow.enabled = enabled
        if steps is not None:
            existing = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id))
            ).scalars().all()
            for step in existing:
                await session.delete(step)
            for index, step in enumerate(steps):
                session.add(
                    WorkflowStep(
                        workflow_id=workflow_id,
                        index=index,
                        name=str(step.get("name") or f"Step {index + 1}")[:200],
                        kind=str(step.get("kind") or "instruction"),
                        config=step.get("config") or {},
                    )
                )
        await session.commit()
        return workflow

    async def delete_workflow(self, session: AsyncSession, workflow_id: str) -> bool:
        workflow = await session.get(Workflow, workflow_id)
        if workflow is None:
            return False
        steps = (
            await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id))
        ).scalars().all()
        for step in steps:
            await session.delete(step)
        runs = (
            await session.execute(select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id))
        ).scalars().all()
        for run in runs:
            await session.delete(run)
        jobs = (
            await session.execute(select(ScheduledJob).where(ScheduledJob.workflow_id == workflow_id))
        ).scalars().all()
        for job in jobs:
            job.workflow_id = None
            job.enabled = False
        await session.delete(workflow)
        await session.commit()
        return True

    async def run(self, workflow_id: str, trigger: str = "manual") -> dict[str, Any]:
        async with self.db.session() as session:
            workflow = await session.get(Workflow, workflow_id)
            if workflow is None:
                raise ValueError("Workflow not found")
            steps = (
                await session.execute(
                    select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id).order_by(WorkflowStep.index)
                )
            ).scalars().all()
            run = WorkflowRun(workflow_id=workflow_id, status="running", started_at=datetime.now(timezone.utc))
            session.add(run)
            await session.flush()
            parent = Task(
                instruction=f"[Workflow] {workflow.name}",
                status=TaskStatus.RUNNING.value,
                permission_mode=self.services.settings.permission_mode,
                trigger=trigger,
                plan=[{"title": step.name} for step in steps],
                metadata_json={"workflow_id": workflow_id, "workflow_run_id": run.id},
                started_at=datetime.now(timezone.utc),
            )
            session.add(parent)
            await session.commit()
            run_id = run.id
            parent_id = parent.id
            workflow_name = workflow.name
            step_data = [
                {"id": step.id, "name": step.name, "kind": step.kind, "config": dict(step.config or {})}
                for step in steps
            ]

        self._running.add(run_id)
        await self.events.publish("workflow.started", {"workflow_id": workflow_id, "run_id": run_id, "name": workflow_name})
        results: list[dict[str, Any]] = []
        final_status = "completed"
        error: str | None = None
        last_result: dict[str, Any] = {}

        try:
            for index, step in enumerate(step_data):
                self.services.emergency.check()
                if not _condition_met(step["config"].get("condition"), last_result):
                    results.append({"index": index, "name": step["name"], "status": "skipped", "reason": "condition not met"})
                    continue
                await self.events.publish(
                    "workflow.step",
                    {"run_id": run_id, "workflow_id": workflow_id, "index": index, "name": step["name"], "kind": step["kind"]},
                )
                try:
                    result = await self._execute_step(parent_id, step, index)
                    last_result = result
                    results.append({"index": index, "name": step["name"], "status": "completed", "result": result})
                    if not result.get("ok", True) and step["config"].get("on_error", "stop") == "stop":
                        final_status = "failed"
                        error = result.get("error") or f"Step '{step['name']}' failed."
                        break
                except PermissionDeniedError as exc:
                    results.append({"index": index, "name": step["name"], "status": "denied", "error": exc.message})
                    final_status = "blocked"
                    error = exc.message
                    break
                except asyncio.CancelledError:
                    final_status = "cancelled"
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.exception("Workflow step failed")
                    results.append({"index": index, "name": step["name"], "status": "failed", "error": str(exc)})
                    if step["config"].get("on_error", "stop") == "stop":
                        final_status = "failed"
                        error = str(exc)
                        break
        except EmergencyStopError as exc:
            final_status = "cancelled"
            error = str(exc)
        finally:
            self._running.discard(run_id)
            finished = datetime.now(timezone.utc)
            async with self.db.session() as session:
                run = await session.get(WorkflowRun, run_id)
                if run is not None:
                    run.status = final_status
                    run.results = results
                    run.error = redact(error or "", 3000) or None
                    run.finished_at = finished
                parent = await session.get(Task, parent_id)
                if parent is not None:
                    parent.status = (
                        TaskStatus.COMPLETED.value if final_status == "completed" else (
                            TaskStatus.CANCELLED.value if final_status == "cancelled" else TaskStatus.FAILED.value
                        )
                    )
                    parent.completed_at = finished
                    parent.result = _summary(workflow_name, results, final_status)
                    parent.error = error
                await session.commit()
            await self.events.publish(
                "workflow.completed",
                {
                    "run_id": run_id,
                    "workflow_id": workflow_id,
                    "status": final_status,
                    "results": results,
                    "error": error,
                },
            )
            audit("workflow.completed", workflow_id=workflow_id, run_id=run_id, status=final_status)

        return {
            "run_id": run_id,
            "task_id": parent_id,
            "workflow_id": workflow_id,
            "status": final_status,
            "results": results,
            "error": error,
        }

    async def _execute_step(self, parent_task_id: str, step: dict[str, Any], index: int) -> dict[str, Any]:
        kind = step["kind"]
        config = step["config"] or {}

        if kind == "instruction":
            instruction = str(config.get("instruction") or "").strip()
            if not instruction:
                return {"ok": False, "error": "Instruction step has no instruction text."}
            auto = set(config.get("auto_approve_tools") or [])
            task = await self.services.tasks.run_and_wait(
                instruction=instruction,
                trigger="workflow",
                auto_approved=auto,
                title=f"Workflow step: {step['name']}",
            )
            ok = task.status == TaskStatus.COMPLETED.value
            return {
                "ok": ok,
                "task_id": task.id,
                "status": task.status,
                "result": redact(task.result or "", 4000),
                "error": task.error if not ok else None,
            }

        if kind == "tool":
            tool_name = str(config.get("tool") or "")
            arguments = config.get("arguments") or {}
            tool = self.services.registry.get(tool_name)
            if tool is None:
                return {"ok": False, "error": f"Unknown tool '{tool_name}'."}
            async with self.db.session() as session:
                decision = await self.services.permissions.evaluate(
                    tool, auto_approved=set(config.get("auto_approve_tools") or [])
                )
                if not decision.allowed:
                    return {"ok": False, "error": decision.reason}
                if decision.requires_approval:
                    await self.services.permissions.request_approval(
                        session,
                        task_id=parent_task_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        permission=tool.permission.value,
                        reason=decision.reason,
                        timeout=self.services.settings.app.approval_timeout_seconds,
                    )
                ctx = ToolContext(
                    session=session,
                    task_id=parent_task_id,
                    conversation_id=None,
                    services=self.services,
                    auto_approved=set(config.get("auto_approve_tools") or []),
                )
                result, duration = await self.services.registry.execute(tool_name, arguments, ctx)
            return {
                "ok": result.ok,
                "tool": tool_name,
                "verified": result.verified,
                "evidence": result.evidence,
                "data": result.data,
                "error": result.error,
                "duration_ms": round(duration, 1),
            }

        if kind == "approval":
            reason = str(config.get("reason") or "Workflow checkpoint requires approval.")
            async with self.db.session() as session:
                approved, note = await self.services.permissions.request_approval(
                    session,
                    task_id=parent_task_id,
                    tool_name="workflow.approval",
                    arguments={"workflow_step": step["name"], "index": index},
                    permission=ToolPermission.WRITE.value,
                    reason=reason,
                    timeout=self.services.settings.app.approval_timeout_seconds,
                )
            return {"ok": approved, "note": note}

        return {"ok": False, "error": f"Unsupported workflow step kind '{kind}'."}


def _condition_met(condition: Any, last_result: dict[str, Any]) -> bool:
    if not condition:
        return True
    if isinstance(condition, str):
        path, expected = condition.split("==", 1)
        condition = {"path": path.strip(), "equals": expected.strip()}
    path = str(condition.get("path") or "")
    value: Any = last_result
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            value = None
            break
    if "equals" in condition:
        return str(value).lower() == str(condition["equals"]).lower()
    if "exists" in condition:
        return (value is not None) == bool(condition["exists"])
    return bool(value)


def _summary(workflow_name: str, results: list[dict[str, Any]], status: str) -> str:
    lines = [f"Workflow '{workflow_name}' finished with status {status}."]
    for item in results:
        detail = item.get("error") or (item.get("result") or {}).get("result") or ""
        lines.append(f"- Step {item['index'] + 1} {item['name']}: {item['status']}{(' — ' + str(detail)[:180]) if detail else ''}")
    return "\n".join(lines)
