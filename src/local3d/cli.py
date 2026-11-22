import json
import os
import plistlib
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ValidationError

from .engine import Engine
from .inspection import doctor as diagnose
from .inspection import model_inventory
from .models import Format, GenerateRequest, Mode, Provider
from .providers.draw_things_cli import DEFAULT_MODEL, DrawThingsCLI
from .providers.geometry import GEOMETRY, install_geometry
from .providers.mflux import MFlux
from .settings import Local3DError, Settings
from .web_assets import install_viewer

app = typer.Typer(no_args_is_help=True, help="Local 3D assets for agents.")
models_app = typer.Typer(no_args_is_help=True)
jobs_app = typer.Typer(no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(jobs_app, name="jobs")


def json_output(value: object) -> None:
    if isinstance(value, BaseModel):
        typer.echo(value.model_dump_json(exclude_none=True))
    else:
        typer.echo(json.dumps(value, separators=(",", ":")))


def fail(error: Exception) -> None:
    if isinstance(error, ValidationError):
        detail = error.errors(include_input=False, include_url=False, include_context=False)[0]
        json_output({"error": detail["msg"]})
    else:
        json_output({"error": str(error)})
    raise typer.Exit(1)


@app.command()
def generate(
    prompt: Annotated[str | None, typer.Option("--prompt", "-p")] = None,
    image: Annotated[Path | None, typer.Option("--image", "-i")] = None,
    view: Annotated[
        list[Path] | None,
        typer.Option(help="Extra reference angle of the same object. TRELLIS.2 only, up to 3."),
    ] = None,
    mode: Mode = Mode.FAST,
    provider: Provider | None = None,
    format: Format = Format.GLB,
    seed: int = typer.Option(42, min=0, max=2**32 - 1),
    allow_experimental: bool = False,
) -> None:
    """Generate one asset and print a compact job result."""
    try:
        request = GenerateRequest(
            prompt=prompt,
            image=image,
            views=view or [],
            mode=mode,
            provider=provider,
            format=format,
            seed=seed,
            allow_experimental=allow_experimental,
        )
        result = Engine(Settings.load()).generate(request)
    except (ValidationError, Local3DError) as exc:
        fail(exc)
        return
    json_output(result)
    if result.status != "succeeded":
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Inspect Blender, hardware, disk and model availability."""
    json_output(diagnose(Settings.load()))


@models_app.command("list")
def models_list() -> None:
    """Show provider validation and local availability."""
    json_output(model_inventory(Settings.load()))


@models_app.command("install")
def models_install(
    provider: str,
    image_model: Annotated[
        str, typer.Option(help="FLUX model name or file ID for Draw Things.")
    ] = DEFAULT_MODEL,
    image_models_dir: Annotated[
        Path | None, typer.Option(help="Optional Draw Things weights directory.")
    ] = None,
) -> None:
    """Install a pinned provider and verify its official runtime."""
    settings = Settings.load()
    try:
        if provider == "mflux":
            path = MFlux(settings).install()
        elif provider == "draw-things":
            path = DrawThingsCLI(settings).install(image_model, image_models_dir)
        elif provider in GEOMETRY:
            path = install_geometry(settings, Provider(provider))
        else:
            raise Local3DError("Choose sf3d, trellis2, mflux or draw-things.")
    except Local3DError as exc:
        fail(exc)
        return
    json_output({"provider": provider, "path": str(path)})


@jobs_app.command("list")
def jobs_list(limit: int = typer.Option(10, min=1, max=100)) -> None:
    """List recent jobs across CLI, REST and MCP."""
    jobs = Engine(Settings.load()).jobs.recent(limit)
    json_output([job.model_dump(mode="json", exclude_none=True) for job in jobs])


@app.command()
def optimize(
    source: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    ratio: float = typer.Option(0.5, min=0.01, max=1.0),
) -> None:
    """Decimate a static mesh and save a new GLB or BLEND."""
    try:
        json_output(Engine(Settings.load()).process("optimize", source, output, ratio=ratio))
    except Local3DError as exc:
        fail(exc)


@app.command()
def preview(
    source: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    size: int = typer.Option(512, min=128, max=1024),
) -> None:
    """Render a PNG of a GLB or BLEND using headless Blender."""
    try:
        json_output(Engine(Settings.load()).process("preview", source, output, preview_size=size))
    except Local3DError as exc:
        fail(exc)


@app.command()
def export(source: Path, output: Annotated[Path, typer.Option("--output", "-o")]) -> None:
    """Convert a GLB or BLEND to the format in the output extension."""
    try:
        json_output(Engine(Settings.load()).process("export", source, output))
    except Local3DError as exc:
        fail(exc)


@app.command()
def serve(port: int = typer.Option(8765, min=1024, max=65535)) -> None:
    """Run the REST API on 127.0.0.1."""
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(Settings.load()), host="127.0.0.1", port=port, access_log=False)


@app.command()
def mcp() -> None:
    """Run the optional FastMCP server over stdio."""
    try:
        from .mcp_server import create_server
    except ModuleNotFoundError as exc:
        if exc.name != "fastmcp":
            raise
        typer.echo("Install the MCP extra with uv sync --extra mcp.", err=True)
        raise typer.Exit(1) from exc
    create_server(Settings.load()).run(transport="stdio", show_banner=False)


@app.command()
def setup(
    launchd: bool = typer.Option(False, help="Write a user LaunchAgent plist."),
    viewer: bool = typer.Option(False, help="Install the optional offline GLB viewer."),
    port: int = typer.Option(8765, min=1024, max=65535),
) -> None:
    """Initialize local state and optionally prepare a launchd service."""
    settings = Settings.load()
    Engine(settings)
    response = {"home": str(settings.home)}
    if viewer:
        try:
            response["viewer"] = str(install_viewer(settings))
        except (Local3DError, OSError) as exc:
            fail(exc)
            return
    if launchd:
        executable = shutil.which("local3d")
        if sys.platform != "darwin" or not executable:
            fail(Local3DError("Run this from the installed macOS local3d CLI."))
            return
        label = "com.crafterstation.local3d"
        target = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            fail(Local3DError(f"LaunchAgent already exists: {target}"))
            return
        env = {
            key: value
            for key, value in os.environ.items()
            if key.startswith("LOCAL3D_DRAW_THINGS_")
        }
        env["LOCAL3D_HOME"] = str(settings.home)
        if settings.blender:
            env["LOCAL3D_BLENDER"] = str(settings.blender)
        with target.open("xb") as file:
            plistlib.dump(
                {
                    "Label": label,
                    "ProgramArguments": [executable, "serve", "--port", str(port)],
                    "RunAtLoad": True,
                    "EnvironmentVariables": env,
                    "StandardOutPath": str(settings.home / "serve.log"),
                    "StandardErrorPath": str(settings.home / "serve.log"),
                },
                file,
            )
        response["launch_agent"] = str(target)
    json_output(response)
