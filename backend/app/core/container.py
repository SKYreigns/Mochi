"""Application container wiring every subsystem together."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import Settings
from app.core.events import EventBus
from app.core.logging import get_logger, setup_logging
from app.core.security import PathPolicy
from app.computer import ComputerUseEngine
from app.computer.tools import register_computer_tools
from app.database.database import Database
from app.database.models import Workflow
from app.memory.manager import MemoryManager
from app.orchestrator.agent import AgentOrchestrator
from app.permissions.engine import EmergencyState, PermissionEngine
from app.providers.manager import ProviderManager
from app.scheduler.service import SchedulerService
from app.services.settings_service import SettingsService
from app.services.task_service import TaskService
from app.services.voice import VoiceService
from app.tools.base import ToolRegistry
from app.tools.browser.manager import BrowserManager
from app.tools.browser.tools import register_browser_tools
from app.tools.desktop.manager import DesktopManager
from app.tools.desktop.tools import register_desktop_tools
from app.tools.documents.tools import register_document_tools
from app.tools.filesystem.tools import register_filesystem_tools
from app.tools.lms.service import LmsService
from app.tools.lms.tools import register_lms_tools
from app.tools.media.tools import register_media_tools
from app.tools.memory_tools import register_memory_tools
from app.tools.system.tools import register_system_tools
from app.workflows.engine import WorkflowEngine

log = get_logger("container")

BUILTIN_WORKFLOW = {
    "name": "Organize College Assignments",
    "description": "Checks the configured LMS, collects new assignment resources, summarizes them and files them by subject.",
    "steps": [
        {
            "name": "Check LMS session",
            "kind": "instruction",
            "config": {
                "instruction": "Open the configured LMS, check whether the session is authenticated, and list the available courses. If login is required, stop and ask me to sign in manually. Do not attempt to bypass MFA or CAPTCHA."
            },
        },
        {
            "name": "Find assignments",
            "kind": "instruction",
            "config": {
                "instruction": "For each configured course, open it, list assignments and read the details of assignments that are pending or due soon. Save each one in the assignment workspace with subject, title, deadline and instructions."
            },
        },
        {
            "name": "Download resources",
            "kind": "instruction",
            "config": {
                "instruction": "Download the attachments for the pending assignments into the configured LMS download folder, creating a subject subfolder. Verify each file exists and is non-empty."
            },
        },
        {
            "name": "Summarize pending work",
            "kind": "instruction",
            "config": {
                "instruction": "Create a dated PDF summary of pending assignments in the workspace folder, listing subject, title, deadline and the required deliverable for each. Verify the PDF exists afterwards."
            },
        },
        {
            "name": "Review before submission",
            "kind": "approval",
            "config": {"reason": "Review the generated assignment summary before proceeding. Nothing is submitted by this workflow."},
        },
    ],
}


class AppServices:
    def __init__(self, app_settings: Settings) -> None:
        self.app_settings = app_settings
        setup_logging(app_settings)

        self.db = Database(app_settings.database_url)
        self.events = EventBus()
        self.settings = SettingsService(app_settings, self.events)
        self.emergency = EmergencyState()
        self.permissions = PermissionEngine(self.settings, self.events, self.emergency)
        self.path_policy = PathPolicy(
            [Path(path) for path in self.settings.allowed_directories],
            workspace=app_settings.data_dir / "workspace",
        )
        self.memory = MemoryManager()
        self.provider_manager = ProviderManager(app_settings, self.settings)
        self.browser = BrowserManager(app_settings, self.events)
        self.desktop = DesktopManager(app_settings, self.events)
        self.computer = ComputerUseEngine(self)
        self.lms = LmsService(self.settings, self.browser)
        self.voice = VoiceService(app_settings, self.settings)

        self.registry = ToolRegistry()
        register_browser_tools(self.registry)
        register_desktop_tools(self.registry)
        register_computer_tools(self.registry)
        register_filesystem_tools(self.registry)
        register_document_tools(self.registry)
        register_system_tools(self.registry)
        register_lms_tools(self.registry)
        register_media_tools(self.registry)
        register_memory_tools(self.registry)

        self.orchestrator = AgentOrchestrator(self)
        self.tasks = TaskService(self)
        self.workflows = WorkflowEngine(self)
        self.scheduler = SchedulerService(self)
        self._started = False

    async def init(self) -> None:
        if self._started:
            return
        await self.db.init()
        async with self.db.session() as session:
            await self.settings.load(session)
            cancelled = await self.permissions.mark_stale_approvals(session)
            if cancelled:
                log.info("Cancelled %d stale approval request(s) from a previous session", cancelled)
            await self.tasks.recover_stale_state()
            existing = (await session.execute(select(Workflow).limit(1))).scalars().first()
            if existing is None:
                await self.workflows.create_workflow(
                    session,
                    name=BUILTIN_WORKFLOW["name"],
                    description=BUILTIN_WORKFLOW["description"],
                    steps=BUILTIN_WORKFLOW["steps"],
                )
                log.info("Seeded built-in workflow")
        # Fail-safe reconciliation after recovery: a persisted STOP survives,
        # interrupted work forces STOP, and only an explicit resume clears it.
        await self.tasks.reconcile_emergency_state(stale_approvals=bool(cancelled))
        self.refresh_security()
        await self.scheduler.start()
        self._started = True
        await self.events.publish("app.ready", {"version": "1.0.0"})

    def refresh_security(self) -> None:
        from pathlib import Path

        allowed = [Path(path) for path in self.settings.allowed_directories]
        self.path_policy.set_allowed(allowed)
        self.browser.set_blocked_sites(list(self.settings.safety.get("blocked_websites", [])))

    async def shutdown(self) -> None:
        try:
            await self.scheduler.stop()
        except Exception:
            pass
        try:
            await self.tasks.shutdown()
        except Exception:
            pass
        try:
            await self.permissions.cancel_all_waiters()
            await self.provider_manager.aclose()
            await self.browser.close()
        except Exception:
            pass
        await self.db.dispose()
        self._started = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "permission_mode": self.settings.permission_mode,
            "emergency_stopped": self.emergency.stopped,
            "emergency": self.emergency.snapshot(),
            "running_tasks": self.tasks.running_ids(),
            "browser_running": self.browser.is_running,
            "tools": len(self.registry.all()),
            "provider": self.provider_manager.status(),
            "data_dir": str(self.app_settings.data_dir),
            "voice": self.voice.status(),
        }
