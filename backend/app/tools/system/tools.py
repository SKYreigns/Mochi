"""System information tools and policy-checked PowerShell execution."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import time
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.core.errors import ToolExecutionError
from app.core.security import validate_powershell
from app.tools.base import EmptyArgs, ToolContext, ToolRegistry, build_tool, fail, ok

BLOCKED_PATTERNS = re.compile(
    r"(?i)(remove-item|format-volume|clear-disk|stop-computer|restart-computer|"
    r"shutdown|bcdedit|diskpart|takeown|icacls|cipher\s+/w|reg\s+(add|delete)|"
    r"schtasks\s+/create|new-localuser|set-executionpolicy|invoke-expression|\biex\b)"
)


class CommandArgs(BaseModel):
    command: str = Field(description="PowerShell command. Read-only cmdlets only.")
    timeout: int = Field(default=30, ge=1, le=120)


class AdminCommandArgs(BaseModel):
    command: str = Field(description="PowerShell command requiring explicit approval.")
    timeout: int = Field(default=60, ge=1, le=300)


def register_system_tools(registry: ToolRegistry) -> None:
    async def cpu(args: BaseModel, ctx: ToolContext):
        import psutil

        percent = psutil.cpu_percent(interval=0.4)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        frequency = psutil.cpu_freq()
        return ok(
            {
                "percent": percent,
                "per_core": per_core,
                "cores_logical": psutil.cpu_count(logical=True),
                "cores_physical": psutil.cpu_count(logical=False),
                "frequency_mhz": round(frequency.current, 1) if frequency else None,
            },
            evidence=f"CPU {percent:.1f}% across {psutil.cpu_count(logical=True)} logical cores",
        )

    async def ram(args: BaseModel, ctx: ToolContext):
        import psutil

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return ok(
            {
                "total_gb": round(memory.total / 1e9, 2),
                "available_gb": round(memory.available / 1e9, 2),
                "used_gb": round(memory.used / 1e9, 2),
                "percent": memory.percent,
                "swap_percent": swap.percent,
            },
            evidence=f"RAM {memory.percent:.1f}% used ({memory.available / 1e9:.1f} GB available)",
        )

    async def disk(args: BaseModel, ctx: ToolContext):
        import psutil

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
                    "filesystem": partition.fstype,
                    "total_gb": round(usage.total / 1e9, 2),
                    "used_gb": round(usage.used / 1e9, 2),
                    "free_gb": round(usage.free / 1e9, 2),
                    "percent": usage.percent,
                }
            )
        return ok({"volumes": volumes}, evidence=f"{len(volumes)} volume(s) inspected")

    async def battery(args: BaseModel, ctx: ToolContext):
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            return fail("No battery was detected on this machine.", hint="This appears to be a desktop system.")
        return ok(
            {
                "percent": battery.percent,
                "plugged_in": battery.power_plugged,
                "seconds_left": battery.secsleft if battery.secsleft >= 0 else None,
            },
            evidence=f"Battery {battery.percent}% {'plugged in' if battery.power_plugged else 'on battery'}",
        )

    async def processes(args: BaseModel, ctx: ToolContext):
        data = ctx.services.desktop.get_running_processes(80)
        return ok({"processes": data, "count": len(data)}, evidence=f"{len(data)} processes")

    async def installed_applications(args: BaseModel, ctx: ToolContext):
        apps = _installed_apps()
        return ok({"applications": apps, "count": len(apps)}, evidence=f"{len(apps)} installed applications found")

    async def os_info(args: BaseModel, ctx: ToolContext):
        import psutil

        info = {
            "os": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
            "hostname": platform.node(),
            "user": os.environ.get("USERNAME", ""),
            "python": sys.version.split()[0],
            "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(psutil.boot_time())),
            "timezone": time.tzname,
        }
        return ok(info, evidence=f"{info['os']} {info['release']} on {info['hostname']}")

    async def readonly_powershell(args: CommandArgs, ctx: ToolContext):
        policy = validate_powershell(args.command)
        if policy.blocked:
            return fail(f"Command blocked: {policy.reason}")
        if not policy.readonly:
            return fail(
                f"Refused: {policy.reason}.",
                hint="Use system.run_powershell (requires approval) for non-read-only commands.",
            )
        output = await _run_ps(args.command, args.timeout)
        return ok(output, evidence=f"PowerShell read-only command executed ({len(output['stdout'])} chars of output)")

    async def admin_powershell(args: AdminCommandArgs, ctx: ToolContext):
        if BLOCKED_PATTERNS.search(args.command):
            return fail("Command contains a permanently blocked, destructive or elevation pattern.")
        result = await _run_ps(args.command, args.timeout)
        return ok(result, evidence=f"Approved PowerShell command executed (exit {result['exit_code']})")

    registry.register(build_tool(
        name="system.get_cpu_usage", description="Current CPU utilization and core count.",
        input_model=EmptyArgs, handler=cpu, permission=ToolPermission.READ, tags=("system",),
    ))
    registry.register(build_tool(
        name="system.get_ram_usage", description="Physical memory and swap utilization.",
        input_model=EmptyArgs, handler=ram, permission=ToolPermission.READ, tags=("system",),
    ))
    registry.register(build_tool(
        name="system.get_disk_usage", description="Disk volumes with capacity and free space.",
        input_model=EmptyArgs, handler=disk, permission=ToolPermission.READ, tags=("system",),
    ))
    registry.register(build_tool(
        name="system.get_battery_status", description="Battery percentage and charging state.",
        input_model=EmptyArgs, handler=battery, permission=ToolPermission.READ, tags=("system",),
    ))
    registry.register(build_tool(
        name="system.get_running_processes", description="Running processes with memory usage.",
        input_model=EmptyArgs, handler=processes, permission=ToolPermission.READ, tags=("system",),
    ))
    registry.register(build_tool(
        name="system.get_installed_applications", description="List installed applications from the Windows registry.",
        input_model=EmptyArgs, handler=installed_applications, permission=ToolPermission.READ, timeout=45, tags=("system",),
    ))
    registry.register(build_tool(
        name="system.get_os_information", description="Operating system, host, boot time and Python information.",
        input_model=EmptyArgs, handler=os_info, permission=ToolPermission.READ, tags=("system",),
    ))
    registry.register(build_tool(
        name="system.run_readonly_powershell",
        description="Run a read-only, allowlisted PowerShell command (Get-*/Test-Path/Select-Object pipelines only).",
        input_model=CommandArgs, handler=readonly_powershell, permission=ToolPermission.READ, timeout=130, tags=("system", "powershell"),
    ))
    registry.register(build_tool(
        name="system.run_powershell",
        description="Run an approved PowerShell command. Always requires explicit user approval; dangerous patterns are blocked.",
        input_model=AdminCommandArgs, handler=admin_powershell, permission=ToolPermission.ADMIN, timeout=310,
        requires_approval=True, approval_reason="Arbitrary PowerShell execution requires explicit approval.",
        tags=("system", "powershell"),
    ))


def _installed_apps() -> list[dict[str, Any]]:
    import winreg

    apps: dict[str, dict[str, Any]] = {}
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, name) as subkey:
                            display = _reg_value(subkey, "DisplayName")
                            if not display:
                                continue
                            apps[display] = {
                                "name": display,
                                "version": _reg_value(subkey, "DisplayVersion") or "",
                                "publisher": _reg_value(subkey, "Publisher") or "",
                                "install_location": _reg_value(subkey, "InstallLocation") or "",
                            }
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(apps.values(), key=lambda item: item["name"].lower())


def _reg_value(key: Any, name: str) -> Any:
    import winreg

    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


async def _run_ps(command: str, timeout: int) -> dict[str, Any]:
    import asyncio

    creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    process = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creation_flags,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        raise ToolExecutionError(f"PowerShell command timed out after {timeout}s.")
    return {
        "exit_code": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace")[:20000],
        "stderr": stderr.decode("utf-8", errors="replace")[:5000],
    }
