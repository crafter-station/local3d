from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from .blender import Action, process
from .jobs import JobStore
from .models import Asset, GenerateRequest, Job, Provider
from .providers.draw_things import DrawThings
from .providers.geometry import GEOMETRY
from .runtime import engine_lock, run_stage
from .settings import Local3DError, Settings


def save_reference(path: Path, output: Path) -> None:
    try:
        if not path.is_file() or path.stat().st_size > 25 * 2**20:
            raise ValueError("Reference file missing or too large")
        with Image.open(path, formats=["PNG", "JPEG", "WEBP"]) as image:
            if image.width > 4096 or image.height > 4096:
                raise ValueError("Reference image dimensions exceed 4096 pixels")
            ImageOps.exif_transpose(image).convert("RGBA").save(output, "PNG")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise Local3DError(
            "Use a readable PNG, JPEG or WebP up to 25 MiB and 4096 × 4096."
        ) from exc


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs = JobStore(settings.home)

    def enqueue(self, request: GenerateRequest) -> Job:
        if request.image is not None:
            request = request.model_copy(update={"image": request.image.expanduser().resolve()})
        return self.jobs.create(request)

    def run(self, job_id: str, *, wait: bool = True) -> Job:
        with engine_lock(self.settings, wait=wait) as lock_fd:
            job = self.jobs.get(job_id)
            if job is None:
                raise Local3DError("Job not found.")
            directory = self.settings.home / "jobs" / job_id
            log = directory / "run.log"
            request = self.jobs.claim(job_id, log)
            if request is not None:
                try:
                    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                    asset = self._generate(request, directory, log, lock_fd)
                except Exception as exc:
                    if isinstance(exc, Local3DError):
                        message = str(exc)
                    else:
                        message = f"Unexpected {type(exc).__name__}. See the local log."
                        if directory.is_dir():
                            import traceback

                            with log.open("a") as stream:
                                traceback.print_exc(file=stream)
                    self.jobs.finish(job_id, error=message)
                except KeyboardInterrupt:
                    self.jobs.finish(job_id, error="Interrupted.")
                    raise
                else:
                    self.jobs.finish(job_id, asset=asset)
            result = self.jobs.get(job_id)
            assert result is not None
            return result

    def generate(self, request: GenerateRequest) -> Job:
        return self.run(self.enqueue(request).id)

    def _generate(
        self, request: GenerateRequest, directory: Path, log: Path, lock_fd: int
    ) -> Asset:
        provider = request.selected_provider()
        output = directory / f"asset.{request.format.value}"
        log.touch()
        if self.settings.blender is None:
            raise Local3DError("Blender was not found. Run local3d doctor.")
        if provider == Provider.DEMO:
            if request.prompt not in {"cube", "icosphere", "torus"}:
                raise Local3DError("Choose cube, icosphere or torus for the procedural demo.")
            return process(self.settings, "demo", request.prompt, output, log, lock_fd)

        if (
            provider == Provider.TRELLIS or request.image is None
        ) and not request.allow_experimental:
            raise Local3DError("TRELLIS.2 and Draw Things require explicit experimental opt-in.")
        geometry = GEOMETRY[provider]
        if not geometry.ready(self.settings):
            raise Local3DError(f"Model is missing. Run local3d models install {provider.value}.")
        stages = {}
        reference = directory / "reference.png"
        if request.image is not None:
            save_reference(request.image, reference)
        else:
            stages["draw_things"] = DrawThings().generate(
                request,
                reference,
                log=log,
                lock_fd=lock_fd,
                timeout=self.settings.timeout,
            )
        raw = directory / "raw.glb"
        command, env = geometry.command(self.settings, reference, raw, request.mode, request.seed)
        stages["geometry"] = run_stage(
            "Generate geometry",
            command,
            log=log,
            lock_fd=lock_fd,
            timeout=self.settings.timeout,
            env=env,
            cwd=geometry.root(self.settings) / "upstream",
        )
        if not raw.is_file():
            raise Local3DError("Geometry provider did not produce a GLB.")
        with raw.open("rb") as stream:
            if stream.read(4) != b"glTF":
                raise Local3DError("Geometry provider did not return a valid GLB header.")
        asset = process(self.settings, "export", raw, output, log, lock_fd)
        raw.unlink()
        return asset.model_copy(update={"stages": stages | asset.stages})

    def process(
        self,
        action: Action,
        source: Path,
        output: Path,
        *,
        ratio: float = 0.5,
        preview_size: int = 512,
    ) -> Asset:
        with engine_lock(self.settings) as lock_fd:
            logs = self.settings.home / "logs"
            logs.mkdir(exist_ok=True)
            log = logs / f"{uuid4().hex}.log"
            try:
                return process(
                    self.settings,
                    action,
                    source,
                    output,
                    log,
                    lock_fd,
                    ratio=ratio,
                    preview_size=preview_size,
                )
            except Local3DError as exc:
                if log.exists():
                    raise Local3DError(f"{exc} Log: {log}") from exc
                raise
