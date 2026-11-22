---
name: local3d
description: Generate local GLB or BLEND assets from a reference image or prompt on Apple Silicon. Use for asset generation, mesh optimization, export, or a requested preview.
---

# Local3D

Use the installed `local3d` command or the configured Local3D MCP server. From a source checkout, use `uv run --project /absolute/path/to/local3d local3d` in place of `local3d`.

- For an image, pass its absolute path to `local3d generate --image /path/reference.png`. The fast mode selects Stable Fast 3D. Check `local3d models list` when provider availability is unclear.
- For text only, check the model inventory and run `generate --prompt` with MFLUX installed. If MFLUX is unavailable, ask for a reference image. TRELLIS.2 and the Draw Things adapters require experimental opt-in. Use `--provider sf3d` when requesting quality with Stable Fast 3D.
- Report the resulting local asset path and the requested metadata. For MCP, submit with `generate_asset` and poll `get_job` with the job ID. A queued or running job has no final asset yet.
- Use `optimize`, `export`, or `preview` only when requested. Pass file paths to tools and keep binary assets out of the conversation.
- If a provider fails, use the job error and local log. The `demo` provider generates a Blender primitive for setup verification.
