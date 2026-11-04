"""Shared API dependencies and serializers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.core.container import AppServices
from app.core.errors import JarvisError
from app.database.models import Approval, Task
from app.tools.base import ToolContext, ToolResult


def services_from(request: Request) -> AppServices:
    return request.app.state.services


async def run_direct_tool(
    services: AppServices,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Execute a user-initiated UI action through the same permission engine."""
    tool = services.registry.get(tool_name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{tool_name}'")
    decision = await services.permissions.evaluate(tool)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail={"message": decision.reason, "mode": decision.mode})
    if decision.requires_approval:
        raise HTTPException(
            status_code=409,
            detail={"message": decision.reason, "requires_approval": True, "tool": tool_name},
        )
    async with services.db.session() as session:
        ctx = ToolContext(session=session, task_id="ui", conversation_id=None, services=services)
        result, _duration = await services.registry.execute(tool_name, arguments, ctx)
    if not result.ok:
        raise HTTPException(
            status_code=400,
            detail={"message": result.error, "hint": result.hint, "retryable": result.retryable},
        )
    return result


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "conversation_id": task.conversation_id,
        "instruction": task.instruction,
        "objective": task.objective,
        "plan": task.plan,
        "status": task.status,
        "current_step": task.current_step,
        "permission_mode": task.permission_mode,
        "trigger": task.trigger,
        "result": task.result,
        "error": task.error,
        "metadata": task.metadata_json,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "cancelled_at": task.cancelled_at.isoformat() if task.cancelled_at else None,
    }


def approval_to_dict(approval: Approval, task: Task | None = None) -> dict[str, Any]:
    return {
        "id": approval.id,
        "task_id": approval.task_id,
        "tool_name": approval.tool_name,
        "arguments": approval.arguments,
        "reason": approval.reason,
        "permission": approval.permission,
        "status": approval.status,
        "decision_note": approval.decision_note,
        "requested_at": approval.requested_at.isoformat() if approval.requested_at else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "instruction": task.instruction if task else None,
    }


def jarvis_http_error(exc: JarvisError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.to_dict())
