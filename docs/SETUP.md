# Setting up Vera

This is the end-to-end path from nothing to a working installation: backend, chat, native app, then the optional layers — integrations, veins, and satellite services. Each stage works without the stages after it, and the system reports what is and isn't configured.

**The stack** (each component is a URL in config; topology is up to you):

| Piece | What it is | Required? |
|---|---|---|
| An OpenAI-compatible LLM server | llama.cpp / llama-swap / vLLM / Ollama / a hosted API — anything serving `/v1` | Yes |
| [Open WebUI](https://github.com/open-webui/open-webui) | Transitional tool and skill surfaces | No |
| **Vera.app** (this repo) | The native macOS client with direct streaming text chat and local history | Recommended |
| **vera-api** (this repo) | One FastAPI container that lights up the ambient and experimental surfaces (Pulse, veins, weather, kitchen, research, heartbeat, scheduler, actions) | Optional |
| Integrations (Home Assistant, Grocy, Mealie, Overseerr, Unraid, SearXNG, Reddit, Embeddings) | Each unlocks a capability | No |
| Satellite services (voice, image, vision, coder) | Reference implementations of documented HTTP contracts | No |

> **Fastest path on a Mac.** Install the Mac app and point onboarding at an OpenAI-compatible endpoint ending in `/v1`. Text chat needs nothing else. For the optional ambient tier, open Settings, Services, Engine and choose **On this Mac**. The app downloads the packaged engine for its own version, verifies it, and runs it as a background service on `127.0.0.1`.

## 1. Prerequisites

- **An LLM endpoint** — any standard OpenAI-compatible `/v1` server with a capable instruct model.
- **Docker** (optional) on any Linux/macOS host for vera-api or Open WebUI.
- **Open WebUI** (optional) for the transitional tool and skill surfaces that have not moved into the native engine.

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
| Open WebUI | `OWUI_BASE`, `OWUI_KEY` | Self-authored skills, knowledge collections, and chat-history learning while one is still connected |
| Web search | `SEARXNG_BASE` (+ optional `PLAYWRIGHT_WS`) | Research, Pulse sourcing, watcher veins, and the Mac app's web search and deep research chat tools (the app reaches them through its `vera_api_base` setting, never SearXNG directly) |
| Identity | `VERA_OWNER_ID`, `VERA_OWNER_NAME`, `HOME_LOCATION_NAME`, `HOME_TZ`, `WEATHER_LAT`/`LON`, `TEMPERATURE_UNIT` | Personalization, the owner id that cards/read marks/profiles are keyed by (defaults to `owner`), schedules in your timezone, weather anchoring |
| Dream/coder | `DREAM_BASE`, `DREAM_MODEL`, `DREAM_TOOL_PROTOCOL` | Nightly knowledge consolidation + fact verification |
| Audit hooks | `AUDIT_WAKE_URL`, `AUDIT_RELEASE_URL` | Cross-model claim audits on every Pulse run when the audit model is served on demand (POSTed before/after the batched end-of-run audit; unset = no hook calls) |
| Embeddings | `VERA_EMBED_URL`, `VERA_EMBED_MODEL` | Document knowledge collections (upload, indexing, retrieval that grounds research and chat) plus Profile Graph dedup and Pulse novelty math; unset, collection management still works while indexing and retrieval report unconfigured |
| Image gen | `VERA_IMAGE_BASE`, `IMAGE_PROTOCOL` | Generated cover art on Pulse cards |
| Vision review | `VERA_VISION_BASE`, `VERA_VISION_MODEL` | Optional Pulse cover-art review and one retry |
| Scout sources | `GITHUB_API_BASE`, `ARXIV_BASE` (default to public endpoints); Reddit is a plugin (`REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`, register a script app at reddit.com/prefs/apps) | Pulse candidate search across github/papers/reddit; news and local ride `SEARXNG_BASE`, weather rides `WEATHER_LAT`/`LON` |

Two conventions:

- **Endpoints are `*_BASE`, credentials are `*_KEY`.** Older names still work; the config report flags them with their replacement.
- **Unset means off, visibly.** A capability without its endpoint reports itself as not configured — it never fakes output and never affects other capabilities.

Integrations (Home Assistant and the rest) can be set in `.env` for headless installs; the app's integration store in step 4 is the recommended path.

## 3. Wire Open WebUI (optional)

Vera attaches to Open WebUI as a set of tools (model-invokable capabilities) and functions (every-turn pipeline filters):

1. **Tools** — in Open WebUI: Workspace → Tools → create, then paste each file from `services/owui-tools/` you want (start with `vera_memory.py` and `deep_research.py`; add `kitchen.py`, `media_request.py`, `home_knowledge.py`, `propose_action.py`, `see_image.py`, `self_author.py` as you enable their backends). In each tool's valve settings, set `vera_api_url` to your vera-api base.
2. **Functions** — Admin → Functions → create, paste from `services/owui-functions/` (the memory filter; `vision_autosee.py` if you run a vision endpoint), enable them.
3. **The model** — give your Vera model the tools you imported (model settings → tools) so chat can invoke them.

Open WebUI tools and features do not apply to native text chat. Native chat sends the saved system prompt, selected model, and completed local conversation history directly to `POST /v1/chat/completions`. It uses only the standard OpenAI `tools`, streamed `tool_calls`, assistant tool-call, and `tool` result fields. No Open WebUI fallback or model-specific text convention is used. Endpoints that return text only continue to work as text chat.

If you install the Mac app, its integration store performs the per-integration OWUI wiring (attaching kitchen/media tools when you connect Grocy or Overseerr, etc.) automatically — the manual steps above are only needed once for the base tools.

## 4. The Mac app

**From a release** (macOS 26 Tahoe or later; earlier macOS can run releases up to 0.2.x): download `Vera.app.zip` from the [latest release](https://github.com/DisplacedForest/vera/releases/latest), unzip, and drag `Vera.app` to Applications. The app is ad-hoc signed (no notarization), so macOS quarantines the first launch — right-click → **Open** once, and it runs normally from then on. The app checks Releases and can update itself in place.

**From source** (Swift toolchain):

```sh
cd apps/vera-mac
swift build -c release
scripts/deploy.sh    # packages Vera.app, ad-hoc signs it, installs it to /Applications
```

First launch runs **onboarding**. Give the endpoint a friendly saved name, enter its OpenAI-compatible URL ending in `/v1`, and add an optional API key. The key is stored in the Mac keychain. Discover models, inspect every returned identifier, choose one explicitly, review the local system prompt, decide whether to enable local memory, and inspect the native tool picker. The guide can be skipped and resumed from Settings, Endpoints. A valid configuration from an earlier release migrates into a saved endpoint and opens the app normally. Native conversations and approved memory live in `~/.vera/vera.sqlite`. A fresh native install starts with empty history and memory off. Existing Open WebUI history and memory remain untouched.

Settings, Models keeps the last successful discovery result, shows the active model and whether it was restored, recommended, or chosen by you, and distinguishes an empty response, unusable model entries, authentication failure, network failure, and malformed data. Refresh never clears a known selection just because discovery fails. Settings, Persona holds the prompt library: personas with one active selection, user context, and reusable prompts, each with revision history and markdown import and export. Settings, Tools clearly separates callable tools from unavailable integrations and persists enabled choices. Apple Reminders starts disabled. Enabling it deliberately requests macOS Reminders access. Denied or unavailable tools are omitted from model requests even when their saved preference remains on.

Apple Reminders is the first native tool surface. A standard tool-calling endpoint can list reminders, create reminders, and complete a reminder by the identifier returned from a list call. These operations run through EventKit inside the Mac app and do not need vera-api, the standalone bridge service, or Open WebUI. They are available only during an explicit chat turn. Pulse, dreaming, scheduled work, voice, and other autonomous paths cannot invoke them.

Each call renders as a pending, succeeded, or failed activity chip. Expand it to inspect the JSON request and result. Activity is stored with local conversation history without endpoint credentials or authorization headers. Unknown tools, disabled or unavailable tools, invalid JSON arguments, and calls past the loop limits do not execute. Their bounded error result is returned to the model. A turn stops after four tool rounds or eight total calls. A tool failure, malformed stream, endpoint outage, or exhausted limit keeps received text and activity and marks the assistant turn interrupted when no final answer arrives.

Settings, Model also holds each model's capability profile and the optional vision bridge. The profile (accepts image input, supports tool calls, supports streaming replies, images per request) defaults from a bundled name-pattern table of known vision-model families and can be overridden per model; the pane names which pattern matched or that your override is active, and Use defaults clears an override. The profile is enforced at request time: a model marked without tool support receives no tool schemas, a model marked without streaming gets one complete non-streamed response, image history is trimmed to the per-request limit, and a model marked text-only never receives image parts, including images from earlier turns after a model switch. The vision bridge is any separate OpenAI-compatible vision endpoint (base URL ending in `/v1`, model id, optional keychain-stored API key; `VERA_VISION_BRIDGE_BASE`, `VERA_VISION_BRIDGE_MODEL`, and `VERA_VISION_BRIDGE_API_KEY` override the saved values). When the active model does not accept an attached image, a configured bridge describes it and the description enters the request as labeled context with the bridge model named on the turn; without a bridge the app asks whether to send the message without its attachment. Attachments only ever leave the machine toward the model endpoint or the bridge you configured.

Voice, document knowledge, and MCP remain unavailable in the native chat path.

Continuing a Pulse card into chat is native and local. The first continuation of a card re-reads `GET /pulse/cards` and then stores the card's text, sources, and provenance in the local database, so vera-api must be reachable only for that first step. After that the conversation opens offline, survives the card's expiry, and is reused by later continues of the same card. Card images are not cached locally.

### Custom chat tools

Chat tools beyond the built-ins are configuration, not code. Drop a JSON file in
`~/.vera/tools.d/` (next to `config.json`; `VERA_CONFIG_DIR` relocates the whole directory)
and the app loads it at startup and again whenever Settings saves. Each valid file becomes
a native tool: a toggle appears in Settings, Tools, the model is offered the tool when it is
enabled and available, calls execute over HTTP, and each call renders as an activity chip
in chat like any other tool. A malformed file is skipped, with the reason shown in Settings.

A declaration is one JSON object per file. For example, a tool over a home inventory
service:

```json
{
  "name": "list_inventory_items",
  "title": "List inventory items",
  "description": "Looks up items in the household inventory service by name or location. Use this when the user asks what is in stock or where something is stored.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": { "type": "string" },
      "in_stock_only": { "type": "boolean" }
    },
    "required": ["query"],
    "additionalProperties": false
  },
  "endpoint": "/inventory/items",
  "method": "GET",
  "confirmation": "none",
  "timeout_s": 20,
  "enabled": true
}
```

- `name`: the unique function name the model calls.
- `title`: the short label shown in Settings and in the activity chip.
- `description`: what the tool does and when to use it, written for the model.
- `parameters`: a JSON Schema object. The supported subset is string and boolean
  properties, plus `required` and `additionalProperties: false`.
- `endpoint`: an absolute URL, or a path starting with `/`, resolved against the
  configured vera-api base URL. A path-relative tool stays unavailable until that URL is
  set.
- `method`: `GET` sends the arguments as query parameters; `POST` sends them as a JSON
  body.
- `confirmation`: `"none"` runs the call immediately; `"required"` asks in the chat UI
  before the request fires.
- `timeout_s`: optional, seconds before the call is abandoned (default 20).
- `enabled`: optional, default true.
- `json_fields`: optional, POST only. Maps a declared string property to a request body
  field, and the string must then contain a JSON object, for example
  `"json_fields": {"filters_json": "filters"}`. This lets a tool accept a nested object
  while the parameters stay within the string and boolean subset.

The response comes back to the model as tool data: JSON is preferred, plain text is
tolerated, and it is held under a size cap. A non-2xx response shows in chat as a failed
tool call with its status. Deleting a declaration, or setting `enabled` to false, removes
the tool starting with the next message.

The app bundles two declarations this way, an actions surface and a self-authoring/journal
surface over vera-api routes, and both activate once the vera-api base URL is configured.

### Native personal memory

Settings, Memory controls the optional native personal-memory feature. It starts off after upgrade. Approved records stay in the Mac app's local database and are organized in the Memory Library as **You**, **Topics**, **Areas**, and **People**. A compatible embeddings model enables semantic recall. A compatible chat model can create bounded proposals from completed turns and natural-language change requests. Missing or failed optional models leave ordinary chat usable.

All generated changes wait in the Memory review queue. Vera does not automatically create, update, merge, suppress, or prune approved memory. Episodic memory requires an absolute expiry date, stops appearing in recall after that date, and is removed by an automatic groom once the date has passed, with each removal recorded in the audit trail. A conversation can be excluded from suggestions through its sidebar menu.

See [Native memory](NATIVE_MEMORY.md) for the recall budgets, privacy boundary, retained Adaptive Memory behavior, and intentional differences.

### Conversation ingestion (opt-in)

vera-api's Profile Graph learns from conversation transcripts. Alongside the scheduled pull sources, `POST /conversations/ingest` accepts batched transcripts pushed by a client (each with a stable `conv_id`, a unix `ts`, and role/text messages). The endpoint is guarded by the `X-Agent-Token` header matching `KNOWLEDGE_AGENT_TOKEN` and rejects everything when that variable is unset, so nothing is ingested unless you explicitly configure the token and point a client at it. Ingestion is idempotent per `conv_id`: re-posting a conversation is counted as a duplicate and changes nothing.

When vera-api is configured, Pulse and the other ambient surfaces continue operating independently. The two opt-in surfaces afterward each live inside the feature they drive:

- **Plugins** — the integration store, a tab in **Settings** (⌘,). Each card is one integration: enter URL + key, **Test**, **Save & Enable**. The app writes vera-api's config and performs the OWUI wiring in the same step. Experimental features (whole-house event modeling, media curation) sit behind their parent integration with an explicit consent sheet — off until consented.
- **Veins** — Pulse's ambient monitors, opened from the **Veins** button in the Pulse header. **Vera ships with none**: a vein is something you build for your own life — a river gauge, a service-status watch, a geopolitics bar, whatever deserves an ambient eye — and your catalog starts empty. Every vein is a schema-validated JSON definition living one file per vein at `/data/veins.d/<kind>.json`, managed through the API (`GET /pulse/veins/schema` serves the contract, `POST /pulse/veins` creates). A vein carries a `pipeline` of blocks (the built-ins `web_search`, `http_fetch`, `ha_state`, `trip_band`, `llm_judge`, `llm_compose`, `situation_cluster`, `present`, plus code-backed blocks capabilities register, plus any Python modules you drop in the data volume's `blocks.d/` — the code extension point for bespoke sources, each calling `vein_engine.register` at import) and a cron `schedule`, and the vein engine runs it: dropping a valid definition file in place is all it takes to have a running ambient monitor, and a definition marked `standing` keeps one always-present card updated in place instead of alerting. Each pipeline vein registers a scheduler job as `vein_<kind>`, `POST /pulse/veins/{kind}/run` fires it on demand (`dry_run=true` returns the would-be cards without posting), and the engine owns quiet discipline: one card per distinct situation (updated, never stacked), a seen-memory so watchers don't re-alert on a standing story (`VEIN_SEEN_DECAY_DAYS`, default 7), and a schedule floor for LLM-bearing pipelines (`VEIN_LLM_FLOOR_MINUTES`, default 30; engine state in `VEIN_ENGINE_DB_PATH`). Per-vein enable/options state lives separately in `/data/veins.json`. The Veins pane shows only what this deployment can actually run: a vein whose definition requires an integration appears once that integration is connected (`GET /pulse/veins/catalog` returns exposed veins, `?all=true` adds the requirement-unmet ones for the pane's Add-a-vein browse sheet), and removing the integration hides the vein again while keeping its settings. Veins you author yourself always show, whatever their requirements. You don't have to write definitions by hand: `POST /pulse/veins/builder/turn` runs the authoring conversation against whatever model `VERA_BASE`/`VERA_MODEL` name (describe what you want watched; each turn returns prose plus a schema-validated draft), and `POST /pulse/veins/builder/dry_run` executes an unsaved draft once and returns what would have posted, persisting nothing. With no model configured both endpoints report disabled cleanly. After a vein exists, its Configure sheet can reopen it in the builder to edit the definition in place or delete it outright (`PUT /pulse/veins/{kind}/definition`, `DELETE /pulse/veins/{kind}`), so the whole lifecycle lives in the app.

