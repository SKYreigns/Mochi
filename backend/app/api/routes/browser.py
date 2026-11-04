"""Browser control endpoints for the Live Computer panel."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import run_direct_tool, services_from

router = APIRouter(tags=["browser"])


class OpenRequest(BaseModel):
    url: str
    tab_id: str | None = None


class CloseRequest(BaseModel):
    tab_id: str


class ScreenshotRequest(BaseModel):
    tab_id: str | None = None
    full_page: bool = False


@router.get("/api/browser/state")
async def browser_state(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return await services.browser.state()


@router.get("/api/browser/tabs")
async def tabs(request: Request) -> dict[str, Any]:
    services = services_from(request)
    if not services.browser.is_running:
        return {"tabs": [], "running": False}
    return {"tabs": await services.browser.list_tabs(), "running": True}


@router.post("/api/browser/open")
async def open_url(body: OpenRequest, request: Request) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "browser.open_url", body.model_dump())
    return {"data": result.data, "evidence": result.evidence}


@router.post("/api/browser/close")
async def close_tab(body: CloseRequest, request: Request) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "browser.close_tab", {"tab_id": body.tab_id})
    return {"data": result.data, "evidence": result.evidence}


@router.post("/api/browser/screenshot")
async def screenshot(body: ScreenshotRequest, request: Request) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(
        services, "browser.screenshot", {"tab_id": body.tab_id, "full_page": body.full_page}
    )
    return {"data": result.data, "evidence": result.evidence}


@router.get("/api/browser/snapshot")
async def snapshot(request: Request, tab_id: str | None = Query(default=None)) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "browser.snapshot", {"tab_id": tab_id})
    return {"data": result.data, "evidence": result.evidence}
