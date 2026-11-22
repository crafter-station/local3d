from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .engine import Engine
from .models import GenerateRequest
from .queue import run_queue
from .settings import Settings


def create_server(settings: Settings) -> FastMCP:
    engine = Engine(settings)

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        async with run_queue(engine):
            yield {}

    server = FastMCP("Local3D", lifespan=lifespan)

    @server.tool
    def generate_asset(request: GenerateRequest) -> dict:
        """Queue one local 3D asset from an absolute image path or prompt. Returns a job ID."""
        return engine.enqueue(request).model_dump(mode="json", exclude_none=True)

    @server.tool
    def get_job(job_id: str) -> dict:
        """Get status and local asset metadata. Poll the returned ID until the job finishes."""
        job = engine.jobs.get(job_id)
        if job is None:
            raise ToolError("Job not found.")
        return job.model_dump(mode="json", exclude_none=True)

    return server