**Sharing veins.** A vein is a file, so it travels. Export one from its detail sheet (or `GET /pulse/veins/{kind}/export`) and you get a `<kind>.vein.json` with deployment specifics stripped: provider URLs, env-seeded option defaults, and any secret-shaped strings are cleared, so the file is safe to hand to anyone. Import one from the Add-a-vein sheet (or `POST /pulse/veins/import`): it validates against the schema, re-resolves requirements on your deployment, warns about anything unmet or any pipeline block this build lacks (the vein simply cannot enable until it exists), and lands the vein **disabled**. Review it, then enable it exactly like a vein you built yourself. A file from a newer deployment format is refused with a clear message rather than imported half-understood.

Both are UI over vera-api's API — headless deployments can do everything with `curl`.

## 5. Integrations, one by one

Each integration unlocks its capability when its test passes; each degrades to "off" when absent. All are configurable from the integration store or `.env`:

| Integration | Unlocks | Notes |
|---|---|---|
| Home Assistant | Live home state in chat and Pulse, confirm-gated device actuation, the `ha_state` vein block | Use an IP for the URL, not `.local` — containers can't resolve mDNS. Long-lived access token. |
| Grocy | Kitchen inventory + expiry awareness, shopping list | Pairs with Mealie: recipe suggestions from expiring inventory unlock when both are on |
| Mealie | Recipe import, browse, classification | See Grocy pairing |
| Overseerr | Media requests from chat, availability checks; the media-curation blocks for a weekly digest vein (experimental, consent-gated) | |
| Unraid | Confirm-gated container updates, host actuation, update digests | Official Unraid API (GraphQL) with an API key |
| SearXNG | Web search for chat, research, Pulse, watcher veins | Strongly recommended; run it next to vera-api |
| Embeddings | Pulse novelty ranking and the duplicate-finding floor, profile-graph node embeddings for dedup-merge | Any OpenAI-compatible `POST {base}/v1/embeddings` endpoint; the model id is only needed for multi-model servers (llama-swap, hosted APIs). The LLM server can serve this too |
| Vision review | Reviews generated Pulse cover art and permits one retry | Any OpenAI-compatible vision-capable endpoint, including the primary-model endpoint when that model accepts images. Configure its base URL and model, then promote Pulse's visual-review workflow draft. |
| Apple Reminders | Reminders lists read/write from chat, shared lists included | URL of the `vera-reminders` bridge (see satellite services below) |

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

