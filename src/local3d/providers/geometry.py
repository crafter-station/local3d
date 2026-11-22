import os
import platform
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..models import Mode, Provider
from ..runtime import engine_lock, file_lock, run_stage
from ..settings import Local3DError, Settings

SF3D_MODEL = "stabilityai/stable-fast-3d"
SF3D_REVISION = "f0c9a8ffd62cb1bbc8a7a53c9f87a0be1b6be778"
DINO_MODEL = "facebook/dinov3-vitl16-pretrain-lvd1689m"
DINO_REVISION = "ea8dc2863c51be0a264bab82070e3e8836b02d51"
TRELLIS_MODEL = "microsoft/TRELLIS.2-4B"
TRELLIS_REVISION = "af44b45f2e35a493886929c6d786e563ec68364d"
TRELLIS_AUX_MODEL = "microsoft/TRELLIS-image-large"
TRELLIS_AUX_REVISION = "25e0d31ffbebe4b5a97464dd851910efc3002d96"
TRELLIS_CKPTS = (
    "ss_flow_img_dit_1_3B_64_bf16.safetensors",
    "slat_flow_img2shape_dit_1_3B_512_bf16.safetensors",
    "slat_flow_img2shape_dit_1_3B_1024_bf16.safetensors",
    "shape_dec_next_dc_f16c32_fp16.safetensors",
    "slat_flow_imgshape2tex_dit_1_3B_512_bf16.safetensors",
    "tex_dec_next_dc_f16c32_fp16.safetensors",
)


@dataclass(frozen=True)
class Geometry:
    provider: Provider
    repo: str
    commit: str
    gated_model: str
    reserve_gib: int
    validation: str = "experimental"

    def root(self, settings: Settings) -> Path:
        return settings.models / self.provider.value / self.commit

    def ready(self, settings: Settings) -> bool:
        root = self.root(settings)
        return (
            (root / "ready").is_file()
            and (root / ".venv/bin/python").is_file()
            and (root / "rembg/u2net.onnx").is_file()
            and all(self.checkpoint(root, *entry).is_file() for entry in self.weights())
        )

    @staticmethod
    def checkpoint(root: Path, model: str, revision: str, filename: str) -> Path:
        return (
            root
            / ".cache/huggingface/hub"
            / f"models--{model.replace('/', '--')}"
            / "snapshots"
            / revision
            / filename
        )

    def weights(self) -> list[tuple[str, str, str]]:
        if self.provider == Provider.SF3D:
            return [(SF3D_MODEL, SF3D_REVISION, "model.safetensors")]
        return [
            (DINO_MODEL, DINO_REVISION, "model.safetensors"),
            (TRELLIS_AUX_MODEL, TRELLIS_AUX_REVISION, "ckpts/ss_dec_conv3d_16l8_fp16.safetensors"),
            *((TRELLIS_MODEL, TRELLIS_REVISION, f"ckpts/{name}") for name in TRELLIS_CKPTS),
        ]

    def command(
        self,
        settings: Settings,
        images: Sequence[Path],
        output: Path,
        mode: Mode,
        seed: int,
    ) -> tuple[Sequence[str | Path], dict[str, str]]:
        root = self.root(settings)
        worker = Path(__file__).with_name("worker.py")
        env = os.environ.copy()
        env.pop("HF_TOKEN", None)
        env.pop("PYTHONPATH", None)
        env.update(
            HOME=str(root),
            HF_HOME=str(root / ".cache/huggingface"),
            HF_HUB_OFFLINE="1",
            U2NET_HOME=str(root / "rembg"),
            PYTHONUNBUFFERED="1",
        )
        if self.provider == Provider.SF3D:
            env["LOCAL3D_SF3D_WEIGHTS"] = str(self.checkpoint(root, *self.weights()[0]))
        return (
            [
                root / ".venv/bin/python",
                worker,
                "generate",
                self.provider.value,
                root,
                output,
                mode.value,
                str(seed),
                *images,
            ],
            env,
        )


GEOMETRY = {
    Provider.SF3D: Geometry(
        Provider.SF3D,
        "https://github.com/bahaehmimdi/stable-fast-3d-mlx.git",
        "ac426cda2f50f5b31ed3818670ee2d426decee22",
        SF3D_MODEL,
        8,
        validation="validated",
    ),
    Provider.TRELLIS: Geometry(
        Provider.TRELLIS,
        "https://github.com/lyonsno/trellis2mlx.git",
        "449bfbc011401fe03d684a01f0b1068bfe8f6ceb",
        DINO_MODEL,
        24,
    ),
}


def install_geometry(settings: Settings, provider: Provider) -> Path:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise Local3DError("MLX model installation requires macOS ARM64.")
    uv = shutil.which("uv")
    if not uv or not shutil.which("git"):
        raise Local3DError("Install uv and git before installing models.")
    spec = GEOMETRY[provider]
    root = spec.root(settings)
    log = root / "install.log"
    with file_lock(root / "install.lock") as lock_fd:
        if spec.ready(settings):
            return root
        if shutil.disk_usage(settings.home).free < spec.reserve_gib * 2**30:
            raise Local3DError(f"Allow at least {spec.reserve_gib} GiB free for {provider}.")
        (root / "ready").unlink(missing_ok=True)
        upstream = root / "upstream"
        upstream.mkdir(exist_ok=True)
        python = root / ".venv/bin/python"
        env = os.environ.copy()
        env.setdefault("UV_CACHE_DIR", str(settings.home / "uv-cache"))
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        commands = [
            ("Initialize pinned checkout", ["git", "-C", upstream, "init", "-q"]),
            (
                "Fetch pinned source",
                ["git", "-C", upstream, "fetch", "-q", "--depth=1", spec.repo, spec.commit],
            ),
            (
                "Check out pinned source",
                ["git", "-C", upstream, "checkout", "-q", "--detach", "FETCH_HEAD"],
            ),
            (
                "Create provider environment",
                [uv, "venv", "--allow-existing", "--python", "3.12", root / ".venv"],
            ),
        ]
        deps = ["mlx==0.32.4", "huggingface-hub>=0.32,<2"]
        if provider == Provider.SF3D:
            deps.extend(["numpy<3", "pillow<14", "rembg[cpu]==2.0.76", "trimesh<5"])
        else:
            deps.append(str(upstream))
        commands.extend(
            [
                (
                    "Install provider dependencies",
                    [uv, "pip", "install", "--python", python, *deps],
                ),
            ]
        )
        try:
            for name, args in commands:
                run_stage(name, args, log=log, lock_fd=lock_fd, timeout=settings.timeout, env=env)
            worker = Path(__file__).with_name("worker.py")
            with engine_lock(settings) as probe_fd:
                run_stage(
                    "Probe Metal and provider imports",
                    [python, worker, "probe", provider.value, root],
                    log=log,
                    lock_fd=probe_fd,
                    timeout=settings.timeout,
                    env=env,
                )
            run_stage(
                "Install official weights",
                [
                    python,
                    Path(__file__).with_name("worker.py"),
                    "install",
                    provider.value,
                    root,
                    *(value for weight in spec.weights() for value in weight),
                ],
                log=log,
                lock_fd=lock_fd,
                timeout=settings.timeout,
                env=env,
            )
        except Local3DError as exc:
            raise Local3DError(
                f"{exc} Install log: {log}. Gated weights: https://huggingface.co/{spec.gated_model}"
            ) from exc
        (root / "ready").touch()
    return root
