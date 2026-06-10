# vera-vision

Vision serving for Vera — **Qwen3-VL-8B (4-bit) via `mlx-vlm`** on an Apple Silicon host,
OpenAI-compatible at `http://<vision host>:8082/v1`. Vera reaches it through the
`see_image` OWUI tool (see `../owui-tools/see_image.py`), which routes an attached image to this
endpoint and returns the description — no manual model switching.

## Run

Served from the `~/venvs/vera` venv (Python 3.12, `mlx-vlm`):

```bash
~/venvs/vera/bin/python -m mlx_vlm.server --host 0.0.0.0 --port 8082 \
  --model mlx-community/Qwen3-VL-8B-Instruct-4bit
```

## Durable (launchd, on the vision host)

```bash
scripts/install-launchd.sh services/vera-vision/vera-vision.plist.template
launchctl kickstart -k gui/$(id -u)/com.vera.vision
```

**Gotcha:** load via `bootstrap gui/$(id -u)` — `launchctl load` over an SSH session loads into the
SSH domain and the agent never actually runs (empty logs, nothing on :8082).

## Verify

```bash
curl -s http://localhost:8082/v1/models        # lists Qwen3-VL
# vision smoke test: POST /v1/chat/completions with an image_url data URL (OpenAI multimodal shape)
```

Model: ~5.4GB on disk, ~6.7GB peak RAM, ~75 tok/s on the M4 Max.
