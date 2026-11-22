import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .models import GenerateRequest, Job
from .providers.draw_things import DrawThings
from .providers.mflux import MFlux
from .queue import run_queue
from .settings import Local3DError, Settings
from .web_assets import viewer_module


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

    web = Path(__file__).with_name("web")
    app.mount("/dashboard-assets", StaticFiles(directory=web), name="dashboard-assets")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(
            web / "index.html",
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self' blob:; img-src 'self' data: blob:; "
                    "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                ),
            },
        )

    @app.get("/v1/dashboard")
    def dashboard_snapshot(request: Request) -> JSONResponse:
        jobs, counts = engine.jobs.dashboard()
        ports = [
            {"service": "Local3D", "port": request.url.port or 80, "status": "online"},
        ]
        try:
            draw = DrawThings()
            link = {"service": "Draw Things HTTP", "port": draw.url.port or 80, "status": "offline"}
            try:
                draw.options(timeout=0.35)
                link["status"] = "online"
            except Local3DError:
                pass
            ports.append(link)
        except Local3DError:
            ports.append({"service": "Draw Things HTTP", "port": None, "status": "unconfigured"})
        return JSONResponse(
            {
                "jobs": [job.model_dump(mode="json", exclude_none=True) for job in jobs],
                "counts": counts,
                "ports": ports,
                "disk_free_bytes": shutil.disk_usage(settings.home).free,
                "image_cli_ready": MFlux(settings).ready(),
                "viewer_ready": viewer_module(settings).is_file(),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/dashboard/viewer-module", include_in_schema=False)
    def dashboard_viewer() -> FileResponse:
        module = viewer_module(settings)
        if not module.is_file():
            raise HTTPException(status_code=404, detail="GLB viewer is not installed.")
        return FileResponse(module, media_type="application/javascript")

    def job_file(job_id: str, name: str) -> Path:
        directory = settings.home / "jobs" / job_id
        path = directory / name
        if path.resolve() != path or not path.is_file():
            raise HTTPException(status_code=404, detail="Job file not found.")
        return path

    @app.get("/v1/jobs/{job_id}/reference")
    def job_reference(job_id: str) -> FileResponse:
        if engine.jobs.get(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return FileResponse(job_file(job_id, "reference.png"), media_type="image/png")

    @app.get("/v1/jobs/{job_id}/artifact")
    def job_artifact(job_id: str, preview: bool = False) -> FileResponse:
        job = engine.jobs.get(job_id)
        if not job or not job.asset or job.status != "succeeded":
            raise HTTPException(status_code=404, detail="Asset not found.")
        name = job.asset.path.name
        if name not in {"asset.glb", "asset.blend"}:
            raise HTTPException(status_code=404, detail="Asset not found.")
        if preview:
            return FileResponse(
                job_file(job_id, "asset.glb" if name == "asset.glb" else "preview.glb"),
                media_type="model/gltf-binary",
            )
        return FileResponse(
            job_file(job_id, name),
            filename=name,
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return app
