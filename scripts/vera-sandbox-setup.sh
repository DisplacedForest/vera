#!/bin/bash
# [EXAMPLE] Stand up the hardened code-interpreter sandbox. An isolated Jupyter kernel that
# OWUI's code interpreter executes server-side, reachable ONLY by OWUI, with no host/LAN/
# internet egress. Run on the docker host:  vera-sandbox-setup.sh <jupyter-token>
# Overrides: VERA_SANDBOX_NET (network name), VERA_SANDBOX_IMAGE (kernel image),
# OWUI_CONTAINER (your Open WebUI container's name).
set -euo pipefail

NET="${VERA_SANDBOX_NET:-vera-sandbox-net}"
NAME=vera-sandbox
IMG="${VERA_SANDBOX_IMAGE:-quay.io/jupyter/scipy-notebook:latest}"
OWUI_CONTAINER="${OWUI_CONTAINER:-open-webui}"
TOKEN="${1:?usage: vera-sandbox-setup.sh <jupyter-token>}"

# 1. Internal network — containers on it have NO route to the internet, host, or LAN.
docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET"

# 2. Hardened Jupyter: non-root image (jovyan), no host mounts, resource-capped,
#    no privilege escalation. State is ephemeral (recreate to reset).
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
  --network "$NET" \
  -e JUPYTER_TOKEN="$TOKEN" \
  --memory 2g --cpus 2 --pids-limit 256 \
  --security-opt no-new-privileges \
  "$IMG" >/dev/null

# 3. Let OWUI reach it by name (OWUI keeps its bridge network and gains this internal one).
docker network connect "$NET" "$OWUI_CONTAINER" 2>/dev/null || true

echo "sandbox '$NAME' up on internal net '$NET'; '$OWUI_CONTAINER' connected. Reachable at http://$NAME:8888"
