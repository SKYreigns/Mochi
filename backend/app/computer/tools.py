"""Computer-use tool definitions exposed to the agent.

All input tools are WRITE actions and therefore pass through the existing
permission engine. Consequential clicks have their own approval-gated tool so a
send/submit/purchase/delete/install action can never happen by accident.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.tools.base import EmptyArgs, ToolContext, ToolRegistry, build_tool, fail, ok


class ObserveArgs(BaseModel):
    include_elements: bool = Field(default=True, description="Include UI Automation elements for the active window.")
    include_ocr: bool = Field(default=False, description="Run OCR when Tesseract is installed.")
    include_screenshot: bool = Field(default=True, description="Capture a screenshot as part of the observation.")


class ScreenshotArgs(BaseModel):
    monitor: int = Field(default=0, ge=0, le=8, description="0 = all monitors; 1+ = a specific monitor.")


class OpenAppArgs(BaseModel):
    app_name: str = Field(min_length=1, max_length=260, description="Executable name, alias or absolute path.")
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)


class FocusArgs(BaseModel):
    window_name: str = Field(min_length=1, max_length=200, description="Case-insensitive part of the window title.")


class ClickArgs(BaseModel):
    x: int = Field(ge=0, le=20000)
    y: int = Field(ge=0, le=20000)
    button: Literal["left", "right", "middle"] = "left"
    clicks: int = Field(default=1, ge=1, le=3)


class ClickElementArgs(BaseModel):
    label: str = Field(min_length=1, max_length=300, description="Accessible label of an element returned by computer.observe.")
    window_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=80, description="Optional control role filter.")
    button: Literal["left", "right", "middle"] = "left"
    clicks: int = Field(default=1, ge=1, le=3)


class ConsequentialClickArgs(ClickElementArgs):
    reason: str = Field(min_length=1, max_length=300, description="Why this consequential action is required.")


class MoveArgs(BaseModel):
    x: int = Field(ge=0, le=20000)
    y: int = Field(ge=0, le=20000)


class ScrollArgs(BaseModel):
    direction: Literal["up", "down"]
    amount: int = Field(default=3, ge=1, le=50)
    x: int | None = Field(default=None, ge=0, le=20000)
    y: int | None = Field(default=None, ge=0, le=20000)


class DragArgs(BaseModel):
    from_x: int = Field(ge=0, le=20000)
    from_y: int = Field(ge=0, le=20000)
    to_x: int = Field(ge=0, le=20000)
    to_y: int = Field(ge=0, le=20000)
    button: Literal["left", "right"] = "left"


class TypeArgs(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    window_name: str | None = Field(default=None, max_length=200)
    verify: bool = Field(default=True, description="Re-observe the window and check that the text is visible.")


class KeyArgs(BaseModel):
    key: str = Field(min_length=1, max_length=60, description="Key name such as enter, tab, f5.")


class HotkeyArgs(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=4)


class PasteArgs(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    window_name: str | None = Field(default=None, max_length=200)
    verify: bool = Field(default=True)


class CopyArgs(BaseModel):
    expected: str | None = Field(
        default=None,
        max_length=20000,
        description="Optional expected clipboard text. When provided, the clipboard is verified.",
    )


class VerifyTextArgs(BaseModel):
    expected: str = Field(min_length=1, max_length=10000)
    window_name: str | None = Field(default=None, max_length=200)


def _ids(ctx: ToolContext) -> tuple[str | None, str | None]:
    return ctx.task_id, ctx.conversation_id


def register_computer_tools(registry: ToolRegistry) -> None:
    async def observe(args: ObserveArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.observe(
            include_elements=args.include_elements,
            include_ocr=args.include_ocr,
            include_screenshot=args.include_screenshot,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        artifacts = []
        if data.get("screenshot"):
            artifacts.append(
                {
                    "type": "screenshot",
                    "path": data["screenshot"].get("path"),
                    "url": data["screenshot"].get("url"),
                }
            )
        return ok(
            data,
            evidence=(
                f"Observed {data.get('window_count', 0)} window(s), "
                f"{data.get('element_count', 0)} UI element(s); "
                f"sources: {', '.join(data.get('detection_sources', []))}"
            ),
            verified=True,
            artifacts=artifacts,
        )

    async def screenshot(args: ScreenshotArgs, ctx: ToolContext):
        task_id, _ = _ids(ctx)
        data = await ctx.services.computer.screenshot(args.monitor, task_id=task_id)
        return ok(
            data,
            evidence=f"Captured desktop screenshot ({data.get('size', 0)} bytes)",
            verified=True,
            artifacts=[{"type": "screenshot", "path": data.get("path"), "url": data.get("url")}],
        )

    async def open_application(args: OpenAppArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.open_application(
            args.app_name,
            task_id=task_id,
            conversation_id=conversation_id,
            timeout=args.timeout_seconds,
        )
        verification = data["verification"]
        return ok(
            data,
            evidence=f"Launched {data['launched'].get('launched')}; {verification.get('detail')}",
            verified=bool(verification.get("verified")),
        )

    async def focus_window(args: FocusArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.focus_window(
            args.window_name, task_id=task_id, conversation_id=conversation_id
        )
        verification = data["verification"]
        return ok(
            data,
            evidence=verification.get("detail", ""),
            verified=bool(verification.get("verified")),
        )

    async def click(args: ClickArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.click(
            args.x,
            args.y,
            button=args.button,
            clicks=args.clicks,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        verification = data["verification"]
        return ok(data, evidence=verification.get("evidence") or verification.get("detail", ""), verified=False)

    async def click_element(args: ClickElementArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.click_element(
            args.label,
            window_name=args.window_name,
            role=args.role,
            button=args.button,
            clicks=args.clicks,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        verification = data["verification"]
        return ok(
            data,
            evidence=verification.get("evidence") or verification.get("detail", ""),
            verified=bool(verification.get("verified")),
        )

    async def consequential_click(args: ConsequentialClickArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.click_element(
            args.label,
            window_name=args.window_name,
            role=args.role,
            button=args.button,
            clicks=args.clicks,
            approved=True,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        verification = data["verification"]
        return ok(
            data,
            evidence=f"Approved consequential action ({args.reason}): {verification.get('detail')}",
            verified=bool(verification.get("verified")),
        )

    async def move_pointer(args: MoveArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.move_pointer(
            args.x, args.y, task_id=task_id, conversation_id=conversation_id
        )
        return ok(data, evidence=f"Moved pointer to ({args.x},{args.y})", verified=False)

    async def scroll(args: ScrollArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.scroll(
            args.direction,
            args.amount,
            x=args.x,
            y=args.y,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        return ok(data, evidence=data["scroll"].__repr__(), verified=False)

    async def drag(args: DragArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.drag(
            args.from_x,
            args.from_y,
            args.to_x,
            args.to_y,
            button=args.button,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        return ok(data, evidence=data["verification"]["evidence"], verified=False)

    async def type_text(args: TypeArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.type_text(
            args.text,
            window_name=args.window_name,
            verify=args.verify,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        verification = data["verification"]
        return ok(
            data,
            evidence=verification.get("evidence") or verification.get("detail", ""),
            verified=bool(verification.get("verified")),
        )

    async def press_key(args: KeyArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.press_key(
            args.key, task_id=task_id, conversation_id=conversation_id
        )
        return ok(data, evidence=f"Pressed {args.key}; verify the effect with a fresh observation.", verified=False)

    async def hotkey(args: HotkeyArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.hotkey(
            args.keys, task_id=task_id, conversation_id=conversation_id
        )
        return ok(data, evidence=f"Sent hotkey {'+'.join(args.keys)}; verify the effect.", verified=False)

    async def select_all(args: EmptyArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.select_all(
            task_id=task_id, conversation_id=conversation_id
        )
        return ok(data, evidence="Sent Ctrl+A; selection is not independently verified.", verified=False)

    async def copy(args: CopyArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.copy(
            expected=args.expected, task_id=task_id, conversation_id=conversation_id
        )
        verification = data.get("verification") or {}
        return ok(
            data,
            evidence=verification.get("detail") or "Sent Ctrl+C; clipboard was not compared with an expected value.",
            verified=bool(verification.get("verified")),
        )

    async def paste_text(args: PasteArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        data = await ctx.services.computer.paste_text(
            args.text,
            verify=args.verify,
            window_name=args.window_name,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        verification = data["verification"]
        return ok(
            data,
            evidence=verification.get("evidence") or verification.get("detail", ""),
            verified=bool(verification.get("verified")),
        )

    async def read_clipboard(args: EmptyArgs, ctx: ToolContext):
        text = await ctx.services.computer.read_clipboard()
        return ok(
            {"text": text[:20000], "chars": len(text)},
            evidence=f"Read {len(text)} clipboard character(s)",
            verified=True,
        )

    async def verify_text(args: VerifyTextArgs, ctx: ToolContext):
        task_id, conversation_id = _ids(ctx)
        verification = await ctx.services.computer.verify_text(
            args.expected,
            window_name=args.window_name,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        data = {"verification": verification}
        if verification.get("verified"):
            return ok(data, evidence=verification.get("evidence") or verification.get("detail", ""), verified=True)
        return fail(
            verification.get("detail", "Text could not be verified."),
            hint="Observe the window again, or report that the outcome could not be confirmed.",
        )

    registry.register(build_tool(
        name="computer.observe",
        description="Observe the Windows desktop: screen geometry, active window, all visible windows, UI Automation elements, screenshot and optional OCR.",
        input_model=ObserveArgs,
        handler=observe,
        permission=ToolPermission.READ,
        timeout=90,
        tags=("computer", "observation"),
    ))
    registry.register(build_tool(
        name="computer.screenshot",
        description="Capture the desktop (all monitors or one) and return a preview URL.",
        input_model=ScreenshotArgs,
        handler=screenshot,
        permission=ToolPermission.READ,
        timeout=45,
        tags=("computer", "observation"),
    ))
    registry.register(build_tool(
        name="computer.open_application",
        description="Launch a supported application and verify that its window appeared.",
        input_model=OpenAppArgs,
        handler=open_application,
        permission=ToolPermission.WRITE,
        timeout=45,
        tags=("computer", "window"),
    ))
    registry.register(build_tool(
        name="computer.focus_window",
        description="Focus a window by title and verify the active window afterwards.",
        input_model=FocusArgs,
        handler=focus_window,
        permission=ToolPermission.WRITE,
        timeout=30,
        tags=("computer", "window"),
    ))
    registry.register(build_tool(
        name="computer.click",
        description="Click at validated screen coordinates. Consequential UI labels are refused without approval.",
        input_model=ClickArgs,
        handler=click,
        permission=ToolPermission.WRITE,
        timeout=25,
        tags=("computer", "input", "mouse"),
    ))
    registry.register(build_tool(
        name="computer.click_element",
        description="Click an observed UI Automation element by label. Refuses consequential labels without approval.",
        input_model=ClickElementArgs,
        handler=click_element,
        permission=ToolPermission.WRITE,
        timeout=35,
        tags=("computer", "input", "mouse", "uia"),
    ))
    registry.register(build_tool(
        name="computer.consequential_click",
        description="Click a send/submit/purchase/delete/install/security element. Always requires explicit approval.",
        input_model=ConsequentialClickArgs,
        handler=consequential_click,
        permission=ToolPermission.DESTRUCTIVE,
        requires_approval=True,
        approval_reason="This click can send, submit, purchase, delete, install or change security settings.",
        timeout=35,
        tags=("computer", "input", "mouse", "consequential"),
    ))
    registry.register(build_tool(
        name="computer.move_pointer",
        description="Move the mouse pointer to validated screen coordinates.",
        input_model=MoveArgs,
        handler=move_pointer,
        permission=ToolPermission.WRITE,
        timeout=20,
        tags=("computer", "input", "mouse"),
    ))
    registry.register(build_tool(
        name="computer.scroll",
        description="Scroll the window under a point (or the current cursor).",
        input_model=ScrollArgs,
        handler=scroll,
        permission=ToolPermission.WRITE,
        timeout=20,
        tags=("computer", "input", "mouse"),
    ))
    registry.register(build_tool(
        name="computer.drag",
        description="Drag from one validated screen point to another.",
        input_model=DragArgs,
        handler=drag,
        permission=ToolPermission.WRITE,
        timeout=25,
        tags=("computer", "input", "mouse"),
    ))
    registry.register(build_tool(
        name="computer.type_text",
        description="Type text into the focused window, then re-observe and verify the text where possible.",
        input_model=TypeArgs,
        handler=type_text,
        permission=ToolPermission.WRITE,
        timeout=45,
        tags=("computer", "input", "keyboard"),
    ))
    registry.register(build_tool(
        name="computer.press_key",
        description="Press one key in the focused window. Shell-launching keys are blocked.",
        input_model=KeyArgs,
        handler=press_key,
        permission=ToolPermission.WRITE,
        timeout=20,
        tags=("computer", "input", "keyboard"),
    ))
    registry.register(build_tool(
        name="computer.hotkey",
        description="Send a key combination in the focused window. Shell-launching combinations are blocked.",
        input_model=HotkeyArgs,
        handler=hotkey,
        permission=ToolPermission.WRITE,
        timeout=20,
        tags=("computer", "input", "keyboard"),
    ))
    registry.register(build_tool(
        name="computer.select_all",
        description="Select all content in the focused window (Ctrl+A).",
        input_model=EmptyArgs,
        handler=select_all,
        permission=ToolPermission.WRITE,
        timeout=20,
        tags=("computer", "input", "keyboard"),
    ))
    registry.register(build_tool(
        name="computer.copy",
        description="Copy the current selection (Ctrl+C). Optionally verify the clipboard against expected text.",
        input_model=CopyArgs,
        handler=copy,
        permission=ToolPermission.WRITE,
        timeout=25,
        tags=("computer", "input", "keyboard", "clipboard"),
    ))
    registry.register(build_tool(
        name="computer.paste_text",
        description="Place text on the clipboard (Ctrl+V) into the focused window and verify it when possible.",
        input_model=PasteArgs,
        handler=paste_text,
        permission=ToolPermission.WRITE,
        timeout=45,
        tags=("computer", "input", "keyboard", "clipboard"),
    ))
    registry.register(build_tool(
        name="computer.read_clipboard",
        description="Read the current clipboard text. Treated as sensitive because it may contain private data.",
        input_model=EmptyArgs,
        handler=read_clipboard,
        permission=ToolPermission.SENSITIVE,
        requires_approval=True,
        approval_reason="The clipboard may contain private information.",
        timeout=20,
        tags=("computer", "clipboard", "sensitive"),
    ))
    registry.register(build_tool(
        name="computer.verify_text",
        description="Verify expected text from a fresh UI Automation/OCR observation. Fails honestly when no evidence exists.",
        input_model=VerifyTextArgs,
        handler=verify_text,
        permission=ToolPermission.READ,
        timeout=45,
        tags=("computer", "verification"),
    ))
