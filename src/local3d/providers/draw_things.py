import base64
import binascii
import json
import os
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from ..models import GenerateRequest, StageMetrics
from ..runtime import run_stage
from ..settings import Local3DError

MAX_RESPONSE = 24 * 2**20


class DrawThings:
    def __init__(self) -> None:
        try:
            url = httpx.URL(os.environ.get("LOCAL3D_DRAW_THINGS_URL", "http://127.0.0.1:7860"))
        except httpx.InvalidURL as exc:
            raise Local3DError("Invalid Draw Things URL.") from exc
        if (
            url.scheme != "http"
            or url.host not in {"localhost", "127.0.0.1", "::1"}
            or url.path != "/"
            or url.query
            or url.fragment
            or url.userinfo
        ):
            raise Local3DError("Set a loopback HTTP origin for Draw Things.")
        self.url = url
        self.model = os.environ.get("LOCAL3D_DRAW_THINGS_MODEL")
        self.sampler = os.environ.get("LOCAL3D_DRAW_THINGS_SAMPLER", "Euler A Trailing")

    def options(self, timeout: float = 2) -> dict:
        with httpx.Client(base_url=self.url, trust_env=False, timeout=timeout) as client:
            return self._request(client, "GET", "/sdapi/v1/options")

    @staticmethod
    def _request(client: httpx.Client, method: str, endpoint: str, **kwargs) -> dict:
        try:
            with client.stream(method, endpoint, **kwargs) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise Local3DError("Draw Things response exceeded 24 MiB.")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("Expected an object")
            return value
        except (httpx.HTTPError, ValueError) as exc:
            detail = (
                f" (HTTP {exc.response.status_code})"
                if isinstance(exc, httpx.HTTPStatusError)
                else ""
            )
            raise Local3DError(
                f"Draw Things request failed{detail}. Check the local server and model."
            ) from exc

    @staticmethod
    def release_command() -> list[str]:
        try:
            command = json.loads(os.environ.get("LOCAL3D_DRAW_THINGS_RELEASE_COMMAND", "null"))
        except ValueError as exc:
            raise Local3DError("Draw Things release command must be a JSON argv array.") from exc
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in command)
        ):
            raise Local3DError(
                "Configure LOCAL3D_DRAW_THINGS_RELEASE_COMMAND before prompt generation."
            )
        if not Path(command[0]).is_absolute():
            raise Local3DError(
                "Use an absolute executable path for the Draw Things release command."
            )
        return command

    def generate(
        self,
        request: GenerateRequest,
        output: Path,
        *,
        log: Path,
        lock_fd: int,
        timeout: int,
    ) -> StageMetrics:
        if not self.model or not self.model.lower().startswith("flux_"):
            raise Local3DError("Set LOCAL3D_DRAW_THINGS_MODEL to an installed FLUX checkpoint ID.")
        command = self.release_command()
        self.options()
        started = time.monotonic()
        try:
            with httpx.Client(base_url=self.url, trust_env=False, timeout=timeout) as client:
                payload = {
                    "prompt": request.prompt,
                    "model": self.model,
                    "seed": request.seed,
                    "width": 512,
                    "height": 512,
                    "steps": 4,
                    "guidance_scale": 1.0,
                    "sampler": self.sampler,
                }
                response = self._request(client, "POST", "/sdapi/v1/txt2img", json=payload)
            images = response.get("images")
            if not isinstance(images, list) or not images or not isinstance(images[0], str):
                raise Local3DError("Draw Things returned no reference image.")
            try:
                with Image.open(BytesIO(base64.b64decode(images[0], validate=True))) as image:
                    if image.width > 4096 or image.height > 4096:
                        raise ValueError("Image exceeds size limit")
                    image.convert("RGBA").save(output, "PNG")
            except (OSError, ValueError, binascii.Error, Image.DecompressionBombError) as exc:
                raise Local3DError("Draw Things returned an invalid reference image.") from exc
        finally:
            # Even a timed-out HTTP request may leave inference running in the server.
            run_stage("Release Draw Things", command, log=log, lock_fd=lock_fd, timeout=90)
        try:
            self.options()
        except Local3DError as exc:
            if not isinstance(exc.__cause__, httpx.ConnectError):
                raise Local3DError("Could not confirm Draw Things stopped listening.") from exc
        else:
            raise Local3DError("Draw Things still responds after the release command.")
        return StageMetrics(seconds=round(time.monotonic() - started, 3))
