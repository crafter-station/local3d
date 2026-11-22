"""Run inside the isolated provider Python environment."""

import os
import runpy
import sys
from datetime import datetime
from pathlib import Path


def probe(provider: str, root: Path) -> None:
    import mlx.core as mx

    if not mx.metal.is_available():
        raise RuntimeError("MLX Metal is unavailable.")
    mx.eval(mx.arange(4) + 1)
    print(f"Metal available (MLX {mx.__version__})", flush=True)
    if provider == "sf3d":
        sys.path.insert(0, str(root / "upstream/mlx_port"))
        import finish_mesh_mlx  # noqa: F401
        import mlx_sf3d  # noqa: F401
    else:
        sys.path.insert(0, str(root / "upstream"))
        script = root / "upstream/generate.py"
        sys.argv = [str(script), "--help"]
        runpy.run_path(str(script), run_name="__main__")


def install(root: Path, weights: list[str]) -> None:
    from huggingface_hub import hf_hub_download

    cache = root / ".cache/huggingface/hub"
    os.environ["U2NET_HOME"] = str(root / "rembg")
    for model, rev, filename in zip(weights[::3], weights[1::3], weights[2::3], strict=True):
        print(f"{datetime.now().isoformat(timespec='seconds')} {model}/{filename}", flush=True)
        hf_hub_download(model, filename, revision=rev, cache_dir=cache)
    import rembg

    rembg.new_session("u2net", providers=["CPUExecutionProvider"])


def generate(provider: str, root: Path, image: Path, output: Path, mode: str, seed: str) -> None:
    import mlx.core as mx

    if not mx.metal.is_available():
        raise RuntimeError("MLX Metal is unavailable.")
    mx.set_cache_limit(512 * 2**20)
    # The MLX allocator limit does not bound total process or system memory.
    mx.set_memory_limit(15 * 2**30)
    mx.random.seed(int(seed))
    if provider == "sf3d":
        sys.path.insert(0, str(root / "upstream/mlx_port"))
        import finish_mesh_mlx
        from mlx_sf3d import MLXStableFast3D
        from PIL import Image

        weights = Path(os.environ["LOCAL3D_SF3D_WEIGHTS"])
        with Image.open(image) as reference:
            model = MLXStableFast3D(weights)
            codes = model.image_to_scene_codes(reference)
        del model
        mx.clear_cache()
        finish_mesh_mlx.WEIGHTS_PATH = weights
        finish_mesh_mlx.finish_mesh(
            codes, str(output), bake_resolution=512 if mode == "fast" else 1024
        )
    else:
        sys.path.insert(0, str(root / "upstream"))
        script = root / "upstream/generate.py"
        profile = (
            ["--no-cascade", "--steps", "8", "--target-faces", "100000", "--texture-size", "512"]
            if mode == "fast"
            else [
                "--resolution",
                "768",
                "--max-tokens",
                "12000",
                "--steps",
                "12",
                "--target-faces",
                "200000",
                "--texture-size",
                "1024",
            ]
        )
        sys.argv = [
            str(script),
            "--image",
            str(image),
            "--output",
            str(output),
            "--seed",
            seed,
            "--simplify-first",
            "--no-hole-fill",
            "--repair-exterior-surface",
            *profile,
        ]
        runpy.run_path(str(script), run_name="__main__")
    print(f"MLX peak allocator bytes: {mx.get_peak_memory()}", flush=True)


if __name__ == "__main__":
    action, provider, directory, *args = sys.argv[1:]
    root = Path(directory)
    if action == "probe":
        probe(provider, root)
    elif action == "install":
        install(root, args)
    elif action == "generate":
        image_path, output_path, mode, seed = args
        generate(provider, root, Path(image_path), Path(output_path), mode, seed)
    else:
        raise ValueError(f"Unknown action: {action}")
