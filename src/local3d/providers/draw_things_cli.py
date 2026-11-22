import hashlib
import platform
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from PIL import Image
from pydantic import BaseModel, ValidationError

from ..models import GenerateRequest, StageMetrics
from ..runtime import engine_lock, file_lock, run_stage
from ..settings import Local3DError, Settings

RELEASE = "v26.1023.0"
DOWNLOAD = f"https://github.com/drawthingsai/draw-things-community/releases/download/{RELEASE}/draw-things-cli"
SHA256 = "5900f7a2f42e00b3b028c0fbcfcb799bd1da8905a2901b6b5bfbd32088c4a049"
MAX_BINARY_BYTES = 300 * 2**20
DEFAULT_MODEL = "flux_2_klein_4b_q6p.ckpt"


class DrawThingsProfile(BaseModel):
    release: str
    model: str
    models_dir: Path | None = None


class DrawThingsCLI:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.models / "draw-things-cli" / RELEASE
        self.binary = self.root / "draw-things-cli"
        self.manifest = self.root / "ready.json"

    def profile(self) -> DrawThingsProfile | None:
        try:
            profile = DrawThingsProfile.model_validate_json(self.manifest.read_text())
        except (OSError, ValidationError):
            return None
        return profile if self.binary.is_file() and profile.release == RELEASE else None

    def run_image(
        self,
        request: GenerateRequest,
        output: Path,
        profile: DrawThingsProfile,
        *,
        log: Path,
        lock_fd: int,
        download_missing: bool = False,
    ) -> StageMetrics:
        if request.prompt is None:
            raise Local3DError("Draw Things requires a text prompt.")
        prompt = output.with_name("draw-things-prompt.txt")
        prompt.write_text(
            f"{request.prompt}\nA single centered object, clear silhouette, three-quarter view, "
            "plain background, no lettering or watermark.\n",
            encoding="utf-8",
        )
        try:
            command: list[Path | str] = [
                self.binary,
                "generate",
                "--local",
                "--disable-preview",
                "--model",
                profile.model,
                "--prompt-file",
                prompt,
                "--width",
                "512",
                "--height",
                "512",
                "--steps",
                "4",
                "--cfg",
                "1",
                "--seed",
                str(request.seed),
                "--output",
                output,
            ]
            if download_missing:
                command.append("--download-missing")
            else:
                command.extend(["--offline", "--no-download-missing"])
            if profile.models_dir is not None:
                command.extend(["--models-dir", profile.models_dir])
            metrics = run_stage(
                "Draw Things local image",
                command,
                log=log,
                lock_fd=lock_fd,
                timeout=self.settings.timeout,
            )
            try:
                with Image.open(output, formats=["PNG"]) as image:
                    image.verify()
            except (OSError, ValueError) as exc:
                raise Local3DError("Draw Things did not save a valid PNG.") from exc
            return metrics
        finally:
            prompt.unlink(missing_ok=True)

    def install(self, model: str, models_dir: Path | None = None) -> Path:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise Local3DError("The pinned Draw Things CLI requires macOS ARM64.")
        if not model.lower().startswith(("flux_", "flux.")):
            raise Local3DError("Choose an official FLUX checkpoint name or file ID.")
        directory = models_dir.expanduser().resolve() if models_dir is not None else None
        profile = DrawThingsProfile(release=RELEASE, model=model, models_dir=directory)
        log = self.root / "install.log"
        with file_lock(self.root / "install.lock"):
            if self.profile() == profile:
                return self.root
            try:
                self._install_binary()
                with (
                    TemporaryDirectory(dir=self.root) as scratch,
                    engine_lock(self.settings) as lock_fd,
                ):
                    request = GenerateRequest(prompt="A red ceramic mug on a white background.")
                    self.run_image(
                        request,
                        Path(scratch) / "reference.png",
                        profile,
                        log=log,
                        lock_fd=lock_fd,
                        download_missing=True,
                    )
            except (OSError, Local3DError) as exc:
                raise Local3DError(f"{exc} Draw Things install log: {log}") from exc
            temp = self.manifest.with_suffix(".tmp")
            temp.write_text(profile.model_dump_json(), encoding="utf-8")
            temp.replace(self.manifest)
        return self.root

    def _install_binary(self) -> None:
        if self.binary.is_file():
            with self.binary.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() == SHA256:
                    self.binary.chmod(0o700)
                    return
        with TemporaryDirectory(dir=self.root) as scratch:
            target = Path(scratch) / "draw-things-cli"
            digest = hashlib.sha256()
            received = 0
            try:
                with (
                    httpx.stream("GET", DOWNLOAD, follow_redirects=True, timeout=60) as response,
                    target.open("wb") as file,
                ):
                    response.raise_for_status()
                    for chunk in response.iter_bytes(2**20):
                        received += len(chunk)
                        if received > MAX_BINARY_BYTES:
                            raise Local3DError("Draw Things download exceeded the expected size.")
                        digest.update(chunk)
                        file.write(chunk)
            except httpx.HTTPError as exc:
                raise Local3DError("Could not download the official Draw Things CLI.") from exc
            if digest.hexdigest() != SHA256:
                raise Local3DError("Draw Things CLI checksum did not match the pinned release.")
            target.chmod(0o700)
            target.replace(self.binary)
