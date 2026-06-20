from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from puppyrun_api.config import get_settings
from puppyrun_api.demo_limits import DemoSafetyException
from puppyrun_api.routes import admin, health, sessions


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PuppyRun API", version="0.1.0")

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin).rstrip("/") for origin in settings.cors_origins],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(DemoSafetyException)
    async def demo_safety_exception_handler(
        _request,
        exc: DemoSafetyException,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload.model_dump(mode="json"),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request, exc: HTTPException) -> JSONResponse:
        if (
            isinstance(exc.detail, dict)
            and "code" in exc.detail
            and "message" in exc.detail
        ):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(sessions.router)
    return app


app = create_app()
