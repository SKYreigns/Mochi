"""Computer-use engine status and observation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.api.deps import services_from

router = APIRouter(tags=["computer"])


@router.get("/api/computer/state")
async def computer_state(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return await services.computer.state()


@router.get("/api/computer/observe")
async def computer_observe(
    request: Request,
    include_elements: bool = Query(default=True),
    include_ocr: bool = Query(default=False),
    include_screenshot: bool = Query(default=True),
) -> dict[str, Any]:
    """Read-only observation. Observation is allowed in every permission mode."""
    services = services_from(request)
    observation = await services.computer.observe(
        include_elements=include_elements,
        include_ocr=include_ocr,
        include_screenshot=include_screenshot,
    )
    return {"observation": observation}
