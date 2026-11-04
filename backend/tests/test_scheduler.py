"""Scheduler: validation, next-run math, execution, dedupe, persistence, shutdown."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.container import AppServices
from app.database.models import ScheduledJob, Task
from app.scheduler.service import compute_next_run, normalize_utc
from tests.helpers import wait_until


def _job(**kwargs) -> ScheduledJob:
    defaults = {"id": "job_test", "name": "Test job", "instruction": "Say hello", "enabled": True}
    return ScheduledJob(**{**defaults, **kwargs})


async def _add_job(services, **kwargs) -> ScheduledJob:
    async with services.db.session() as session:
        job = ScheduledJob(**kwargs)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _get_job(services, job_id: str) -> ScheduledJob | None:
    async with services.db.session() as session:
        return await session.get(ScheduledJob, job_id)


def test_validate_job_accepts_supported_schedules():
    from app.scheduler.service import SchedulerService

    validator = SchedulerService.__new__(SchedulerService)
    assert validator.validate_job({"schedule_type": "once"})["schedule_type"] == "once"
    assert validator.validate_job({"schedule_type": "interval", "interval_minutes": 5})["interval_minutes"] == 5
    assert validator.validate_job({"schedule_type": "daily", "time_of_day": "23:59"})["time_of_day"] == "23:59"


@pytest.mark.parametrize(
    "payload",
    [
        {"schedule_type": "hourly"},
        {"schedule_type": "interval"},
        {"schedule_type": "daily"},
        {"schedule_type": "daily", "time_of_day": "25:00"},
        {"schedule_type": "daily", "time_of_day": "not-a-time"},
    ],
)
def test_validate_job_rejects_invalid_schedules(payload):
    from app.scheduler.service import SchedulerService

    validator = SchedulerService.__new__(SchedulerService)
    with pytest.raises(ValueError):
        validator.validate_job(payload)


def test_compute_next_run_once():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    future = _job(schedule_type="once", run_at=now + timedelta(hours=1))
    assert compute_next_run(future, now) == now + timedelta(hours=1)
    past = _job(schedule_type="once", run_at=now - timedelta(hours=1))
    assert compute_next_run(past, now) is None
    assert compute_next_run(_job(schedule_type="once", run_at=None), now) is None


def test_compute_next_run_interval_advances_past_now():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    job = _job(
        schedule_type="interval",
        interval_minutes=15,
        next_run_at=now - timedelta(minutes=40),
    )
    next_run = compute_next_run(job, now)
    assert next_run is not None and next_run > now
    assert (next_run - now) <= timedelta(minutes=15)


def test_compute_next_run_daily_uses_local_time():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    job = _job(schedule_type="daily", time_of_day="23:30")
    next_run = compute_next_run(job, now)
    assert next_run is not None and next_run > now
    local = next_run.astimezone()
    assert (local.hour, local.minute) == (23, 30)
    assert normalize_utc(next_run) == next_run


async def test_tick_runs_due_interval_job_and_advances(services):
    now = datetime.now(timezone.utc)
    job = await _add_job(
        services,
        name="Interval job",
        instruction="Say hello",
        schedule_type="interval",
        interval_minutes=5,
        next_run_at=now - timedelta(minutes=1),
        enabled=True,
    )

    started = await services.scheduler.tick(now)
    assert job.id in started

    await wait_until(lambda: _job_has_run(services, job.id), timeout=10)
    stored = await _get_job(services, job.id)
    assert stored is not None
    assert stored.enabled is True
    assert stored.last_run_at is not None
    assert stored.last_task_id is not None
    assert stored.next_run_at is not None and normalize_utc(stored.next_run_at) > now

    async with services.db.session() as session:
        task = await session.get(Task, stored.last_task_id)
    assert task is not None and task.trigger == "schedule"


async def test_tick_disables_once_job(services):
    now = datetime.now(timezone.utc)
    job = await _add_job(
        services,
        name="Once job",
        instruction="One-shot",
        schedule_type="once",
        run_at=now - timedelta(seconds=5),
        next_run_at=now - timedelta(seconds=5),
        enabled=True,
    )

    started = await services.scheduler.tick(now)
    assert job.id in started

    stored = await _get_job(services, job.id)
    assert stored is not None
    assert stored.enabled is False
    assert stored.next_run_at is None
    await wait_until(lambda: _job_has_run(services, job.id), timeout=10)


async def test_tick_skips_jobs_already_running(services):
    now = datetime.now(timezone.utc)
    job = await _add_job(
        services,
        name="Busy job",
        instruction="Do something",
        schedule_type="interval",
        interval_minutes=5,
        next_run_at=now - timedelta(minutes=1),
        enabled=True,
    )
    services.scheduler._running_jobs.add(job.id)
    try:
        started = await services.scheduler.tick(now)
        assert job.id not in started
        stored = await _get_job(services, job.id)
        assert stored is not None
        assert normalize_utc(stored.next_run_at) == normalize_utc(job.next_run_at)
    finally:
        services.scheduler._running_jobs.discard(job.id)


async def test_tick_ignores_disabled_and_future_jobs(services):
    now = datetime.now(timezone.utc)
    disabled = await _add_job(
        services,
        name="Disabled",
        instruction="x",
        schedule_type="interval",
        interval_minutes=5,
        next_run_at=now - timedelta(minutes=1),
        enabled=False,
    )
    future = await _add_job(
        services,
        name="Future",
        instruction="x",
        schedule_type="once",
        run_at=now + timedelta(hours=1),
        next_run_at=now + timedelta(hours=1),
        enabled=True,
    )
    started = await services.scheduler.tick(now)
    assert disabled.id not in started
    assert future.id not in started


async def test_jobs_persist_across_restart(tmp_path, sandbox):
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "sched-data",
        log_dir=tmp_path / "sched-logs",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'sched.db').as_posix()}",
        allowed_directories=[sandbox],
        llm_provider="mock",
        llm_model="mock",
        voice_enabled=False,
    )
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    first = AppServices(settings)
    await first.init()
    try:
        async with first.db.session() as session:
            job = ScheduledJob(
                name="Persisted job",
                instruction="Survive restart",
                schedule_type="once",
                run_at=future,
                next_run_at=future,
                enabled=True,
            )
            session.add(job)
            await session.commit()
            job_id = job.id
    finally:
        await first.shutdown()

    second = AppServices(settings)
    await second.init()
    try:
        async with second.db.session() as session:
            restored = await session.get(ScheduledJob, job_id)
            assert restored is not None
            assert restored.name == "Persisted job"
            assert restored.enabled is True
            assert normalize_utc(restored.next_run_at) == normalize_utc(future)
            rows = (await session.execute(select(ScheduledJob))).scalars().all()
            assert len(rows) == 1
    finally:
        await second.shutdown()


async def test_scheduler_start_stop_is_graceful(services):
    scheduler = services.scheduler
    await scheduler.stop()
    assert scheduler._loop_task is None

    await scheduler.start()
    assert scheduler._loop_task is not None
    await asyncio.sleep(0.05)
    assert not scheduler._loop_task.done()

    await scheduler.stop()
    assert scheduler._loop_task is None
    # Restart after shutdown must work (used on app reload).
    await scheduler.start()
    assert scheduler._loop_task is not None
    await scheduler.stop()


async def _job_has_run(services, job_id: str) -> bool:
    stored = await _get_job(services, job_id)
    return bool(stored and stored.last_task_id)
