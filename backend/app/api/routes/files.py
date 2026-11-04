"""Filesystem browser, uploads, previews and downloads."""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import run_direct_tool, services_from
from app.core.security import is_sensitive_file

router = APIRouter(tags=["files"])

_SAFE_UPLOAD_NAME = re.compile(r"[^A-Za-z0-9 ._\-()\[\]]+")


class PreviewRequest(BaseModel):
    directory: str
    mode: str = "by_category"
    include_subfolders: bool = False


@router.get("/api/files/roots")
async def roots(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return {
        "roots": [str(path) for path in services.path_policy.allowed],
        "workspace": str(services.app_settings.data_dir / "workspace"),
        "uploads": str(services.app_settings.data_dir / "uploads"),
    }


@router.get("/api/files/list")
async def list_directory(request: Request, path: str = Query(...)) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "files.list_directory", {"path": path})
    return {"data": result.data, "evidence": result.evidence}


@router.get("/api/files/read")
async def read_file(request: Request, path: str = Query(...), max_chars: int = 20000) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "files.read_file", {"path": path, "max_chars": max_chars})
    return {"data": result.data}


@router.get("/api/files/metadata")
async def metadata(request: Request, path: str = Query(...)) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(services, "files.get_metadata", {"path": path})
    return {"data": result.data}


@router.get("/api/files/download")
async def download(request: Request, path: str = Query(...)) -> FileResponse:
    services = services_from(request)
    try:
        resolved = services.path_policy.validate(path, must_exist=True)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not resolved.is_file() or is_sensitive_file(resolved):
        raise HTTPException(status_code=403, detail="File cannot be downloaded")
    return FileResponse(str(resolved), filename=resolved.name)


@router.post("/api/files/upload")
async def upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    services = services_from(request)
    upload_dir = services.app_settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    raw_name = _SAFE_UPLOAD_NAME.sub("_", Path(file.filename or "upload.bin").name).strip() or "upload.bin"
    target = upload_dir / f"{uuid.uuid4().hex[:8]}_{raw_name}"
    limit = services.app_settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"File exceeds {services.app_settings.max_upload_mb} MB")
                handle.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    mime = file.content_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return {
        "name": raw_name,
        "path": str(target),
        "mime": mime,
        "size": size,
    }


@router.post("/api/files/organize/preview")
async def organize_preview(body: PreviewRequest, request: Request) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(
        services,
        "files.preview_organization",
        {
            "directory": body.directory,
            "mode": body.mode,
            "include_subfolders": body.include_subfolders,
            "dry_run": True,
        },
    )
    return {"data": result.data, "evidence": result.evidence}


@router.get("/api/files/search")
async def search(
    request: Request,
    query: str = Query(..., min_length=1),
    directory: str = Query(...),
    content_pattern: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    services = services_from(request)
    result = await run_direct_tool(
        services,
        "files.search",
        {"query": query, "directory": directory, "content_pattern": content_pattern, "limit": limit},
    )
    return {"data": result.data, "evidence": result.evidence}
