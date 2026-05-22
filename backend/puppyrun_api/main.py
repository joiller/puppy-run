from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from puppyrun_api.config import get_settings
from puppyrun_api.routes import health


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

    app.include_router(health.router)
    return app


app = create_app()
