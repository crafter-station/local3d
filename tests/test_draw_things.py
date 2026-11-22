import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from local3d.models import GenerateRequest, StageMetrics
from local3d.providers import draw_things
from local3d.settings import Local3DError


@pytest.mark.parametrize("success", [True, False])
def test_draw_things_releases_server_even_on_failure(tmp_path, monkeypatch, success):
    calls = []
    png = BytesIO()
    Image.new("RGB", (16, 16), "seagreen").save(png, "PNG")
    encoded = base64.b64encode(png.getvalue()).decode()
    real_client = httpx.Client

    def handler(request):
        if request.method == "GET":
            if "release" in calls:
                raise httpx.ConnectError("Server stopped")
            calls.append("options")
            return httpx.Response(200, json={"model": "flux_1_schnell_q5p.ckpt"})
        calls.append("generate")
        assert request.url.path == "/sdapi/v1/txt2img"
        assert json.loads(request.content)["model"] == "flux_1_schnell_q5p.ckpt"
        return httpx.Response(200, json={"images": [encoded] if success else []})

    def release(*args, **kwargs):
        calls.append("release")
        return StageMetrics(seconds=0.1)

    monkeypatch.setenv("LOCAL3D_DRAW_THINGS_MODEL", "flux_1_schnell_q5p.ckpt")
    monkeypatch.setenv("LOCAL3D_DRAW_THINGS_RELEASE_COMMAND", '["/usr/bin/true"]')
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(draw_things, "run_stage", release)
    file = tmp_path / "reference.png"
    request = GenerateRequest(prompt="ceramic mug", allow_experimental=True)

    if success:
        draw_things.DrawThings().generate(request, file, log=tmp_path / "log", lock_fd=0, timeout=2)
        with Image.open(file) as image:
            assert image.size == (16, 16)
    else:
        with pytest.raises(Local3DError, match="no reference"):
            draw_things.DrawThings().generate(
                request,
                file,
                log=tmp_path / "log",
                lock_fd=0,
                timeout=2,
            )
    assert calls == ["options", "generate", "release"]


def test_draw_things_refuses_nonlocal_endpoint(monkeypatch):
    monkeypatch.setenv("LOCAL3D_DRAW_THINGS_URL", "https://example.org")
    with pytest.raises(Local3DError, match="loopback"):
        draw_things.DrawThings()
