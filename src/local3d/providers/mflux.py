import os
import platform
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from ..models import GenerateRequest, StageMetrics
from ..runtime import engine_lock, file_lock, run_stage
from ..settings import Local3DError, Settings

VERSION = "0.24.0"
MODEL = "mlx-community/flux2-klein-4b-4bit"
REVISION = "860e87183ceb29e39627c0612ebd66d8ea66e68c"
DOWNLOAD = """\
import sys
from huggingface_hub import snapshot_download
snapshot_download(
    sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3],
    allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "tokenizer/chat_template.jinja"], max_workers=4,
)
"""


class MFlux:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.models / "mflux-flux2"
        self.weights = self.root / "weights"
        self.cli = self.root / ".venv/bin/mflux-generate-flux2"
        self.marker = self.root / f"ready-{VERSION}-{REVISION}"

    def ready(self) -> bool:
        return self.marker.is_file() and self.cli.is_file() and self.weights.is_dir()

    def run_image(
        self,
        request: GenerateRequest,
        output: Path,
        *,
        log: Path,
        lock_fd: int,
    ) -> StageMetrics:
        if request.prompt is None:
            raise Local3DError("MFLUX requires a text prompt.")
        env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HOME": str(self.root / "hf"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }
        # Single-image 3D models read every pixel as geometry or albedo. Cast shadows, floor
        # planes, reflections and cropped edges come back as bumps, holes or baked stains.
        prompt = (
            f"{request.prompt}. A single complete object, centered and fully in frame with "
            "empty margin around it, three-quarter front view at eye level, "
            "soft even studio lighting, no cast shadow, no floor, no reflections, no text, "
            "isolated on a plain neutral gray background, clean product render."
        )
        size = "768" if request.mode == "fast" else "1024"
        metrics = run_stage(
            "Generate reference with MFLUX",
            [
                self.cli,
                "--model",
                self.weights,
                "--base-model",
                "flux2-klein-4b",
                "--prompt",
                prompt,
                "--steps",
                "4",
                "--seed",
                str(request.seed),
                "--width",
                size,
                "--height",
                size,
                "--output",
                output,
                "--no-exif",
            ],
            log=log,
            lock_fd=lock_fd,
            timeout=self.settings.timeout,
            env=env,
        )
        try:
            with Image.open(output, formats=["PNG"]) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise Local3DError("MFLUX did not save a valid reference PNG.") from exc
        return metrics

    def install(self) -> Path:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise Local3DError("MFLUX requires macOS ARM64.")
        uv = shutil.which("uv")
        if not uv:
            raise Local3DError("Install uv before installing MFLUX.")
        with file_lock(self.root / "install.lock") as lock_fd:
            if self.ready():
                return self.root
            log = self.root / "install.log"
            if shutil.disk_usage(self.root).free < 7 * 2**30:
                raise Local3DError("Allow at least 7 GiB free for MFLUX and its checkpoint.")
            env = {
                **os.environ,
                "UV_CACHE_DIR": str(self.settings.home / "uv-cache"),
                "HF_HOME": str(self.root / "hf"),
                "HF_HUB_DISABLE_TELEMETRY": "1",
            }
            python = self.root / ".venv/bin/python"
            commands = [
                (
                    "Create MFLUX environment",
                    [uv, "venv", "--allow-existing", "--python", "3.12", self.root / ".venv"],
                ),
                ("Install MFLUX", [uv, "pip", "install", "--python", python, f"mflux=={VERSION}"]),
                (
                    "Download FLUX.2 weights",
                    [python, "-c", DOWNLOAD, MODEL, REVISION, self.weights],
                ),
            ]
            try:
                for stage, args in commands:
                    run_stage(
                        stage,
                        args,
                        log=log,
                        lock_fd=lock_fd,
                        timeout=self.settings.timeout,
                        env=env,
                    )
                with (
                    TemporaryDirectory(dir=self.root) as scratch,
                    engine_lock(self.settings) as gpu_fd,
                ):
                    self.run_image(
                        GenerateRequest(prompt="A red ceramic mug"),
                        Path(scratch) / "reference.png",
                        log=log,
                        lock_fd=gpu_fd,
                    )
            except Local3DError as exc:
                raise Local3DError(f"{exc} MFLUX install log: {log}") from exc
            self.marker.touch()
            return self.root