**If you run the Vera Mac app, native chat does not need this service.** Open Settings,
Tools and enable **Apple Reminders**. The native permission prompt appears, and standard
OpenAI tool calls then reach EventKit directly inside the app. The separate Apple
Reminders switch in Settings, Plugins controls optional legacy service wiring and does
not expose the native chat schema. This path needs neither
vera-api nor Open WebUI and runs only for an explicit chat ask. Settings, Plugins still
owns the separate optional wiring for legacy Open WebUI and vera-api use.

`services/vera-reminders` remains the headless reference for deployments with no Mac
app. Run `scripts/deploy-vera-reminders.sh` on a signed-in Mac, approve the one-time
prompt, then enable the Apple Reminders integration with the bridge URL and install
`services/owui-tools/reminders.py` as an Open WebUI tool.

Every satellite env var (models, ports, voices, paths) is documented in `.env.example`'s
companion-services section; voice installs with one command (`scripts/deploy-vera-voice.sh` —
runtime copy-out, both venvs, launchd agents), and the image/vision services ship launchd
templates installed via `scripts/install-launchd.sh`.

## 7. The scheduler

vera-api runs all recurring work itself — no external cron. Defaults:

| Job | Default | Gated on |
|---|---|---|
| Pulse briefing | daily 5:00 | — (core; needs your LLM) |
| Home modeling (3 nightly jobs) | 2:00–3:30 | Home Assistant's home-modeling consent |
| Heartbeat tick | every 20 min | `HEARTBEAT_ENABLED` kill switch |
| Vein runs (`vein_<kind>`) | each definition's `schedule` | that vein's enable state |

