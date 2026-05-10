# IP-Adapter workflow for character consistency

The reader app uses an IP-Adapter (image prompt adapter) workflow to keep
character faces consistent across generated scenes. This file isn't shipped
because the exact node graph depends on which IP-Adapter implementation you
have installed in ComfyUI.

## One-time setup

1. **Install IP-Adapter custom nodes** in ComfyUI. Common choices:
   - [`comfyui_IPAdapter_plus`](https://github.com/cubiq/ComfyUI_IPAdapter_plus) — the most flexible, supports FLUX via the `IPAdapterFluxLoader`
   - Or the FLUX-specific `ComfyUI_PuLID_Flux_ll` for face-strong consistency

2. **Build a working IP-Adapter workflow** in the ComfyUI web UI:
   - LoadCheckpoint → FLUX1-dev (or your preferred FLUX checkpoint)
   - LoadImage node → set `image` to a placeholder file (we'll template it)
   - IPAdapterFluxLoader → CLIP vision model + IP-Adapter weights
   - IPAdapterApply → connects model + reference image
   - CLIPTextEncode (positive) → text prompt
   - KSampler → cfg=1.0, steps=25, euler/simple
   - VAEDecode → SaveImage

3. **Test it works in ComfyUI directly** — you should be able to generate an
   image of a person using a face reference. Get it looking right.

4. **Export the workflow as JSON** ("Save (API Format)" — NOT the regular
   "Save"; the API format is what `generate_from_workflow` consumes).

5. **Drop the JSON into this directory** as `ipadapter.json` and edit it:
   - In the LoadImage node, replace `"image": "<your-file.png>"` with `"image": "{image}"`
   - In the positive CLIPTextEncode, replace the text with `"text": "{prompt}"`
   - In the KSampler, replace `"seed": <number>` with `"seed": "{seed}"`
   - Optionally: replace width/height with `"{width}"` / `"{height}"`

6. **Configure the path** in `emptyos.toml`:
   ```toml
   [plugins.comfyui]
   ipadapter_workflow = "plugins/comfyui/workflows/ipadapter.json"
   ```

7. **Restart the daemon.**

## How reader uses it

When you generate a scene, reader:

1. Derives a world card from the book (one-shot, cached)
2. Generates one canonical portrait per character via bare FLUX (one-shot per
   character, cached at `data/apps/reader/canon/<slug>/<name>.png`)
3. For each scene paragraph, detects which character appears earliest, uploads
   that character's canonical portrait to ComfyUI's `input/` folder, and runs
   the IP-Adapter workflow with the portrait as reference + the scene prompt
4. If the workflow isn't configured, OR no character is present, OR the upload
   fails — falls back to bare FLUX with no consistency. So it's safe to leave
   this file unset; you just lose face consistency.

## Inspecting

- `GET /reader/api/canon/<slug>` — list canonical portraits for a book
- `POST /reader/api/canon/<slug>/regen` — wipe and regenerate all portraits
- `GET /reader/api/scene-prompt/<slug>/<paragraph_idx>` — see whether a given
  scene used IP-Adapter and which character's portrait was the reference
