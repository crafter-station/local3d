from pathlib import Path

from PIL import Image

from local3d.models import GenerateRequest, StageMetrics
from local3d.providers import mflux
from local3d.settings import Settings


def test_mflux_runs_with_pinned_local_model_and_offline_environment(tmp_path, monkeypatch):
    provider = mflux.MFlux(Settings(tmp_path, None))

    def generate(_stage, args, *, env, **_kwargs):
        args = list(map(str, args))
        assert args[args.index("--model") + 1] == str(provider.weights)
        assert args[args.index("--seed") + 1] == "123"
        assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
        Image.new("RGB", (32, 32)).save(Path(args[args.index("--output") + 1]), "PNG")
        return StageMetrics(seconds=1.0)

    monkeypatch.setattr(mflux, "run_stage", generate)
    output = tmp_path / "reference.png"
    result = provider.run_image(
        GenerateRequest(prompt="A red mug", seed=123),
        output,
        log=tmp_path / "run.log",
        lock_fd=0,
    )
    assert result.seconds == 1.0
    with Image.open(output) as image:
        assert image.size == (32, 32)