Pipeline veins register their jobs dynamically — one per definition, appearing and disappearing with the definition file — and the standard override convention applies (`SCHEDULE_VEIN_<KIND>`, `SCHEDULE_VEIN_<KIND>_ENABLED`).

With home-modeling consent on, the capture stream also tees every numeric `sensor.*` reading into a dedicated series store (`SERIES_DB_PATH`, default `/data/series.db`) retained for `SERIES_RETAIN_DAYS` (default 365) — the substrate household forecasting reads. Inspect it at `GET /home/series` (entities + counts) and `GET /home/series/{entity_id}` (raw points); on first init it backfills from the existing event log. With capture off it stays empty and both endpoints return empty cleanly.

A gated job never fires while its gate is closed — on a fresh install, nothing is monitored until its vein is enabled or its feature is consented to. The app's **Agentic** tab renders all of this as a canvas: every flow as a node connected to the surface it feeds (the Pulse feed, the veins, memory, actions), with live status, plain-English schedules, and drill-in pipelines for the flows that have stages. The topology comes from `GET /agentic/graph`, a server-declared manifest, so a new capability appears on the canvas without an app update. Click a node to run it now, toggle it, or edit its schedule (live, no restart), or pin schedules with `SCHEDULE_<JOB>` / `SCHEDULE_<JOB>_ENABLED` env overrides. A gated job reports *why* it is gated instead of running.

