"""Tasks, approvals and audit trail endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import approval_to_dict, services_from, task_to_dict
from app.database.models import Approval, AuditLog, Task, TaskStep, ToolExecution

router = APIRouter(tags=["tasks"])


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = ""


@router.get("/api/tasks")
async def list_tasks(
    request: Request,
    limit: int = Query(default=50, ge=1, le=300),
    status: str | None = None,
) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        statement = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if status:
            statement = statement.where(Task.status == status)
        tasks = (await session.execute(statement)).scalars().all()
        return {"tasks": [task_to_dict(task) for task in tasks]}


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        steps = (
            await session.execute(select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.index))
        ).scalars().all()
        executions = (
            await session.execute(
                select(ToolExecution).where(ToolExecution.task_id == task_id).order_by(ToolExecution.created_at)
            )
        ).scalars().all()
        approvals = (
            await session.execute(
                select(Approval).where(Approval.task_id == task_id).order_by(Approval.requested_at.desc())
            )
        ).scalars().all()
        return {
            "task": task_to_dict(task),
            "steps": [
                {
                    "id": step.id,
                    "index": step.index,
                    "kind": step.kind,
                    "title": step.title,
                    "status": step.status,
                    "detail": step.detail,
                    "started_at": step.started_at.isoformat() if step.started_at else None,
                    "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                }
                for step in steps
            ],
            "executions": [
                {
                    "id": item.id,
                    "step_index": item.step_index,
                    "tool_name": item.tool_name,
                    "arguments": item.arguments,
                    "ok": item.ok,
                    "error": item.error,
                    "permission": item.permission,
                    "duration_ms": round(item.duration_ms, 1),
                    "created_at": item.created_at.isoformat(),
                }
                for item in executions
            ],
            "approvals": [approval_to_dict(item, task) for item in approvals],
        }


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    cancelled = await services.tasks.cancel(task_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail="Task is not running")
    return {"cancelled": True, "task_id": task_id}


@router.post("/api/tasks/stop")
async def emergency_stop(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return await services.tasks.emergency_stop()


@router.post("/api/tasks/resume")
async def resume_after_stop(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return await services.tasks.resume()


@router.get("/api/approvals")
async def list_approvals(
    request: Request,
    status: Literal["pending", "approved", "denied", "expired", "cancelled"] | None = "pending",
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        statement = select(Approval).order_by(Approval.requested_at.desc()).limit(limit)
        if status:
            statement = statement.where(Approval.status == status)
        approvals = (await session.execute(statement)).scalars().all()
        out = []
        for approval in approvals:
            task = await session.get(Task, approval.task_id)
            out.append(approval_to_dict(approval, task))
        return {"approvals": out}


@router.post("/api/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, body: ApprovalDecision, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        try:
            approval = await services.permissions.decide(
                session, approval_id, approved=body.approved, note=body.note
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "id": approval.id,
            "status": approval.status,
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        }


@router.get("/api/audit")
async def audit_log(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        rows = (
            await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
        ).scalars().all()
        return {
            "logs": [
                {
                    "id": row.id,
                    "event": row.event,
                    "actor": row.actor,
                    "action": row.action,
                    "target": row.target,
                    "outcome": row.outcome,
                    "detail": row.detail,
                    "task_id": row.task_id,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
        }
