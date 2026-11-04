"""Windows desktop tool definitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.tools.base import EmptyArgs, ToolContext, ToolRegistry, build_tool, ok


class FocusArgs(BaseModel):
    window_name: str = Field(description="Case-insensitive part of the window title.")


class OpenAppArgs(BaseModel):
    app_name: str = Field(description="Executable name (notepad.exe), alias (chrome) or absolute path.")


class CloseAppArgs(BaseModel):
    app_name: str = Field(description="Process name or executable, e.g. 'notepad.exe'.")


class ClickArgs(BaseModel):
    x: int = Field(ge=0, le=20000)
    y: int = Field(ge=0, le=20000)


class TypeArgs(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class KeyArgs(BaseModel):
    key: str = Field(description="Key name such as enter, tab, f5, or a sequence like '^s'.")


class HotkeyArgs(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=4, description="e.g. ['ctrl','s'] or ['alt','tab'].")


class ScrollArgs(BaseModel):
    direction: Literal["up", "down"]
    amount: int = Field(default=3, ge=1, le=50, description="Wheel steps.")


class ScreenshotArgs(BaseModel):
    monitor: int = Field(default=0, ge=0, le=8, description="0 = all monitors; 1+ = specific monitor.")


class ProcessesArgs(BaseModel):
    limit: int = Field(default=60, ge=1, le=300)


def register_desktop_tools(registry: ToolRegistry) -> None:
    async def active_window(args: BaseModel, ctx: ToolContext):
        data = ctx.services.desktop.active_window()
        return ok(data, evidence=f"Active window: {data.get('title')!r} ({data.get('process')})")

    async def list_windows(args: BaseModel, ctx: ToolContext):
        data = ctx.services.desktop.list_windows()
        return ok({"windows": data, "count": len(data)}, evidence=f"{len(data)} visible windows")

    async def open_application(args: OpenAppArgs, ctx: ToolContext):
        data = ctx.services.desktop.open_application(args.app_name)
        await ctx.emit("desktop.app_launched", data)
        return ok(data, evidence=f"Launched {data.get('launched')}")

    async def close_application(args: CloseAppArgs, ctx: ToolContext):
        data = ctx.services.desktop.close_application(args.app_name)
        return ok(data, evidence=f"Terminated {len(data['terminated'])} process(es); still running: {data['still_running']}")

    async def focus_window(args: FocusArgs, ctx: ToolContext):
        data = ctx.services.desktop.focus_window(args.window_name)
        if not data.get("focused"):
            return ok(data, evidence=f"Active window is {data['active'].get('title')!r}", verified=False)
        return ok(data, evidence=f"Focused {data['active'].get('title')!r}")

    async def screenshot(args: ScreenshotArgs, ctx: ToolContext):
        data = ctx.services.desktop.screenshot(args.monitor)
        await ctx.emit("desktop.screenshot", data)
        return ok(
            data,
            evidence=f"Desktop screenshot saved ({data['size']} bytes)",
            artifacts=[{"type": "screenshot", "path": data["path"], "url": data["url"]}],
        )

    async def click(args: ClickArgs, ctx: ToolContext):
        data = ctx.services.desktop.click(args.x, args.y)
        return ok(data, evidence=f"Clicked ({args.x},{args.y}) on {data.get('active_after')!r}")

    async def double_click(args: ClickArgs, ctx: ToolContext):
        data = ctx.services.desktop.click(args.x, args.y, double=True)
        return ok(data, evidence=f"Double-clicked ({args.x},{args.y}) on {data.get('active_after')!r}")

    async def type_text(args: TypeArgs, ctx: ToolContext):
        data = ctx.services.desktop.type_text(args.text)
        return ok(data, evidence=f"Typed {data['characters']} characters into {data.get('active')!r}")

    async def press_key(args: KeyArgs, ctx: ToolContext):
        data = ctx.services.desktop.press_key(args.key)
        return ok(data, evidence=f"Pressed {args.key} in {data.get('active')!r}")

    async def hotkey(args: HotkeyArgs, ctx: ToolContext):
        data = ctx.services.desktop.hotkey(args.keys)
        return ok(data, evidence=f"Sent hotkey {'+'.join(data['keys'])} in {data.get('active')!r}")

    async def scroll(args: ScrollArgs, ctx: ToolContext):
        data = ctx.services.desktop.scroll(args.direction, args.amount)
        return ok(data, evidence=f"Scrolled {args.direction} {args.amount} steps")

    async def get_screen_text(args: ScreenshotArgs, ctx: ToolContext):
        data = ctx.services.desktop.get_screen_text(args.monitor)
        return ok(
            {"text": data["text"], "chars": data["chars"], "screenshot": data["screenshot"]},
            evidence=f"OCR extracted {data['chars']} characters",
        )

    async def get_running_processes(args: ProcessesArgs, ctx: ToolContext):
        data = ctx.services.desktop.get_running_processes(args.limit)
        return ok({"processes": data, "count": len(data)}, evidence=f"{len(data)} processes listed by memory use")

    registry.register(build_tool(
        name="desktop.get_active_window", description="Return the currently focused window (title, process, bounds).",
        input_model=EmptyArgs, handler=active_window, permission=ToolPermission.READ, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.list_windows", description="List all visible top-level windows with titles and process ids.",
        input_model=EmptyArgs, handler=list_windows, permission=ToolPermission.READ, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.open_application", description="Launch an application by executable name, alias or full path.",
        input_model=OpenAppArgs, handler=open_application, permission=ToolPermission.WRITE, timeout=30, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.close_application", description="Terminate an application's processes. Requires explicit approval.",
        input_model=CloseAppArgs, handler=close_application, permission=ToolPermission.DESTRUCTIVE, timeout=30,
        requires_approval=True, approval_reason="Closing an application can discard unsaved work.", tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.focus_window", description="Bring a window whose title contains the given text to the foreground.",
        input_model=FocusArgs, handler=focus_window, permission=ToolPermission.WRITE, timeout=20, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.take_screenshot", description="Capture the desktop (all monitors or one) and return a preview URL.",
        input_model=ScreenshotArgs, handler=screenshot, permission=ToolPermission.READ, timeout=30, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.click", description="Left-click at absolute screen coordinates.",
        input_model=ClickArgs, handler=click, permission=ToolPermission.WRITE, timeout=20, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.double_click", description="Double-click at absolute screen coordinates.",
        input_model=ClickArgs, handler=double_click, permission=ToolPermission.WRITE, timeout=20, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.type_text", description="Type text into the focused window.",
        input_model=TypeArgs, handler=type_text, permission=ToolPermission.WRITE, timeout=30, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.press_key", description="Press a single key (enter, tab, esc, f5, ...) in the focused window.",
        input_model=KeyArgs, handler=press_key, permission=ToolPermission.WRITE, timeout=20, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.hotkey", description="Send a key combination such as ctrl+s or alt+tab.",
        input_model=HotkeyArgs, handler=hotkey, permission=ToolPermission.WRITE, timeout=20, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.scroll", description="Scroll the window under the cursor up or down.",
        input_model=ScrollArgs, handler=scroll, permission=ToolPermission.WRITE, timeout=20, tags=("desktop",),
    ))
    registry.register(build_tool(
        name="desktop.get_screen_text", description="OCR the screen (requires Tesseract) and return the text.",
        input_model=ScreenshotArgs, handler=get_screen_text, permission=ToolPermission.READ, timeout=90, tags=("desktop", "ocr"),
    ))
    registry.register(build_tool(
        name="desktop.get_running_processes", description="List running processes sorted by memory usage.",
        input_model=ProcessesArgs, handler=get_running_processes, permission=ToolPermission.READ, timeout=30, tags=("desktop",),
    ))
