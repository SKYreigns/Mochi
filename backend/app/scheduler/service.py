"""Durable in-process scheduler for recurring JARVIS tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.events import EventBus
from app.core.logging import audit, get_logger
from app.database.database import Database
from app.database.models import ScheduledJob

log = get_logger("scheduler")


def normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_next_run(job: ScheduledJob, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    if job.schedule_type == "once":
        run_at = normalize_utc(job.run_at)
        return run_at if run_at and run_at > now else None
    if job.schedule_type == "interval":
        minutes = max(1, int(job.interval_minutes or 60))
        anchor = normalize_utc(job.next_run_at) or now
        candidate = anchor + timedelta(minutes=minutes)
        while candidate <= now:
            candidate += timedelta(minutes=minutes)
        return candidate
    if job.schedule_type == "daily":
        time_of_day = job.time_of_day or "09:00"
        try:
            hour, minute = (int(part) for part in time_of_day.split(":", 1))
        except ValueError:
            hour, minute = 9, 0
        local_now = now.astimezone()
        candidate_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate_local <= local_now:
            candidate_local += timedelta(days=1)
        return candidate_local.astimezone(timezone.utc)
    return None


class SchedulerService:
    def __init__(self, services: Any) -> None:
        self.services = services
        self._loop_task: asyncio.Task[None] | None = None
        self._running_jobs: set[str] = set()
        self._stop = asyncio.Event()

    @property
    def db(self) -> Database:
        return self.services.db

    @property
    def events(self) -> EventBus:
        return self.services.events

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._stop.clear()
        self._loop_task = asyncio.create_task(self._loop(), name="jarvis-scheduler")
        log.info("Scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - defensive
                log.exception("Scheduler loop ended with an error")
            self._loop_task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=20)
            except asyncio.TimeoutError:
                continue

    async def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        started: list[str] = []
        async with self.db.session() as session:
            jobs = (
                await session.execute(
                    select(ScheduledJob).where(
                        ScheduledJob.enabled.is_(True),
                        ScheduledJob.next_run_at.is_not(None),
                        ScheduledJob.next_run_at <= now,
                    )
                )
            ).scalars().all()
            for job in jobs:
                if job.id in self._running_jobs:
                    continue
                next_run = compute_next_run(job, now)
                if job.schedule_type == "once":
                    job.enabled = False
                    job.next_run_at = None
                else:
                    job.next_run_at = next_run
                await session.commit()
                self._running_jobs.add(job.id)
                started.append(job.id)
                asyncio.create_task(self._run_job(job.id), name=f"jarvis-job-{job.id}")
        return started

    async def _run_job(self, job_id: str) -> None:
        try:
            async with self.db.session() as session:
                job = await session.get(ScheduledJob, job_id)
                if job is None:
                    return
                workflow_id = job.workflow_id
                instruction = job.instruction
                name = job.name
            await self.events.publish("schedule.triggered", {"job_id": job_id, "name": name})
            task_id: str | None = None
            if workflow_id:
                result = await self.services.workflows.run(workflow_id, trigger="schedule")
                task_id = result.get("task_id")
            else:
                task = await self.services.tasks.start(
                    instruction=instruction,
                    trigger="schedule",
                    title=f"Schedule: {name}",
                )
                task_id = task.id
            async with self.db.session() as session:
                job = await session.get(ScheduledJob, job_id)
                if job is not None:
                    job.last_run_at = datetime.now(timezone.utc)
                    job.last_task_id = task_id
                    await session.commit()
            await self.events.publish(
                "schedule.completed", {"job_id": job_id, "name": name, "task_id": task_id}
            )
            audit("schedule.executed", job_id=job_id, task_id=task_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Scheduled job %s failed", job_id)
            await self.events.publish("schedule.failed", {"job_id": job_id, "error": str(exc)})
        finally:
            self._running_jobs.discard(job_id)

    def validate_job(self, data: dict[str, Any]) -> dict[str, Any]:
        schedule_type = str(data.get("schedule_type") or "once")
        if schedule_type not in ("once", "interval", "daily"):
            raise ValueError("schedule_type must be once, interval or daily")
        if schedule_type == "interval" and not data.get("interval_minutes"):
            raise ValueError("interval_minutes is required for interval schedules")
        if schedule_type == "daily" and not data.get("time_of_day"):
            raise ValueError("time_of_day (HH:MM) is required for daily schedules")
        if schedule_type == "daily":
            try:
                hour, minute = (int(part) for part in str(data["time_of_day"]).split(":", 1))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
            except ValueError as exc:
                raise ValueError("time_of_day must be HH:MM (24-hour)") from exc
        return data
