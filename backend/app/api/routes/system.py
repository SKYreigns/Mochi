"""System metrics, tool catalog and the Live Computer state endpoint."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.deps import run_direct_tool, services_from

router = APIRouter(tags=["system"])


@router.get("/api/system/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    import psutil

    memory = psutil.virtual_memory()
    volumes = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            continue
        volumes.append(
            {
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "percent": usage.percent,
                "free_gb": round(usage.free / 1e9, 1),
                "total_gb": round(usage.total / 1e9, 1),
            }
        )
    battery = psutil.sensors_battery()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_cores": psutil.cpu_count(logical=True),
        "ram_percent": memory.percent,
        "ram_used_gb": round(memory.used / 1e9, 1),
        "ram_total_gb": round(memory.total / 1e9, 1),
        "volumes": volumes,
        "battery": (
            {"percent": battery.percent, "plugged_in": battery.power_plugged} if battery else None
        ),
        "uptime_seconds": round(time.time() - psutil.boot_time()),
    }


@router.get("/api/system/os")
async def os_info(request: Request) -> dict[str, Any]:
    from app.tools.system.tools import _installed_apps  # noqa: F401  (reuse for consistency)

    services = services_from(request)
    result = await run_direct_tool(services, "system.get_os_information", {})
    return {"data": result.data}


@router.get("/api/system/processes")
async def processes(request: Request, limit: int = 60) -> dict[str, Any]:
    services = services_from(request)
    return {"processes": services.desktop.get_running_processes(limit)}


@router.get("/api/system/applications")
async def applications(request: Request) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "system.get_installed_applications", {})
    return {"data": result.data}


@router.get("/api/tools")
async def tool_catalog(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return {"tools": services.registry.catalog(), "count": len(services.registry.all())}


@router.get("/api/system/windows")
async def desktop_windows(request: Request) -> dict[str, Any]:
    """Read-only window telemetry for the Computer screen."""
    services = services_from(request)
    active_result = await run_direct_tool(services, "desktop.get_active_window", {})
    listing_result = await run_direct_tool(services, "desktop.list_windows", {})
    data = listing_result.data if isinstance(listing_result.data, dict) else {}
    return {
        "active": active_result.data,
        "windows": data.get("windows", []),
        "count": data.get("count", 0),
    }


@router.get("/api/live/state")
async def live_state(request: Request) -> dict[str, Any]:
    services = services_from(request)
    browser = await services.browser.state() if services.browser.is_running else {"running": False, "tabs": [], "active": None}
    tasks = await services.tasks.recent(limit=10)
    active = next((task for task in tasks if task.status in ("queued", "planning", "running", "waiting_for_approval")), None)
    screenshots = sorted(
        services.app_settings.screenshots_dir.glob("*.png"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    latest = screenshots[0].name if screenshots else None
    return {
        "browser": browser,
        "active_task": (
            {
                "id": active.id,
                "instruction": active.instruction,
                "status": active.status,
                "current_step": active.current_step,
                "plan": active.plan,
            }
            if active
            else None
        ),
        "latest_screenshot": f"/api/live/screenshots/{latest}" if latest else None,
        "permission_mode": services.settings.permission_mode,
        "emergency_stopped": services.emergency.stopped,
        "running_tasks": services.tasks.running_ids(),
        "events": services.events.history(40),
        "voice": services.voice.status(),
    }


@router.post("/api/live/screenshot")
async def capture_desktop(request: Request) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "desktop.take_screenshot", {"monitor": 0})
    return {"data": result.data, "evidence": result.evidence}


@router.get("/api/live/screenshots/{filename}")
async def get_screenshot(filename: str, request: Request) -> FileResponse:
    services = services_from(request)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = Path(services.app_settings.screenshots_dir) / filename
    if not target.exists() or not target.is_file() or target.suffix.lower() != ".png":
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(target), media_type="image/png", filename=filename)
