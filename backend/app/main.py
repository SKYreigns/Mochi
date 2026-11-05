"""FastAPI application factory and server entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    assignments,
    browser,
    chat,
    computer,
    files,
    memory,
    settings as settings_routes,
    system,
    tasks,
    voice,
    workflows,
)
from app.core.config import Settings, get_settings
from app.core.container import AppServices
from app.core.errors import JarvisError
from app.core.logging import get_logger

log = get_logger("main")


def create_app(app_settings: Settings | None = None, services: AppServices | None = None) -> FastAPI:
    settings = app_settings or get_settings()
    services = services or AppServices(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await services.init()
        log.info("JARVIS backend ready on http://%s:%s", settings.host, settings.port)
        try:
            yield
        finally:
            await services.shutdown()

    app = FastAPI(
        title="JARVIS API",
        description="Local autonomous computer assistant backend",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.services = services

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost|https?://tauri\.localhost)$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(JarvisError)
    async def jarvis_error_handler(request: Request, exc: JarvisError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": exc.to_dict()})

    for router in (
        chat.router,
        chat.ws_router,
        tasks.router,
        files.router,
        browser.router,
        system.router,
        computer.router,
        memory.router,
        workflows.workflows_router,
        workflows.schedules_router,
        settings_routes.router,
        assignments.router,
        voice.router,
    ):
        app.include_router(router)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "services": services.snapshot(), "version": "1.0.0"}

    @app.get("/api/events/history")
    async def event_history(limit: int = 100) -> dict[str, Any]:
        return {"events": services.events.history(limit)}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "app": "JARVIS",
            "docs": "/api/docs",
            "health": "/api/health",
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    current = get_settings()
    uvicorn.run("app.main:app", host=current.host, port=current.port, reload=False)
