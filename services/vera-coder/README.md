# vera-coder — local coding agent

A fully-local, offline coding agent: **opencode** on the client machine, backed by
**Qwen3-Coder-30B-A3B** served by MLX on an **Apple Silicon host**. Launched with one
command: `vera`.

## Why a separate host (not the chat GPU)

The GPU serving **Vera** (`qwen3-30b-a3b`) is saturated. A coder can't
co-reside, so hosting it there would evict Vera and cold-start her on every
coding session. An Apple Silicon machine with unified memory makes a good
on-demand worker, and Qwen3-Coder's **A3B** (3 B active params) is ideal for Apple
Silicon. So the coder lives on its own MLX host and **the chat server is never touched.**

## Pieces

| File | Runs on | Purpose |
|---|---|---|
| `vera-coder.sh` | Coder host (`~/.vera/vera-coder.sh`) | On-demand control of the MLX server (`ensure`/`start`/`stop`/`status`) on `:8084` |
| `vera` | Client (`~/.local/bin/vera`) | Wakes the coder server, then launches opencode on the local coder |
| `vera-stop` | Client (`~/.local/bin/vera-stop`) | Releases the coder host's ~17 GB when you're done coding |
| `opencode.json` | Client (`~/.config/opencode/opencode.json`) | Registers the `vera` OpenAI-compatible provider → coder endpoint |

## On-demand by design

`vera-coder.sh` is **not** a RunAtLoad/KeepAlive LaunchAgent (unlike
`vera-vision`). The coder host's unified memory is shared with bursty image-gen
(`qwen-image-8bit`, ~20 GB while generating), so the coder (~17 GB) is brought
up only while you're coding and released with `vera-stop` afterward.

## Model

`mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2` (~17 GB, DWQ 4-bit ≈
near-8-bit quality), in the coder host's HF cache. Served via
`python -m mlx_lm server` from the existing `~/venvs/vera` venv.

## Usage

```sh
vera                 # wake the coder + open the TUI on a fresh session
vera ~/some/project  # open opencode in a project dir
vera run "…"         # one-shot prompt
vera -c              # continue the last session
vera-stop            # release the coder host's memory when done
```

## Deploy

```sh
# coder-host side (an ssh alias for the coder host, e.g. `studio` below)
scp services/vera-coder/vera-coder.sh studio:~/.vera/vera-coder.sh
ssh studio 'chmod +x ~/.vera/vera-coder.sh'

# client side
cp services/vera-coder/vera        ~/.local/bin/vera        && chmod +x ~/.local/bin/vera
cp services/vera-coder/vera-stop   ~/.local/bin/vera-stop   && chmod +x ~/.local/bin/vera-stop
cp services/vera-coder/opencode.json ~/.config/opencode/opencode.json   # merge if one exists
```
