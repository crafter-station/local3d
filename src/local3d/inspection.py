import platform
import shutil
import subprocess

from .models import Provider
from .providers.draw_things import DrawThings
from .providers.draw_things_cli import DrawThingsCLI
from .providers.geometry import GEOMETRY
from .providers.mflux import MFlux
from .settings import Local3DError, Settings


def model_inventory(settings: Settings) -> list[dict]:
    inventory = [
        {
            "id": "demo",
            "validation": "validated",
            "available": settings.blender is not None,
            "kind": "procedural",
        },
        *[
            {
                "id": spec.provider.value,
                "validation": spec.validation,
                "available": spec.ready(settings),
                "kind": "image-to-3d",
                "reserve_gib": spec.reserve_gib,
            }
            for spec in GEOMETRY.values()
        ],
    ]
    inventory.append(
        {
            "id": "mflux",
            "validation": "validated",
            "available": MFlux(settings).ready(),
            "kind": "text-to-image",
            "backend": "mlx",
            "model": "FLUX.2 Klein 4B",
        }
    )
    profile = DrawThingsCLI(settings).profile()
    if profile is not None:
        return inventory + [
            {
                "id": "draw-things",
                "validation": "experimental",
                "available": True,
                "kind": "text-to-image",
                "backend": "cli",
                "model": profile.model,
            }
        ]

    try:
        draw = DrawThings()
        options = draw.options()
        draw.release_command()
        model = draw.model
        available = bool(model and model.lower().startswith("flux_") and options)
    except Local3DError:
        available = False
    inventory.append(
        {
            "id": "draw-things",
            "validation": "experimental",
            "available": available,
            "kind": "text-to-image",
        }
    )
    return inventory


def doctor(settings: Settings) -> dict:
    machine = platform.machine()
    os_name = platform.system()
    blender = settings.blender
    version = None
    if blender is not None:
        try:
            result = subprocess.run(
                [blender, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            version = result.stdout.splitlines()[0]
        except (subprocess.SubprocessError, OSError, IndexError):
            version = None
    settings.home.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {
        "os": os_name,
        "arch": machine,
        "apple_silicon": os_name == "Darwin" and machine == "arm64",
        "home": str(settings.home),
        "disk_free_bytes": shutil.disk_usage(settings.home).free,
        "blender": {"path": str(blender), "version": version} if blender else None,
        "uv": shutil.which("uv"),
        "models": model_inventory(settings),
        "default_provider": Provider.SF3D.value,
    }
