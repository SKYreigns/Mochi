"""Workflow CRUD/run endpoints and scheduling endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import services_from
from app.database.models import ScheduledJob, WorkflowRun
from app.scheduler.service import compute_next_run, normalize_utc

workflows_router = APIRouter(tags=["workflows"])
schedules_router = APIRouter(tags=["schedules"])


class WorkflowStepBody(BaseModel):
    name: str
    kind: str = "instruction"
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    steps: list[WorkflowStepBody] = Field(default_factory=list)
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    steps: list[WorkflowStepBody] | None = None


@workflows_router.get("/api/workflows")
async def list_workflows(request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        return {"workflows": await services.workflows.list_workflows(session)}


@workflows_router.post("/api/workflows")
async def create_workflow(body: WorkflowCreate, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        workflow = await services.workflows.create_workflow(
            session,
            name=body.name,
            description=body.description,
            steps=[step.model_dump() for step in body.steps],
            enabled=body.enabled,
        )
        return {"id": workflow.id, "name": workflow.name}


@workflows_router.put("/api/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, body: WorkflowUpdate, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        workflow = await services.workflows.update_workflow(
            session,
            workflow_id,
            name=body.name,
            description=body.description,
            enabled=body.enabled,
            steps=[step.model_dump() for step in body.steps] if body.steps is not None else None,
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {"id": workflow.id, "enabled": workflow.enabled}


@workflows_router.delete("/api/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        deleted = await services.workflows.delete_workflow(session, workflow_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {"deleted": workflow_id}


@workflows_router.post("/api/workflows/{workflow_id}/run")
async def run_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        from app.database.models import Workflow

        workflow = await session.get(Workflow, workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if not workflow.enabled:
            raise HTTPException(status_code=409, detail="Workflow is disabled")
    asyncio.create_task(services.workflows.run(workflow_id, trigger="manual"), name=f"workflow-{workflow_id}")
    return {"started": True, "workflow_id": workflow_id}


@workflows_router.get("/api/workflows/{workflow_id}/runs")
async def workflow_runs(workflow_id: str, request: Request, limit: int = 20) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        rows = (
            await session.execute(
                select(WorkflowRun)
                .where(WorkflowRun.workflow_id == workflow_id)
                .order_by(WorkflowRun.started_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return {
            "runs": [
                {
                    "id": run.id,
                    "status": run.status,
                    "results": run.results,
                    "error": run.error,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                }
                for run in rows
            ]
        }


# --- schedules -------------------------------------------------------------
class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instruction: str = ""
    workflow_id: str | None = None
    schedule_type: str = "once"
    run_at: str | None = None
    interval_minutes: int | None = None
    time_of_day: str | None = None
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    instruction: str | None = None
    workflow_id: str | None = None
    schedule_type: str | None = None
    run_at: str | None = None
    interval_minutes: int | None = None
    time_of_day: str | None = None
    enabled: bool | None = None


def _parse_run_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="run_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@workflows_router.get("/api/schedules")
async def list_schedules(request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        jobs = (
            await session.execute(select(ScheduledJob).order_by(ScheduledJob.created_at.desc()))
        ).scalars().all()
        return {
            "schedules": [
                {
                    "id": job.id,
                    "name": job.name,
                    "instruction": job.instruction,
                    "workflow_id": job.workflow_id,
                    "schedule_type": job.schedule_type,
                    "run_at": job.run_at.isoformat() if job.run_at else None,
                    "interval_minutes": job.interval_minutes,
                    "time_of_day": job.time_of_day,
                    "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
                    "enabled": job.enabled,
                    "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
                    "last_task_id": job.last_task_id,
                }
                for job in jobs
            ]
        }


@workflows_router.post("/api/schedules")
async def create_schedule(body: ScheduleCreate, request: Request) -> dict[str, Any]:
    services = services_from(request)
    data = body.model_dump()
    try:
        services.scheduler.validate_job(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = ScheduledJob(
        name=body.name,
        instruction=body.instruction,
        workflow_id=body.workflow_id,
        schedule_type=body.schedule_type,
        run_at=_parse_run_at(body.run_at),
        interval_minutes=body.interval_minutes,
        time_of_day=body.time_of_day,
        enabled=body.enabled,
    )
    job.next_run_at = compute_next_run(job)
    if body.schedule_type == "once" and job.next_run_at is None:
        raise HTTPException(status_code=422, detail="run_at must be in the future for a one-time schedule")
    if body.schedule_type == "interval" and job.next_run_at is None:
        job.next_run_at = datetime.now(timezone.utc) + timedelta(
            minutes=body.interval_minutes or 60
        )
    async with services.db.session() as session:
        session.add(job)
        await session.commit()
        return {"id": job.id, "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None}


@workflows_router.patch("/api/schedules/{job_id}")
async def update_schedule(job_id: str, body: ScheduleUpdate, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        job = await session.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        for field, value in body.model_dump(exclude_unset=True).items():
            if field == "run_at":
                job.run_at = _parse_run_at(value)
            else:
                setattr(job, field, value)
        job.next_run_at = compute_next_run(job)
        await session.commit()
        return {"id": job.id, "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None}


@workflows_router.delete("/api/schedules/{job_id}")
async def delete_schedule(job_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        job = await session.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        await session.delete(job)
        await session.commit()
        return {"deleted": job_id}


@workflows_router.post("/api/schedules/{job_id}/run")
async def run_schedule_now(job_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        job = await session.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        workflow_id = job.workflow_id
        instruction = job.instruction
        name = job.name
    if workflow_id:
        asyncio.create_task(services.workflows.run(workflow_id, trigger="schedule"))
        return {"started": True, "workflow_id": workflow_id}
    task = await services.tasks.start(instruction=instruction, trigger="schedule", title=f"Schedule: {name}")
    async with services.db.session() as session:
        job = await session.get(ScheduledJob, job_id)
        if job:
            job.last_run_at = datetime.now(timezone.utc)
            job.last_task_id = task.id
            await session.commit()
    return {"started": True, "task_id": task.id}
