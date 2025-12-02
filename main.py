# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.logging_config import configure_logging
from app.api.http import router as query_router


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="MovieLens NL2SQL Service",
        version="1.0.0",
        description="Convert natural language questions about movies into SQL and execute on private.movielens_view.",
    )

    # Allow browser UI to call the API during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(query_router, prefix="/api", tags=["query"])

    # Serve the simple web UI at /
    app.mount("/", StaticFiles(directory="web", html=True), name="web")

    return app


app = create_app()
