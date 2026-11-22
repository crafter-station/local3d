import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from local3d.cli import app
from local3d.engine import Engine
from local3d.jobs import JobStore
from local3d.models import GenerateRequest, Provider
from local3d.runtime import engine_lock
from local3d.settings import Settings


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"prompt": " ", "image": None},
        {"prompt": "chair", "image": "chair.png"},
        {"provider": "demo", "image": "chair.png"},
        {"image": "chair.png", "seed": -1},
    ],
)
def test_invalid_requests(params):
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(params)


def test_mode_defaults():
    assert GenerateRequest(image=Path("a.png")).selected_provider() == Provider.SF3D
    assert (
        GenerateRequest(image=Path("a.png"), mode="quality").selected_provider() == Provider.TRELLIS
    )


def test_job_persistence_and_recovery(tmp_path):
    first = JobStore(tmp_path)
    second = JobStore(tmp_path)
    request = GenerateRequest(provider="demo", prompt="torus")
    orphan = first.create(request)
    pending = first.create(request)
    settings = Settings(tmp_path, None)
    with engine_lock(settings):
        assert first.claim(orphan.id, tmp_path / "orphan.log") == request
    assert second.get(orphan.id).status == "running"
    with engine_lock(settings):
        assert second.claim(pending.id, tmp_path / "pending.log") == request
        second.finish(pending.id, error="No Blender")
    assert first.get(orphan.id).status == "failed"
    assert first.get(pending.id).error == "No Blender"
    assert not first.pending()


def test_lock_serializes_workers(tmp_path):
    settings = Settings(tmp_path, None)
    waiting = threading.Event()
    entered = threading.Event()

    def contender():
        waiting.set()
        with engine_lock(settings):
            entered.set()

    with ThreadPoolExecutor() as pool:
        with engine_lock(settings):
            next_worker = pool.submit(contender)
            assert waiting.wait(2)
            assert not entered.wait(0.1)
        next_worker.result(timeout=2)
    assert entered.is_set()


def test_installed_model_is_required(tmp_path):
    engine = Engine(Settings(tmp_path, Path("/blender")))
    job = engine.generate(GenerateRequest(image=Path("reference.png")))
    assert job.status == "failed"
    assert "models install sf3d" in job.error


def test_experimental_provider_requires_opt_in(tmp_path):
    engine = Engine(Settings(tmp_path, Path("/blender")))
    job = engine.generate(GenerateRequest(image=Path("reference.png"), provider="trellis2"))
    assert job.status == "failed"
    assert "explicit experimental opt-in" in job.error


def test_cli_reports_compact_json_on_error():
    result = CliRunner().invoke(app, ["generate"])
    assert result.exit_code == 1
    assert "exactly one" in json.loads(result.stdout)["error"]


def test_extra_views_need_trellis():
    with pytest.raises(ValidationError, match="trellis2"):
        GenerateRequest(image=Path("a.png"), views=[Path("b.png")])
    with pytest.raises(ValidationError, match="primary image"):
        GenerateRequest(prompt="mug", provider="trellis2", views=[Path("b.png")])
    with pytest.raises(ValidationError):
        GenerateRequest(image=Path("a.png"), mode="quality", views=[Path("v.png")] * 4)
    request = GenerateRequest(image=Path("a.png"), mode="quality", views=[Path("b.png")])
    assert request.selected_provider() == Provider.TRELLIS


def test_geometry_command_passes_every_reference(tmp_path):
    from local3d.models import Mode
    from local3d.providers.geometry import GEOMETRY

    images = [tmp_path / "reference.png", tmp_path / "view-1.png"]
    command, _env = GEOMETRY[Provider.TRELLIS].command(
        Settings(tmp_path, None), images, tmp_path / "raw.glb", Mode.QUALITY, 7
    )
    assert list(command[2:]) == [
        "generate",
        "trellis2",
        GEOMETRY[Provider.TRELLIS].root(Settings(tmp_path, None)),
        tmp_path / "raw.glb",
        "quality",
        "7",
        *images,
    ]


def test_reference_cleanup_drops_specks_and_soft_edges(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from PIL import Image

    from local3d.providers.worker import clean_reference

    pixels = np.zeros((128, 128, 4), dtype=np.uint8)
    pixels[20:100, 20:100] = (200, 40, 40, 255)
    pixels[18:20, 20:100] = (255, 255, 255, 90)  # soft halo
    pixels[110:112, 110:112] = (0, 0, 255, 255)  # stray speck
    source = tmp_path / "source.png"
    Image.fromarray(pixels, "RGBA").save(source)
    result = clean_reference(source, tmp_path / "cutout.png")
    alpha = np.asarray(Image.open(result))[..., 3]
    assert set(np.unique(alpha)) == {0, 255}
    assert alpha[111, 111] == 0 and alpha[19, 50] == 0
    assert alpha[60, 60] == 255
