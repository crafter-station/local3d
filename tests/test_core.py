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
