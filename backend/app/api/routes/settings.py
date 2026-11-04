"""Settings, provider configuration and credential storage endpoints."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import services_from
from app.core.errors import ConfigurationError, JarvisError
from app.core.logging import audit

router = APIRouter(tags=["settings"])

_ALLOWED_SECTIONS = {"permission_mode", "llm", "lms", "voice", "safety", "browser", "allowed_directories"}
_VALID_MODES = {"observe", "assisted", "autonomous"}


class SettingsPatch(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class ApiKeyBody(BaseModel):
    provider: str
    api_key: str = Field(min_length=1)


@router.get("/api/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return {
        **services.settings.public_snapshot(),
        "provider_status": services.provider_manager.status(),
        "emergency_stopped": services.emergency.stopped,
        "tools_count": len(services.registry.all()),
        "app": {
            "version": "1.0.0",
            "data_dir": str(services.app_settings.data_dir),
            "log_dir": str(services.app_settings.log_dir),
            "environment": services.app_settings.environment,
        },
    }


@router.patch("/api/settings")
async def update_settings(body: SettingsPatch, request: Request) -> dict[str, Any]:
    services = services_from(request)
    updates = body.updates or {}
    unknown = set(updates) - _ALLOWED_SECTIONS
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings section(s): {', '.join(sorted(unknown))}")

    if "permission_mode" in updates:
        if updates["permission_mode"] not in _VALID_MODES:
            raise HTTPException(status_code=422, detail="permission_mode must be observe, assisted or autonomous")

    if "allowed_directories" in updates:
        directories = updates["allowed_directories"]
        if not isinstance(directories, list):
            raise HTTPException(status_code=422, detail="allowed_directories must be a list of paths")
        normalized: list[str] = []
        for raw in directories:
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(raw).expanduser()
            if not path.is_absolute() or not path.exists() or not path.is_dir():
                raise HTTPException(status_code=422, detail=f"Directory does not exist: {raw}")
            normalized.append(str(path.resolve()))
        updates["allowed_directories"] = sorted(set(normalized))

    if "llm" in updates:
        llm = updates["llm"]
        if not isinstance(llm, dict):
            raise HTTPException(status_code=422, detail="llm must be an object")
        if "temperature" in llm:
            try:
                temperature = float(llm["temperature"])
                if not 0 <= temperature <= 2:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="temperature must be between 0 and 2") from exc
            llm["temperature"] = temperature

    async with services.db.session() as session:
        for key, value in updates.items():
            await services.settings.set(session, key, value)

    services.refresh_security()
    if "llm" in updates:
        await services.provider_manager.invalidate()
    audit("settings.updated", sections=list(updates.keys()))
    return {"updated": list(updates.keys()), "settings": services.settings.public_snapshot()}


@router.post("/api/settings/api-key")
async def save_api_key(body: ApiKeyBody, request: Request) -> dict[str, Any]:
    services = services_from(request)
    try:
        import keyring

        keyring.set_password("jarvis", f"llm:{body.provider.strip().lower()}", body.api_key)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Windows Credential Manager is unavailable: {exc}. Use an environment variable instead.",
        ) from exc
    await services.provider_manager.invalidate()
    await services.events.publish("settings.updated", {"key": "llm.api_key", "provider": body.provider})
    return {"saved": True, "provider": body.provider}


@router.delete("/api/settings/api-key/{provider}")
async def delete_api_key(provider: str, request: Request) -> dict[str, Any]:
    services = services_from(request)
    try:
        import keyring

        try:
            keyring.delete_password("jarvis", f"llm:{provider.lower()}")
        except keyring.errors.PasswordDeleteError:
            pass
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await services.provider_manager.invalidate()
    return {"deleted": True, "provider": provider}


@router.get("/api/settings/models")
async def list_models(
    request: Request,
    provider: str | None = Query(default=None),
    base_url: str | None = Query(default=None),
) -> dict[str, Any]:
    services = services_from(request)
    try:
        models = await services.provider_manager.list_models(provider=provider, base_url=base_url)
    except ConfigurationError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    except JarvisError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    return {"models": [model.model_dump() for model in models]}


@router.get("/api/settings/provider")
async def provider_status(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return {"provider": services.provider_manager.status()}
