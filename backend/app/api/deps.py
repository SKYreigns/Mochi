"""Shared API dependencies and serializers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.core.container import AppServices
from app.core.errors import JarvisError
from app.database.models import Approval, Task, ToolExecution
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


def execution_to_dict(execution: ToolExecution) -> dict[str, Any]:
    """Serializable, redacted execution history entry.

    The full ``result`` payload stays in the database; the API exposes the
    verified evidence and error text the UI actually renders.
    """
    status = execution.status or ("completed" if execution.ok else "failed")
    return {
        "id": execution.id,
        "execution_id": execution.id,
        "task_id": execution.task_id,
        "conversation_id": execution.conversation_id,
        "step_index": execution.step_index,
        "tool_name": execution.tool_name,
        "arguments": execution.arguments or {},
        "status": status,
        "ok": bool(execution.ok),
        "verified": bool(execution.verified),
        "evidence": execution.evidence or "",
        "error": execution.error,
        "hint": execution.hint,
        "permission": execution.permission,
        "approval_status": execution.approval_status,
        "duration_ms": round(execution.duration_ms or 0.0, 1),
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
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
