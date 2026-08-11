from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.debug import router as debug_router
from app.api.evals import router as evals_router
from app.api.game import router as game_router
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.api.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.debug.routes import router as strategic_debug_router
from app.infrastructure.logging import configure_logging

configure_logging()
log = structlog.get_logger()
settings = get_settings()
web_dir = Path(__file__).resolve().parent / "web"

app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount("/debug-assets", StaticFiles(directory=web_dir), name="debug-assets")
app.include_router(health_router)
app.include_router(game_router)
app.include_router(sessions_router)
app.include_router(evals_router)
app.include_router(debug_router)
app.include_router(strategic_debug_router)
app.include_router(tasks_router)


@app.get("/debug", include_in_schema=False)
def debug_console() -> FileResponse:
    return FileResponse(web_dir / "index.html")


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
    finally:
        structlog.contextvars.clear_contextvars()


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request.state.request_id,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": exc.errors()},
                "request_id": request.state.request_id,
            }
        },
    )
