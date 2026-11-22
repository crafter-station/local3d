from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .engine import Engine
from .models import GenerateRequest, Job
from .queue import run_queue
from .settings import Settings


def create_app(settings: Settings) -> FastAPI:
    engine = Engine(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with run_queue(engine):
            yield

    app = FastAPI(
        title="Local3D",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if request.url.hostname not in {"127.0.0.1", "localhost"} or (
            origin is not None and origin != f"http://{host}"
        ):
            return JSONResponse({"detail": "Local requests only."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def compact_validation(_request: Request, exc: RequestValidationError):
        errors = [{"location": err["loc"], "message": err["msg"]} for err in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/jobs", status_code=202, response_model_exclude_none=True)
    def submit(request: GenerateRequest) -> Job:
        return engine.enqueue(request)

    @app.get("/v1/jobs", response_model_exclude_none=True)
    def list_jobs(limit: Annotated[int, Query(ge=1, le=100)] = 10) -> list[Job]:
        return engine.jobs.recent(limit)

    @app.get("/v1/jobs/{job_id}", response_model_exclude_none=True)
    def get_job(job_id: str) -> Job:
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return job

    return app
