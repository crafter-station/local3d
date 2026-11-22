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


def clean_reference(image: Path, output: Path) -> Path:
    """Segment the object and return an RGBA reference with a hard, defringed mask.

    SF3D composites the cutout over gray, so soft rembg edges carry background color into
    the baked albedo and small stray blobs become floating geometry. Only the main object
    and parts at least 5% of its area are kept. Interior holes are left intact.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    with Image.open(image) as source:
        rgba = source.convert("RGBA")
    if np.asarray(rgba)[..., 3].min() == 255:
        import rembg

        session = rembg.new_session("u2net", providers=["CPUExecutionProvider"])
        rgba = rembg.remove(rgba.convert("RGB"), session=session, post_process_mask=True)
        rgba = rgba.convert("RGBA")
    pixels = np.array(rgba)
    mask = pixels[..., 3] > 127
    labels, count = ndimage.label(mask)
    if count == 0:
        return image
    if count > 1:
        sizes = ndimage.sum(mask, labels, range(1, count + 1))
        mask = np.isin(labels, 1 + np.flatnonzero(sizes >= 0.05 * sizes.max()))
    mask = ndimage.binary_erosion(mask, iterations=max(1, round(min(mask.shape) / 512)))
    if not mask.any():
        return image
    pixels[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
    pixels[~mask, :3] = 0
    Image.fromarray(pixels, "RGBA").save(output)
    return output


def lossless_textures() -> None:
    """Make trimesh embed PNG textures. Upstream tags them JPEG, which trimesh saves at
    quality 75 and leaves block artifacts in both the albedo and the normal map."""
    import trimesh

    material = trimesh.visual.material.PBRMaterial
    original = material.__init__

    def init(self, *args, **kwargs):
        for key in ("baseColorTexture", "normalTexture"):
            if kwargs.get(key) is not None:
                kwargs[key].format = None
        original(self, *args, **kwargs)

    material.__init__ = init


def generate(
    provider: str, root: Path, output: Path, mode: str, seed: str, images: list[Path]
) -> None:
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
        if len(images) != 1:
            raise ValueError("SF3D conditions on exactly one reference image.")
        cutout = clean_reference(images[0], output.with_name("reference-cutout.png"))
        lossless_textures()
        with Image.open(cutout) as reference:
            model = MLXStableFast3D(weights)
            codes = model.image_to_scene_codes(reference)
        del model
        mx.clear_cache()
        finish_mesh_mlx.WEIGHTS_PATH = weights
        # 512 px was too coarse once the object fills only part of the atlas. 1024 matches
        # the measured quality profile and baking stays a small share of runtime.
        finish_mesh_mlx.finish_mesh(codes, str(output), bake_resolution=1024)
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
            *(str(path) for path in images),
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
        output_path, mode, seed, *image_paths = args
        generate(provider, root, Path(output_path), mode, seed, [Path(p) for p in image_paths])
    else:
        raise ValueError(f"Unknown action: {action}")
