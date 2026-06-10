# vera-image

HTTP image-generation for an Apple Silicon host — wraps the saved 8-bit
Qwen-Image (`~/models/qwen-image-8bit`) via the mflux CLI. Used by the Pulse pipeline to
give each card a generated, on-vibe image. Image-gen is Pulse-only for now.

## Run

```bash
~/venvs/vera/bin/uvicorn app:app --host 0.0.0.0 --port 8083   # from this dir on the image host
```

`POST /generate {prompt, style?, width?, height?, steps?, seed?}` →
`{image_base64, dominant: "#rrggbb", width, height}`.

## Memory / co-residence

Qwen-Image 8-bit is ~34GB; the vision server (`vera-vision`, :8082) holds ~7GB. On a host without
enough unified memory for both, they can't co-reside. Before an image batch, pause vision and
restore after:

```bash
launchctl bootout    gui/$(id -u)/com.vera.vision     # free RAM
# … generate …
launchctl bootstrap  gui/$(id -u) ~/Library/LaunchAgents/com.vera.vision.plist
launchctl kickstart -k gui/$(id -u)/com.vera.vision
```

Subprocess-per-request reloads the model each call (~3–4 min); fine for the overnight Pulse run.
Warm-batch (load once, generate N) is a future optimization.
