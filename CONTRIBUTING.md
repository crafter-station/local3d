# Contributing

Use Python 3.12 and uv. Install [Blender](https://www.blender.org/download/) to run the headless integration test.

```bash
uv sync --locked --all-extras
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Add provider runtime dependencies to the isolated provider installer. Keep the orchestration environment free of MLX, Torch and model weights. Pin upstream revisions and check the actual command or imports on Apple Silicon before enabling a new provider.

For changes that affect inference, record the device, macOS and Blender versions, provider and upstream revisions, input source, mode, output counts, stage times and measured memory. Use model and hardware availability labels that match the evidence.

Keep fixtures small and reproducible. Use temporary directories in tests. Leave weights, credentials, caches and generated meshes outside Git. Use English for issues, commits, code and documentation.
