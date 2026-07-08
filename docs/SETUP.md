# Setting up Vera

This is the end-to-end path from nothing to a working installation: backend, chat, native app, then the optional layers — integrations, veins, and satellite services. Each stage works without the stages after it, and the system reports what is and isn't configured.

**The stack** (each component is a URL in config; topology is up to you):

| Piece | What it is | Required? |
|---|---|---|
| An OpenAI-compatible LLM server | llama.cpp / llama-swap / vLLM / Ollama / a hosted API — anything serving `/v1` | Yes |
| [Open WebUI](https://github.com/open-webui/open-webui) | Conversations, memory, tool execution | Yes |
| **Vera.app** (this repo) | The native macOS client; complete pointed at Open WebUI alone | Recommended |
| **vera-api** (this repo) | One FastAPI container that lights up the ambient and experimental surfaces (Pulse veins, signals, weather, kitchen, research, heartbeat, scheduler, actions) | Optional |
| Integrations (Home Assistant, Grocy, Mealie, Overseerr, Unraid, SearXNG, Reddit, Embeddings) | Each unlocks a capability | No |
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

### Run the engine without Docker (macOS arm64)

Every release ships a packaged engine binary alongside the image. Download `vera-api-macos-arm64.zip` and its `.sha256` from the GitHub Release, verify with `shasum -a 256 -c vera-api-macos-arm64.zip.sha256`, unzip, and run `./vera-api/vera-api`. It serves on `127.0.0.1:8089` and keeps its data in `~/.vera/data`; `VERA_DATA_DIR`, `VERA_BIND`, and `VERA_PORT` override those defaults. Configuration is the same `.env` surface either way (export the variables or launch through a wrapper that sets them).

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
| Scout sources | `GITHUB_API_BASE`, `ARXIV_BASE` (default to public endpoints); Reddit is a plugin (`REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`, register a script app at reddit.com/prefs/apps) | Pulse candidate search across github/papers/reddit; news and local ride `SEARXNG_BASE`, weather rides `WEATHER_LAT`/`LON` |

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

**From a release** (macOS 26 Tahoe or later; earlier macOS can run releases up to 0.2.x): download `Vera.app.zip` from the [latest release](https://github.com/DisplacedForest/vera/releases/latest), unzip, and drag `Vera.app` to Applications. The app is ad-hoc signed (no notarization), so macOS quarantines the first launch — right-click → **Open** once, and it runs normally from then on. The app checks Releases and can update itself in place.

**From source** (Swift toolchain):

```sh
cd apps/vera-mac
swift build -c release
scripts/deploy.sh    # packages Vera.app, ad-hoc signs it, installs it to /Applications
```

First launch runs **onboarding**: your Open WebUI URL + account, your model, your vera-api URL — then a skippable **Veins** page. The two opt-in surfaces afterward, each living inside the feature it drives:

- **Plugins** — the integration store, a tab in **Settings** (⌘,). Each card is one integration: enter URL + key, **Test**, **Save & Enable**. The app writes vera-api's config and performs the OWUI wiring in the same step. Experimental features (whole-house event modeling, media curation) sit behind their parent integration with an explicit consent sheet — off until consented.
- **Veins** — Pulse's ambient monitors (System, Weather, Signals, Media), opened from the **Veins** button in the Pulse header. **None are enabled by default.** Enable the ones you want, point them at your services, and scope each one: the Signals vein can watch only financial stress, the System vein can monitor only Home Assistant — every vein carries its own options. Every vein is a schema-validated JSON definition: the built-ins ship as files in the service image, and custom definitions live one file per vein at `/data/veins.d/<kind>.json`, managed through the API (`GET /pulse/veins/schema` serves the contract, `POST /pulse/veins` creates). A custom vein carries a `pipeline` of blocks (`web_search`, `http_fetch`, `ha_state`, `trip_band`, `llm_judge`, `llm_compose`) plus a cron `schedule`, and the vein engine runs it: dropping a valid definition file in place is all it takes to have a running ambient monitor. Each pipeline vein registers a scheduler job as `vein_<kind>`, `POST /pulse/veins/{kind}/run` fires it on demand (`dry_run=true` returns the would-be cards without posting), and the engine owns quiet discipline: one card per distinct situation (updated, never stacked), a seen-memory so watchers don't re-alert on a standing story (`VEIN_SEEN_DECAY_DAYS`, default 7), and a schedule floor for LLM-bearing pipelines (`VEIN_LLM_FLOOR_MINUTES`, default 30; engine state in `VEIN_ENGINE_DB_PATH`). Per-vein enable/options state lives separately in `/data/veins.json`. You don't have to write definitions by hand: `POST /pulse/veins/builder/turn` runs the authoring conversation against whatever model `VERA_BASE`/`VERA_MODEL` name (describe what you want watched; each turn returns prose plus a schema-validated draft), and `POST /pulse/veins/builder/dry_run` executes an unsaved draft once and returns what would have posted, persisting nothing. With no model configured both endpoints report disabled cleanly.

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
| Embeddings | Pulse novelty ranking and the duplicate-finding floor, profile-graph node embeddings for dedup-merge | Any OpenAI-compatible `POST {base}/v1/embeddings` endpoint; the model id is only needed for multi-model servers (llama-swap, hosted APIs). The LLM server can serve this too |
| Apple Reminders | Reminders lists read/write from chat, shared lists included | URL of the `vera-reminders` bridge (see satellite services below) |
| Code sandbox | The `code_interpreter` tool: the model runs Python in an isolated kernel for tasks no named tool covers | Stand one up with `scripts/vera-sandbox-setup.sh` (egress-free, resource-capped); point `VERA_SANDBOX_URL`/`VERA_SANDBOX_TOKEN` at it |

## 6. Satellite services (all optional)

These are **documented HTTP contracts** with reference implementations in this repo. The references are MLX-based (Apple Silicon), but anything that implements the contract fills the slot:

| Slot | Contract | Reference |
|---|---|---|
| Image gen | OpenAI Images API: `POST {base}/v1/images/generations` | `services/vera-image` — serves the standard contract out of the box; `IMAGE_PROTOCOL=vera` adds deterministic seeds + the vision pause/resume extension |
| Vision | OpenAI chat completions with `image_url` content parts | Any MLX/vLLM-served VLM; see `services/vera-vision` for the launchd template |
| Dream/coder | OpenAI `/v1` with tool calling (`DREAM_TOOL_PROTOCOL=hermes` for servers that pass model text through untouched) | `services/vera-coder` |
| Voice | Wyoming protocol (ASR + TTS) plus a small batch HTTP API | `services/vera-voice`; install with `scripts/deploy-vera-voice.sh` |
| Reminders | Small HTTP API over EventKit: `/health`, `/lists`, `/reminders` | `services/vera-reminders`; install with `scripts/deploy-vera-reminders.sh` |

Reminders reaches EventKit, Apple's only supported door into Reminders, which must run
on a **Mac signed into the iCloud account whose lists Vera should see** — it sees shared
lists, so items added by Siri on any household device appear and Vera's writes sync back
to everyone.

**If you run the Vera Mac app, you do not need this service.** The app hosts the bridge
itself: open Settings, Plugins, and toggle **Apple Reminders** on. That grants the
permission (a native prompt), points vera-api at the app, and installs the Open WebUI
tool in one step. It serves while the app is open, which is all reminders need — Vera
only touches them on an explicit chat ask. `services/vera-reminders` remains as the
headless reference for deployments with no Mac app: run `scripts/deploy-vera-reminders.sh`
on a signed-in Mac, approve the one-time prompt, then enable the Apple Reminders
integration with the bridge URL and install `services/owui-tools/reminders.py` as an
Open WebUI tool.

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
| Pipeline vein runs (`vein_<kind>`) | each definition's `schedule` | that vein's enable state |

Pipeline veins register their jobs dynamically — one per definition, appearing and disappearing with the definition file — and the standard override convention applies (`SCHEDULE_VEIN_<KIND>`, `SCHEDULE_VEIN_<KIND>_ENABLED`).

With home-modeling consent on, the capture stream also tees every numeric `sensor.*` reading into a dedicated series store (`SERIES_DB_PATH`, default `/data/series.db`) retained for `SERIES_RETAIN_DAYS` (default 365) — the substrate household forecasting reads. Inspect it at `GET /home/series` (entities + counts) and `GET /home/series/{entity_id}` (raw points); on first init it backfills from the existing event log. With capture off it stays empty and both endpoints return empty cleanly.

A gated job never fires while its gate is closed — on a fresh install, nothing is monitored until its vein is enabled or its feature is consented to. The app's **Agentic** tab renders all of this as a canvas: every flow as a node connected to the surface it feeds (the Pulse feed, the veins, memory, actions), with live status, plain-English schedules, and drill-in pipelines for the flows that have stages. The topology comes from `GET /agentic/graph`, a server-declared manifest, so a new capability appears on the canvas without an app update. Click a node to run it now, toggle it, or edit its schedule (live, no restart), or pin schedules with `SCHEDULE_<JOB>` / `SCHEDULE_<JOB>_ENABLED` env overrides. A gated job reports *why* it is gated instead of running.

Everything autonomous is auditable in one place: `GET /agentic/activity?hours=24` returns a normalized, newest-first feed merging heartbeat outcomes, scheduled job runs, the action audit log, and Open WebUI automation runs (when OWUI's automations API is reachable). The app renders it as the **Activity** pane of the Agentic tab, refreshed every 30 seconds, and recent events animate along their edges on the canvas. A missing backing store or unreachable OWUI contributes nothing instead of erroring, so the feed works on any subset of the stack.

The heartbeat tick also resolves Vera's **journal** watches: a due watch node whose resolve condition and date are both met transitions to resolved deterministically (no model recheck, so a watch can never become immortal). The journal itself is a view over the Profile Graph's watch and project nodes, rendered read-only in the app's Journal view and at `GET /journal`; the legacy self-authored markdown at `VERA_JOURNAL_PATH` (default `/data/journal/JOURNAL.md`) remains only as a fallback until the graph holds nodes. With no watch nodes and no fallback file, the step and the view are empty. Chat steers the journal through the `self_author.py` tool's `read_journal` / `journal_commit` functions (a commit lands a watch node), so install that tool if you want "keep an eye on X" and "what are you watching" to work in conversation.

With the Mealie integration enabled, the heartbeat can also **curate recipes autonomously**: when Vera finds a recipe genuinely worth keeping she imports it herself through `POST /actions/auto` — no confirmation card. This free lane accepts only verbs explicitly enrolled as `autonomous` in the action registry (today: `kitchen.mealie_import`, which the registry permits because it is low-risk and reversible by deleting the recipe). Imports are capped at 2 per tick and 3 per rolling day, duplicate URLs are skipped, every execution lands in the action audit log with `auto=true`, and each import posts a System card with the recipe link. Without Mealie configured the lane simply never produces anything; `HEARTBEAT_ENABLED=false` stops it along with the rest of the tick.

## 8. Verifying the install

1. `curl localhost:8089/health` — vera-api is up.
2. The startup config report lists every endpoint you configured (and flags anything deprecated or missing).
3. Chat in the app — ask something that needs a tool (e.g. a web search) and confirm the tool fires.
4. Trigger a Pulse run from the app (or `POST /pulse/run`) and watch cards arrive.
5. Enable a vein and confirm its chip appears; its producer job in Agentic shows the next run time.

When something doesn't work, check the config report and `docker compose logs vera-api` first.
