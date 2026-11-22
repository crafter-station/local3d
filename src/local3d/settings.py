import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


def default_home() -> Path:
    root = Path.home() / (
        "Library/Application Support" if sys.platform == "darwin" else ".local/share"
    )
    return root / "Local3D"


def find_blender() -> Path | None:
    candidates = [
        os.environ.get("LOCAL3D_BLENDER"),
        shutil.which("blender"),
        "/Applications/Blender.app/Contents/MacOS/Blender",
        str(Path.home() / "Applications/Blender.app/Contents/MacOS/Blender"),
    ]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK) and Path(candidate).is_file():
            return Path(candidate).resolve()
    return None


@dataclass(frozen=True)
class Settings:
    home: Path
    blender: Path | None
    timeout: int = 1_800

    @classmethod
    def load(cls) -> "Settings":
        root = Path(os.environ.get("LOCAL3D_HOME", str(default_home()))).expanduser().resolve()
        return cls(root, find_blender())

    @property
    def models(self) -> Path:
        return self.home / "models"


class Local3DError(RuntimeError):
    """A short actionable error suitable for CLI, REST, or MCP."""
