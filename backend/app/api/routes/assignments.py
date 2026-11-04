"""Assignment workspace endpoints (local records + drafting tasks)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import run_direct_tool, services_from
from app.database.models import Assignment

router = APIRouter(tags=["assignments"])


class AssignmentUpdate(BaseModel):
    subject: str | None = None
    title: str | None = None
    deadline: str | None = None
    status: str | None = None
    submission_status: str | None = None


class DraftRequest(BaseModel):
    format: str = Field(default="pdf", description="pdf, docx or both")
    notes: str = Field(default="", description="Extra user guidance for the draft.")


def _serialize(item: Assignment) -> dict[str, Any]:
    return {
        "id": item.id,
        "subject": item.subject,
        "title": item.title,
        "instructions": item.instructions,
        "deadline": item.deadline,
        "lms_url": item.lms_url,
        "source_url": item.source_url,
        "resources": item.resources,
        "generated_files": item.generated_files,
        "status": item.status,
        "submission_status": item.submission_status,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


@router.get("/api/assignments")
async def list_assignments(request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        rows = (
            await session.execute(select(Assignment).order_by(Assignment.updated_at.desc()))
        ).scalars().all()
        return {"assignments": [_serialize(item) for item in rows]}


@router.patch("/api/assignments/{assignment_id}")
async def update_assignment(assignment_id: str, body: AssignmentUpdate, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        item = await session.get(Assignment, assignment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Assignment not found")
        for field, value in body.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(item, field, value)
        await session.commit()
        return {"assignment": _serialize(item)}


@router.delete("/api/assignments/{assignment_id}")
async def delete_assignment(assignment_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        item = await session.get(Assignment, assignment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Assignment not found")
        await session.delete(item)
        await session.commit()
        return {"deleted": assignment_id}


@router.post("/api/assignments/{assignment_id}/open")
async def open_assignment(assignment_id: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        item = await session.get(Assignment, assignment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Assignment not found")
        url = item.source_url or item.lms_url
    if not url:
        raise HTTPException(status_code=422, detail="This assignment has no LMS URL")
    result = await run_direct_tool(services, "browser.open_url", {"url": url})
    return {"data": result.data}


@router.post("/api/assignments/{assignment_id}/draft")
async def draft_assignment(assignment_id: str, body: DraftRequest, request: Request) -> dict[str, Any]:
    services = services_from(request)
    async with services.db.session() as session:
        item = await session.get(Assignment, assignment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Assignment not found")
        output_dir = services.app_settings.data_dir / "workspace" / "assignments"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_subject = "".join(ch for ch in (item.subject or "General") if ch.isalnum() or ch in " -_")[:40].strip() or "General"
        safe_title = "".join(ch for ch in (item.title or "Assignment") if ch.isalnum() or ch in " -_")[:60].strip() or "Assignment"
        instruction = (
            f"Work on the saved assignment in the workspace.\n"
            f"Subject: {item.subject}\nTitle: {item.title}\nDeadline: {item.deadline or 'unknown'}\n"
            f"Assignment instructions:\n{item.instructions[:8000]}\n\n"
            f"Downloaded resources: {item.resources}\n\n"
            f"Create a well-structured deliverable in {body.format.upper()} format at "
            f"'{output_dir / (safe_subject + ' - ' + safe_title)}'. "
            "Open and read the downloaded resources first. Explain the approach, produce the requested "
            "content in your own words, and never fabricate experimental results or citations. "
            f"Additional user guidance: {body.notes or '(none)'}. "
            "After creating the file, verify it exists and report its path and size. "
            "Do not submit anything; submission requires separate explicit approval."
        )
    task = await services.tasks.start(instruction=instruction, trigger="manual", title=f"Draft: {item.title}")
    return {"task_id": task.id, "assignment_id": assignment_id}
