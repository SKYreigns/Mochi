"""Browser tool definitions exposed to the LLM."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.tools.base import ToolContext, ToolRegistry, build_tool, fail, ok


class OpenUrlArgs(BaseModel):
    url: str = Field(description="Absolute http(s) URL to open.")
    tab_id: str | None = Field(default=None, description="Existing tab to reuse; omit for a new tab.")


class TabArgs(BaseModel):
    tab_id: str | None = Field(default=None, description="Tab id from browser.list_tabs; defaults to active tab.")


class CloseTabArgs(BaseModel):
    tab_id: str = Field(description="Tab id to close.")


class ScreenshotArgs(BaseModel):
    tab_id: str | None = None
    full_page: bool = False


class ClickArgs(BaseModel):
    tab_id: str | None = None
    selector: str = Field(description="CSS selector, text=..., or role=... locator.")
    description: str = Field(default="", description="Human description of the target for the audit log.")


class TypeArgs(BaseModel):
    tab_id: str | None = None
    selector: str
    text: str


class ClearTypeArgs(BaseModel):
    tab_id: str | None = None
    selector: str
    text: str


class SelectArgs(BaseModel):
    tab_id: str | None = None
    selector: str
    value: str


class ScrollArgs(BaseModel):
    tab_id: str | None = None
    direction: Literal["up", "down"] = "down"
    amount: int = Field(default=600, ge=1, le=20000)


class KeyArgs(BaseModel):
    tab_id: str | None = None
    key: str = Field(description="Playwright key, e.g. 'Enter', 'Control+L', 'Escape'.")


class ExtractArgs(BaseModel):
    tab_id: str | None = None
    selector: str | None = Field(default=None, description="Optional CSS selector to extract only that region.")


class FindArgs(BaseModel):
    tab_id: str | None = None
    description: str = Field(description="Visible text, label, placeholder or purpose of the element.")


class DownloadArgs(BaseModel):
    url: str = Field(description="Direct http(s) download URL.")
    tab_id: str | None = None


class UploadArgs(BaseModel):
    tab_id: str | None = None
    selector: str = Field(description="CSS selector of the file input.")
    file_path: str = Field(description="Absolute path inside the allowed directories.")


def register_browser_tools(registry: ToolRegistry) -> None:
    async def open_url(args: OpenUrlArgs, ctx: ToolContext):
        browser = ctx.services.browser
        browser.set_blocked_sites(ctx.services.settings.safety.get("blocked_websites", []))
        data = await browser.open_url(args.url, args.tab_id, new_tab=args.tab_id is None)
        evidence = f"Opened {data['url']} — title: {data['title']!r}, HTTP {data.get('http_status')}"
        return ok(data, evidence=evidence, verified=bool(data.get("url")))

    async def close_tab(args: CloseTabArgs, ctx: ToolContext):
        data = await ctx.services.browser.close_tab(args.tab_id)
        return ok(data, evidence=f"Closed tab {args.tab_id}")

    async def list_tabs(args: BaseModel, ctx: ToolContext):
        data = await ctx.services.browser.list_tabs()
        return ok({"tabs": data, "count": len(data)}, evidence=f"{len(data)} open tab(s)")

    async def get_current_url(args: TabArgs, ctx: ToolContext):
        data = await ctx.services.browser.get_url(args.tab_id)
        return ok(data, evidence=data["url"])

    async def get_page_title(args: TabArgs, ctx: ToolContext):
        data = await ctx.services.browser.get_title(args.tab_id)
        return ok(data, evidence=data["title"])

    async def snapshot(args: TabArgs, ctx: ToolContext):
        data = await ctx.services.browser.snapshot(args.tab_id)
        return ok(
            data,
            evidence=f"Snapshot of {data['url']}: {len(data['interactive'])} interactive elements",
        )

    async def screenshot(args: ScreenshotArgs, ctx: ToolContext):
        data = await ctx.services.browser.screenshot(args.tab_id, args.full_page)
        return ok(
            data,
            evidence=f"Screenshot saved ({data['size']} bytes)",
            artifacts=[{"type": "screenshot", "path": data["path"], "url": data["url"]}],
        )

    async def click(args: ClickArgs, ctx: ToolContext):
        data = await ctx.services.browser.click(args.selector, args.tab_id)
        evidence = f"Clicked {args.selector!s}; url now {data['url_after']} (navigated={data['navigated']})"
        return ok(data, evidence=evidence)

    async def type_text(args: TypeArgs, ctx: ToolContext):
        data = await ctx.services.browser.type_text(args.selector, args.text, args.tab_id, clear=False)
        return ok(data, evidence=f"Typed {data['typed_chars']} characters into {args.selector}")

    async def fill_input(args: ClearTypeArgs, ctx: ToolContext):
        data = await ctx.services.browser.fill_input(args.selector, args.text, args.tab_id)
        return ok(data, evidence=f"Filled {args.selector}")

    async def select_option(args: SelectArgs, ctx: ToolContext):
        data = await ctx.services.browser.select_option(args.selector, args.value, args.tab_id)
        return ok(data, evidence=f"Selected {args.value} in {args.selector}")

    async def scroll(args: ScrollArgs, ctx: ToolContext):
        data = await ctx.services.browser.scroll(args.direction, args.amount, args.tab_id)
        return ok(data, evidence=f"Scrolled {args.direction} by {args.amount}px")

    async def press_key(args: KeyArgs, ctx: ToolContext):
        data = await ctx.services.browser.press_key(args.key, args.tab_id)
        return ok(data, evidence=f"Pressed {args.key}; url now {data['url']}")

    async def extract_text(args: ExtractArgs, ctx: ToolContext):
        data = await ctx.services.browser.extract_text(args.tab_id, args.selector)
        return ok(data, evidence=f"Extracted {len(data['text'])} characters from {data['url']}")

    async def find_element(args: FindArgs, ctx: ToolContext):
        candidates = await ctx.services.browser.find_element(args.description, args.tab_id)
        if not candidates:
            return fail(
                f"No visible element matched '{args.description}'.",
                hint="Take a browser.snapshot and choose a more precise selector.",
            )
        return ok(
            {"candidates": candidates, "count": len(candidates)},
            evidence=f"{len(candidates)} candidate(s) matched the description",
        )

    async def download_file(args: DownloadArgs, ctx: ToolContext):
        data = await ctx.services.browser.download(args.url, args.tab_id)
        return ok(
            data,
            evidence=f"Downloaded {data['filename']} ({data['size']} bytes) to {data['path']}",
            artifacts=[{"type": "file", "path": data["path"]}],
        )

    async def upload_file(args: UploadArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.file_path, must_exist=True)
        data = await ctx.services.browser.upload_file(args.selector, str(path), args.tab_id)
        if not data.get("attached"):
            return fail("File input accepted no files.", hint="Inspect the page; the selector may target the wrong input.")
        return ok(data, evidence=f"Attached {data['attached']} to {args.selector}")

    registry.register(build_tool(
        name="browser.open_url",
        description="Open an http(s) URL in the persistent browser, optionally reusing a tab.",
        input_model=OpenUrlArgs, handler=open_url, permission=ToolPermission.NAVIGATE, timeout=60,
        tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.close_tab", description="Close a browser tab by id.",
        input_model=CloseTabArgs, handler=close_tab, permission=ToolPermission.WRITE, timeout=20, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.list_tabs", description="List open browser tabs with ids, URLs and titles.",
        input_model=TabArgs, handler=list_tabs, permission=ToolPermission.READ, timeout=20, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.get_current_url", description="Return the current URL of a tab.",
        input_model=TabArgs, handler=get_current_url, permission=ToolPermission.READ, timeout=20, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.get_page_title", description="Return the title of a tab.",
        input_model=TabArgs, handler=get_page_title, permission=ToolPermission.READ, timeout=20, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.snapshot",
        description="Inspect a page: visible text, interactive elements with selectors, forms and JS errors. Use before clicking.",
        input_model=TabArgs, handler=snapshot, permission=ToolPermission.READ, timeout=45, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.screenshot", description="Capture a PNG screenshot of the current tab.",
        input_model=ScreenshotArgs, handler=screenshot, permission=ToolPermission.READ, timeout=45, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.click",
        description="Click an element using a CSS/text/role selector, then report the resulting URL and page errors.",
        input_model=ClickArgs, handler=click, permission=ToolPermission.WRITE, timeout=45, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.type_text", description="Type text into an input (appends keystrokes).",
        input_model=TypeArgs, handler=type_text, permission=ToolPermission.WRITE, timeout=45, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.fill_input", description="Fill an input or textarea, replacing existing content.",
        input_model=ClearTypeArgs, handler=fill_input, permission=ToolPermission.WRITE, timeout=45, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.select_option", description="Select an option in a <select> element by value.",
        input_model=SelectArgs, handler=select_option, permission=ToolPermission.WRITE, timeout=30, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.scroll", description="Scroll the page up or down by pixels.",
        input_model=ScrollArgs, handler=scroll, permission=ToolPermission.NAVIGATE, timeout=30, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.press_key", description="Press a keyboard key in the active page.",
        input_model=KeyArgs, handler=press_key, permission=ToolPermission.WRITE, timeout=30, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.extract_text", description="Extract readable text from the page or a selector.",
        input_model=ExtractArgs, handler=extract_text, permission=ToolPermission.READ, timeout=45, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.find_element",
        description="Find visible elements matching a description; returns candidate selectors without clicking.",
        input_model=FindArgs, handler=find_element, permission=ToolPermission.READ, timeout=45, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.download_file",
        description="Download a direct http(s) file through the logged-in browser profile and verify the saved file.",
        input_model=DownloadArgs, handler=download_file, permission=ToolPermission.WRITE, timeout=90, tags=("browser",),
    ))
    registry.register(build_tool(
        name="browser.upload_file",
        description="Attach a local file to a file input (no submission is performed).",
        input_model=UploadArgs, handler=upload_file, permission=ToolPermission.WRITE, timeout=45, tags=("browser",),
    ))
