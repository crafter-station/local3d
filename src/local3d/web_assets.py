import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from .settings import Local3DError, Settings

VIEWER_VERSION = "4.4.0"
VIEWER_URL = (
    f"https://ajax.googleapis.com/ajax/libs/model-viewer/{VIEWER_VERSION}/model-viewer.min.js"
)
VIEWER_SHA256 = "18b2e711035aa4705ee281530a55dc2338ff05eed3d70ae16d54105282a75ed5"


def viewer_module(settings: Settings) -> Path:
    return settings.home / "web" / f"model-viewer-{VIEWER_VERSION}.min.js"


def install_viewer(settings: Settings) -> Path:
    destination = viewer_module(settings)
    if (
        destination.is_file()
        and hashlib.sha256(destination.read_bytes()).hexdigest() == VIEWER_SHA256
    ):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        target = Path(temporary) / "viewer.js"
        digest = hashlib.sha256()
        count = 0
        try:
            with (
                httpx.stream("GET", VIEWER_URL, follow_redirects=True, timeout=30) as response,
                target.open("wb") as file,
            ):
                response.raise_for_status()
                for chunk in response.iter_bytes(64 * 1024):
                    count += len(chunk)
                    if count > 2**21:
                        raise Local3DError("The viewer module exceeded its expected size.")
                    digest.update(chunk)
                    file.write(chunk)
        except httpx.HTTPError as exc:
            raise Local3DError("Could not download the official GLB viewer.") from exc
        if digest.hexdigest() != VIEWER_SHA256:
            raise Local3DError("The GLB viewer checksum did not match its pinned release.")
        target.replace(destination)
    return destination
