import asyncio
from pathlib import Path

import httpx
import pytest

from local3d.api import create_app
from local3d.engine import Engine
from local3d.models import GenerateRequest
from local3d.runtime import engine_lock
from local3d.settings import Settings, find_blender


def test_api_persists_jobs_and_checks_loopback(tmp_path):
    settings = Settings(tmp_path, None)
    app = create_app(settings)

    async def check():
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
            ) as client,
        ):
            assert (await client.get("/health")).json() == {"status": "ok"}
            other_host = await client.get("/health", headers={"Host": "other.example"})
            assert other_host.status_code == 403
            other_origin = await client.post(
                "/v1/jobs", headers={"Origin": "https://other.example"}
            )
            assert other_origin.status_code == 403
            assert (await client.post("/v1/jobs", json={})).status_code == 422
            assert (await client.get("/v1/jobs/fake")).status_code == 404
            response = await client.post("/v1/jobs", json={"provider": "demo", "prompt": "cube"})
            assert response.status_code == 202
            job_id = response.json()["id"]
            async with asyncio.timeout(20):
                while True:
                    job = (await client.get(f"/v1/jobs/{job_id}")).json()
                    if job["status"] == "failed":
                        break
                    await asyncio.sleep(0.1)
            assert "Blender" in job["error"]
            assert (await client.get("/v1/jobs")).json()[0] == job
            return job_id

    job_id = asyncio.run(check())
    assert Engine(settings).jobs.get(job_id).status == "failed"


def test_rest_can_stop_while_another_process_holds_the_engine(tmp_path):
    settings = Settings(tmp_path, None)
    engine = Engine(settings)
    job = engine.enqueue(GenerateRequest(provider="demo", prompt="cube"))

    async def check():
        app = create_app(settings)
        async with asyncio.timeout(3):
            async with app.router.lifespan_context(app):
                await asyncio.sleep(0.5)
        assert engine.jobs.get(job.id).status == "queued"

    with engine_lock(settings):
        asyncio.run(check())


def test_mcp_exposes_two_small_tools(tmp_path):
    fastmcp = pytest.importorskip("fastmcp")
    from local3d.mcp_server import create_server

    async def check():
        async with fastmcp.Client(create_server(Settings(tmp_path, None))) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == {"generate_asset", "get_job"}
            queued = await client.call_tool(
                "generate_asset",
                {"request": {"provider": "demo", "prompt": "cube"}},
            )
            assert queued.data["status"] == "queued"
            job_id = queued.data["id"]
            async with asyncio.timeout(10):
                while True:
                    job = await client.call_tool("get_job", {"job_id": job_id})
                    if job.data["status"] == "failed":
                        break
                    await asyncio.sleep(0.1)
            assert "Blender" in job.data["error"]

    asyncio.run(check())


@pytest.mark.blender
def test_headless_blender_generates_optimizes_exports_and_renders(tmp_path):
    blender = find_blender()
    if blender is None:
        pytest.skip("Blender is not installed")
    engine = Engine(Settings(tmp_path, blender))
    job = engine.generate(GenerateRequest(provider="demo", prompt="torus"))
    assert job.status == "succeeded", job.error
    assert job.asset.path.read_bytes()[:4] == b"glTF"
    assert job.asset.triangles > 100
    compact = engine.process("optimize", job.asset.path, tmp_path / "compact.glb", ratio=0.4)
    assert 0 < compact.triangles < job.asset.triangles
    blend = engine.process("export", compact.path, tmp_path / "editable.blend")
    assert blend.bytes > 0
    output = engine.process("preview", Path(blend.path), tmp_path / "preview.png", preview_size=128)
    assert output.path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
