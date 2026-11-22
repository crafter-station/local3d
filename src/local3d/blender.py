import json
import tempfile
from pathlib import Path
from typing import Literal

from .models import Asset
from .runtime import run_stage
from .settings import Local3DError, Settings

Action = Literal["export", "optimize", "preview", "demo"]
MESH_SUFFIXES = {".glb", ".blend"}


def process(
    settings: Settings,
    action: Action,
    source: Path | str,
    output: Path,
    log: Path,
    lock_fd: int,
    *,
    ratio: float = 0.5,
    preview_size: int = 512,
) -> Asset:
    if settings.blender is None:
        raise Local3DError("Blender was not found. Set LOCAL3D_BLENDER to its executable.")
    if action != "demo":
        source = Path(source).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in MESH_SUFFIXES:
            raise Local3DError("Provide an existing GLB or BLEND input file.")
    allowed = {".png"} if action == "preview" else MESH_SUFFIXES
    output = output.expanduser().resolve()
    if output.suffix.lower() not in allowed:
        raise Local3DError(
            f"Expected an output with one of these extensions: {', '.join(sorted(allowed))}."
        )
    if output.exists():
        raise Local3DError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("blender_worker.py")
    with tempfile.TemporaryDirectory(prefix=".local3d-", dir=output.parent) as directory:
        temp_output = Path(directory) / output.name
        info = Path(directory) / "info.json"
        metrics = run_stage(
            "Blender",
            [
                settings.blender,
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                script,
                "--",
                action,
                source,
                temp_output,
                info,
                str(ratio),
                str(preview_size),
            ],
            log=log,
            lock_fd=lock_fd,
            timeout=settings.timeout,
        )
        if not temp_output.is_file() or not info.is_file():
            raise Local3DError("Blender did not produce the expected file and metadata.")
        try:
            asset = Asset(
                path=output,
                bytes=temp_output.stat().st_size,
                stages={"blender": metrics},
                **json.loads(info.read_text()),
            )
            # A same-filesystem hard link publishes the complete file without clobbering a race.
            output.hardlink_to(temp_output)
        except FileExistsError as exc:
            raise Local3DError(f"Output already exists: {output}") from exc
        return asset
