# Setting up Vera

This is the end-to-end path from nothing to a working installation: backend, chat, native app, then the optional layers — integrations, veins, and satellite services. Each stage works without the stages after it, and the system reports what is and isn't configured.

**The stack** (each component is a URL in config; topology is up to you):

| Piece | What it is | Required? |
|---|---|---|
| An OpenAI-compatible LLM server | llama.cpp / llama-swap / vLLM / Ollama / a hosted API — anything serving `/v1` | Yes |
| [Open WebUI](https://github.com/open-webui/open-webui) | Conversations, memory, tool execution | Yes |
| **vera-api** (this repo) | One FastAPI container holding every server-side capability | Yes |
| **Vera.app** (this repo) | The native macOS client | Recommended |
| Integrations (Home Assistant, Grocy, Mealie, Overseerr, Unraid, SearXNG) | Each unlocks a capability | No |
| Satellite services (voice, image, vision, coder) | Reference implementations of documented HTTP contracts | No |

## 1. Prerequisites

- **Docker** (with compose) on any Linux/macOS host for vera-api and Open WebUI.
- **An LLM endpoint** — any OpenAI-compatible `/v1` server with a capable instruct model. An existing Open WebUI endpoint can be shared.
- **Open WebUI** running and reachable from wherever vera-api will run, with an account created and the LLM endpoint connected.

## 2. vera-api

```sh
git clone https://github.com/DisplacedForest/vera.git && cd vera
cp .env.example .env     # fill in what you run — everything unset degrades gracefully
docker compose up -d     # pulls the released ghcr.io/displacedforest/vera-api image
```

Compose pulls the released image by default. To run from source instead, uncomment `build: services/vera-api` in `docker-compose.yml` and use `docker compose up -d --build`.

Then read the **config report** — vera-api prints exactly what is wired at startup, and it is the first thing to check when something is off:

```sh
curl localhost:8089/health
docker compose logs vera-api | head -60
```

### The `.env` walkthrough

`.env.example` is fully commented and is the authoritative reference; every variable the stack reads is in it (a test enforces this). The variables that matter first:

| Block | Variables | What it unlocks |
|---|---|---|
| Core LLM | `VERA_BASE`, `VERA_MODEL` | Everything generated: Pulse briefings, card text, judges |
| Open WebUI | `OWUI_BASE`, `OWUI_KEY`, `VERA_DEFAULT_USER` | Memory, promoted cards, self-authored skills |
| Web search | `SEARXNG_BASE` (+ optional `PLAYWRIGHT_WS`) | Research, Pulse sourcing, signals news |
| Identity | `VERA_OWNER_NAME`, `HOME_LOCATION_NAME`, `HOME_TZ`, `WEATHER_LAT`/`LON`, `TEMPERATURE_UNIT` | Personalization, schedules in your timezone, weather/signals anchoring |
| Dream/coder | `DREAM_BASE`, `DREAM_MODEL`, `DREAM_TOOL_PROTOCOL` | Nightly knowledge consolidation + fact verification |
| Audit hooks | `AUDIT_WAKE_URL`, `AUDIT_RELEASE_URL` | Cross-model claim audits on every Pulse run when the audit model is served on demand (POSTed before/after the batched end-of-run audit; unset = no hook calls) |
| Image gen | `VERA_IMAGE_BASE`, `IMAGE_PROTOCOL` | Generated cover art on Pulse cards |

Two conventions:

- **Endpoints are `*_BASE`, credentials are `*_KEY`.** Older names still work; the config report flags them with their replacement.
- **Unset means off, visibly.** A capability without its endpoint reports itself as not configured — it never fakes output and never affects other capabilities.

Integrations (Home Assistant and the rest) can be set in `.env` for headless installs; the app's integration store in step 4 is the recommended path.

## 3. Wire Open WebUI

Vera attaches to Open WebUI as a set of tools (model-invokable capabilities) and functions (every-turn pipeline filters):

1. **Tools** — in Open WebUI: Workspace → Tools → create, then paste each file from `services/owui-tools/` you want (start with `vera_memory.py` and `deep_research.py`; add `kitchen.py`, `media_request.py`, `home_knowledge.py`, `propose_action.py`, `see_image.py`, `self_author.py` as you enable their backends). In each tool's valve settings, set `vera_api_url` to your vera-api base.
2. **Functions** — Admin → Functions → create, paste from `services/owui-functions/` (the memory filter; `vision_autosee.py` if you run a vision endpoint), enable them.
3. **The model** — give your Vera model the tools you imported (model settings → tools) so chat can invoke them.

**One Open WebUI caveat:** Open WebUI auto-attaches a model's tools and features only on its own Socket.IO chat pipeline. Raw `POST /api/chat/completions` calls do **not** inherit them — a client must send `tool_ids` and `features` explicitly. The Mac app does this; keep it in mind if you build your own client.

If you install the Mac app, its integration store performs the per-integration OWUI wiring (attaching kitchen/media tools when you connect Grocy or Overseerr, etc.) automatically — the manual steps above are only needed once for the base tools.

## 4. The Mac app

**From a release** (macOS 14+): download `Vera.app.zip` from the [latest release](https://github.com/DisplacedForest/vera/releases/latest), unzip, and drag `Vera.app` to Applications. The app is ad-hoc signed (no notarization), so macOS quarantines the first launch — right-click → **Open** once, and it runs normally from then on. The app checks Releases and can update itself in place.

**From source** (Swift toolchain):

```sh
cd apps/vera-mac
swift build -c release
scripts/deploy.sh    # packages Vera.app, ad-hoc signs it, installs it to /Applications
```

First launch runs **onboarding**: your Open WebUI URL + account, your model, your vera-api URL — then a skippable **Veins** page. The two opt-in surfaces, both in the sidebar afterward:

- **Plugins** — the integration store. Each card is one integration: enter URL + key, **Test**, **Save & Enable**. The app writes vera-api's config and performs the OWUI wiring in the same step. Experimental features (whole-house event modeling, media curation) sit behind their parent integration with an explicit consent sheet — off until consented.
- **Veins** — Pulse's ambient monitors (System, Weather, Signals, Media). **None are enabled by default.** Enable the ones you want, point them at your services, and scope each one: the Signals vein can watch only financial stress, the System vein can monitor only Home Assistant — every vein carries its own options.

Both are UI over vera-api's API — headless deployments can do everything with `curl`.

## 5. Integrations, one by one

Each integration unlocks its capability when its test passes; each degrades to "off" when absent. All are configurable from the integration store or `.env`:

| Integration | Unlocks | Notes |
|---|---|---|
| Home Assistant | Live home state in chat and Pulse, confirm-gated device actuation, the System vein's HA sources | Use an IP for the URL, not `.local` — containers can't resolve mDNS. Long-lived access token. |
| Grocy | Kitchen inventory + expiry awareness, shopping list | Pairs with Mealie: recipe suggestions from expiring inventory unlock when both are on |
| Mealie | Recipe import, browse, classification | See Grocy pairing |
| Overseerr | Media requests from chat, availability checks; the Media vein's weekly curation digest (experimental, consent-gated) | |
| Unraid | Confirm-gated container updates, host actuation, update digests | Official Unraid API (GraphQL) with an API key |
| SearXNG | Web search for chat, research, Pulse, signals | Strongly recommended; run it next to vera-api |

## 6. Satellite services (all optional)

These are **documented HTTP contracts** with reference implementations in this repo. The references are MLX-based (Apple Silicon), but anything that implements the contract fills the slot:

| Slot | Contract | Reference |
|---|---|---|
| Image gen | OpenAI Images API: `POST {base}/v1/images/generations` | `services/vera-image` — serves the standard contract out of the box; `IMAGE_PROTOCOL=vera` adds deterministic seeds + the vision pause/resume extension |
| Vision | OpenAI chat completions with `image_url` content parts | Any MLX/vLLM-served VLM; see `services/vera-vision` for the launchd template |
| Dream/coder | OpenAI `/v1` with tool calling (`DREAM_TOOL_PROTOCOL=mlx` for servers that don't emit `tool_calls`) | `services/vera-coder` |
| Voice | Wyoming protocol (ASR + TTS) plus a small batch HTTP API | `services/vera-voice`; install with `scripts/deploy-vera-voice.sh` |

Every satellite env var (models, ports, voices, paths) is documented in `.env.example`'s
companion-services section; voice installs with one command (`scripts/deploy-vera-voice.sh` —
runtime copy-out, both venvs, launchd agents), and the image/vision services ship launchd
templates installed via `scripts/install-launchd.sh`.

## 7. The scheduler

vera-api runs all recurring work itself — no external cron. Defaults:

| Job | Default | Gated on |
|---|---|---|
| Pulse briefing | daily 5:00 | — (core; needs your LLM + Open WebUI) |
| Weather check | every 6h | Weather vein |
| Signals check | 6:00 + 18:00 | Signals vein |
| Service health probe | every 15 min | System vein |
| Stack updates check | daily 7:30 | System vein |
| Media curation digest | Sundays 9:00 | Media vein + Overseerr consent |
| Episodic memory groom | daily 4:00 | — (core; needs Open WebUI) |
| Home modeling (3 nightly jobs) | 2:00–3:30 | Home Assistant's home-modeling consent |
| Heartbeat tick | every 20 min | `HEARTBEAT_ENABLED` kill switch |

A gated job never fires while its gate is closed — on a fresh install, nothing is monitored until its vein is enabled or its feature is consented to. Edit any schedule in the app's **Agentic** tab (live, no restart) or pin it with `SCHEDULE_<JOB>` / `SCHEDULE_<JOB>_ENABLED` env overrides. A gated job reports *why* it is gated instead of running.

Everything autonomous is auditable in one place: `GET /agentic/activity?hours=24` returns a normalized, newest-first feed merging heartbeat outcomes, scheduled job runs, the action audit log, and Open WebUI automation runs (when OWUI's automations API is reachable). The app renders it as the **Activity** section of the Agentic tab, refreshed every 30 seconds. A missing backing store or unreachable OWUI contributes nothing instead of erroring, so the feed works on any subset of the stack.

The heartbeat tick also services Vera's **journal** — the self-authored document of standing commitments she checks, updates, and retires on her own (stored at `VERA_JOURNAL_PATH`, default `/data/journal/JOURNAL.md`; rendered read-only in the app's Journal view and at `GET /journal`). With no journal file and nothing to commit to, the step is a no-op. Chat steers the journal through the `self_author.py` tool's `read_journal` / `journal_commit` functions, so install that tool if you want "keep an eye on X" and "what are you watching" to work in conversation.

With the Mealie integration enabled, the heartbeat can also **curate recipes autonomously**: when Vera finds a recipe genuinely worth keeping she imports it herself through `POST /actions/auto` — no confirmation card. This free lane accepts only verbs explicitly enrolled as `autonomous` in the action registry (today: `kitchen.mealie_import`, which the registry permits because it is low-risk and reversible by deleting the recipe). Imports are capped at 2 per tick and 3 per rolling day, duplicate URLs are skipped, every execution lands in the action audit log with `auto=true`, and each import posts a System card with the recipe link. Without Mealie configured the lane simply never produces anything; `HEARTBEAT_ENABLED=false` stops it along with the rest of the tick.

## 8. Verifying the install

1. `curl localhost:8089/health` — vera-api is up.
2. The startup config report lists every endpoint you configured (and flags anything deprecated or missing).
3. Chat in the app — ask something that needs a tool (e.g. a web search) and confirm the tool fires.
4. Trigger a Pulse run from the app (or `POST /pulse/run`) and watch cards arrive.
5. Enable a vein and confirm its chip appears; its producer job in Agentic shows the next run time.

When something doesn't work, check the config report and `docker compose logs vera-api` first.