Opening the Pulse flow switches the existing app sidebar to the node library served by the API's workflow catalog. Drag nodes into the main workflow canvas (dropping one onto a wire splices it into the path), select a node to configure it through fields rendered from its served schema, and use an explicit draft promotion when the graph is ready. The server validates every save and promotion and its message appears in the editor when a graph is rejected. Editing a draft does not change the published Pulse workflow.

A Run toggle on the same canvas overlays the latest recorded run, per node state, output counts, and duration, and selecting a node shows its recorded input, output, timing, and any error in place of the config fields.

Everything autonomous is auditable in one place: `GET /agentic/activity?hours=24` returns a normalized, newest-first feed merging heartbeat outcomes, scheduled job runs, the action audit log, and Open WebUI automation runs (when OWUI's automations API is reachable). The app renders it as the **Activity** pane of the Agentic tab, refreshed every 30 seconds, and recent events animate along their edges on the canvas. A missing backing store or unreachable OWUI contributes nothing instead of erroring, so the feed works on any subset of the stack.

The heartbeat tick also resolves Vera's **journal** watches: a due watch node whose resolve condition and date are both met transitions to resolved deterministically (no model recheck, so a watch can never become immortal). The journal itself is a view over the Profile Graph's watch and project nodes, rendered read-only in the app's Journal view and at `GET /journal`; the legacy self-authored markdown at `VERA_JOURNAL_PATH` (default `/data/journal/JOURNAL.md`) remains only as a fallback until the graph holds nodes. With no watch nodes and no fallback file, the step and the view are empty. Chat steers the journal through the `self_author.py` tool's `read_journal` / `journal_commit` functions (a commit lands a watch node), so install that tool if you want "keep an eye on X" and "what are you watching" to work in conversation.

With the Mealie integration enabled, the heartbeat can also **curate recipes autonomously**: when Vera finds a recipe genuinely worth keeping she imports it herself through `POST /actions/auto` — no confirmation card. This free lane accepts only verbs explicitly enrolled as `autonomous` in the action registry (today: `kitchen.mealie_import`, which the registry permits because it is low-risk and reversible by deleting the recipe). Imports are capped at 2 per tick and 3 per rolling day, duplicate URLs are skipped, every execution lands in the action audit log with `auto=true`, and each import posts a System card with the recipe link. Without Mealie configured the lane simply never produces anything; `HEARTBEAT_ENABLED=false` stops it along with the rest of the tick.

## 8. Verifying the install

1. `curl localhost:8089/health` — vera-api is up.
2. The startup config report lists every endpoint you configured (and flags anything deprecated or missing).
3. Chat in the app — ask something that needs a tool (e.g. a web search) and confirm the tool fires.
4. Trigger a Pulse run from the app (or `POST /pulse/run`) and watch cards arrive.
5. Create a vein (the in-app builder or `POST /pulse/veins`), enable it, and confirm its chip appears; its job in Agentic shows the next run time.

When something doesn't work, check the config report and `docker compose logs vera-api` first.
