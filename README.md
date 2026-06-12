<div align="center">

# Vera

**A self-hosted AI assistant for your home.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Swift 6](https://img.shields.io/badge/Swift-6-F05138?logo=swift&logoColor=white)](apps/vera-mac)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](services/vera-api)
[![FastAPI](https://img.shields.io/badge/FastAPI-one_container-009688?logo=fastapi&logoColor=white)](services/vera-api)
[![macOS app](https://img.shields.io/badge/macOS-14%2B-000000?logo=apple&logoColor=white)](apps/vera-mac)

[Setup](docs/SETUP.md) · [Contributing](CONTRIBUTING.md)

<img src="docs/assets/home.png" alt="Vera — the native macOS client" width="850">

</div>

---

Vera is a self-hosted personal AI assistant: scheduled research briefings, opt-in ambient monitoring, persistent memory, local voice, and Home Assistant integration, running entirely on your own hardware with no cloud dependency.

All endpoints, model servers, thresholds, and behavioral defaults are configuration — nothing is hardcoded — and every capability degrades gracefully when its dependencies are unconfigured.

## Architecture

Three components, connected by URLs:

- **[Open WebUI](https://github.com/open-webui/open-webui)** — conversations, memory, and tool execution, against any OpenAI-compatible model server.
- **vera-api** — a single FastAPI service; each capability is one router: research briefings, ambient watch veins, home intelligence, kitchen inventory, memory grooming, a scheduler, and a typed confirm-before-acting actuation layer.
- **Vera.app** — a native SwiftUI macOS client: chat, the Pulse feed, veins, memory curation, integrations, and voice.

```mermaid
flowchart LR
    APP["Vera.app<br/>native macOS client"]
    OWUI["Open WebUI<br/>chat · memory · tools"]
    LLM["any OpenAI-compatible<br/>LLM server"]
    API["vera-api<br/>one container, every capability"]
    SEARX["SearXNG"]
    HA["Home Assistant"]
    INT["Grocy · Mealie · Overseerr · Unraid"]
    SAT["satellite contracts<br/>voice · image · vision · coder"]

    APP --> OWUI --> LLM
    APP --> API
    OWUI --> API
    API --> LLM
    API --> SEARX
    API --> HA
    API --> INT
    API --> SAT
    APP --> SAT
```

Run everything on one machine or spread it across several — topology is configuration. There are no hardcoded hosts in the tree, and the startup config report shows exactly what is wired.

## Features

### Chat — tools, artifacts, structured answers

Conversations run through Open WebUI's pipeline, so every tool and memory applies. Replies can carry interactive choice cards, stat blocks and charts, citations, and canvas artifacts.

<div align="center"><img src="docs/assets/chat.png" alt="Vera chat — interactive choice cards, canvas artifacts, cited sources" width="850"></div>

### Pulse — scheduled research briefings

Vera researches overnight — topics drawn from her own accumulating interests and what the household actually asks about — and produces briefing cards with cited sources, inline statistics, and charts. Any card can be continued as a chat.

<div align="center"><img src="docs/assets/pulse-detail.png" alt="A Pulse briefing card — stats, sourced prose, charts" width="850"></div>

### Veins — opt-in ambient monitoring

A row of status chips above the feed — System, Weather, Signals, Media — each an independently configured monitor that stays quiet until a configured threshold is crossed. **None are enabled by default.** Each vein is scoped: the Signals vein can watch only financial stress indicators; the System vein can monitor only Home Assistant. Thresholds determine what surfaces; the model only explains what crossed them.

<div align="center"><img src="docs/assets/pulse.png" alt="The Pulse surface with its vein chips" width="850"></div>

### Journal — her standing commitments, in her own words

When a monitored situation deserves follow-through (a signals event, or simply "keep an eye on lumber prices for me"), Vera writes a commitment into her journal: what she is watching, why it matters, what would resolve it, and when to check next. Each heartbeat she acts on the entries that are due, appends dated findings, surfaces a card only when something materially changed, and retires an entry when its own resolve condition is met. The journal is a plain markdown document she authors and maintains herself — the app renders it read-only, and every touch is logged. It follows the same contract as her self-authored heartbeat instructions: the code parses only entry boundaries and a cadence line; the content is entirely hers. You steer it by talking to her: in chat she reads her own journal (`read_journal`) and takes instructions about it (`journal_commit`) — ask what she's keeping an eye on, hand her a new watch, or have her consolidate or retire an entry, all through the same authoring judgment.

### Scheduler — visible, editable, gated

All recurring work — briefings, weather, signals, grooming, health probes — runs on a built-in scheduler with a visible, editable schedule. A job tied to a vein or integration does not fire until that vein or integration is enabled, and gated jobs report why they are not running.

Everything Vera does on her own is also auditable in one place: an Activity feed (`GET /agentic/activity`) merges heartbeat outcomes, scheduled job runs, and autonomous actions into a single newest-first list, rendered as the Activity section of the Agentic tab. Autonomy is wanted, and it is always visible.

<div align="center"><img src="docs/assets/agentic.png" alt="The Agentic tab — every scheduled job, live" width="850"></div>

### Integrations — configured from the app

Each integration is a card: enter a URL and key, test, enable. Enabling an integration activates the capability across the stack, including the Open WebUI tool wiring. Experimental features (whole-house behavior modeling, media curation) require explicit consent and state exactly what they do before they can be enabled.

<div align="center"><img src="docs/assets/plugins.png" alt="Integrations with live status" width="850"></div>

### Memory, voice, and home control

Vera maintains an inspectable, editable memory store and grooms it nightly — every change reversible and surfaced as an audit card. A local voice service provides STT/TTS. With Home Assistant connected, Vera answers from live home state and acts through a typed, confirmation-gated action system; nothing in the home actuates without an explicit confirmation. Trust is graduated per verb: an action explicitly enrolled as autonomous — which the registry permits only for low-risk, trivially reversible verbs — executes without a confirmation and surfaces afterward as a System card. Exactly one verb is enrolled: recipe import, so Vera can save a recipe she finds worth keeping into the household cookbook on her own, capped, deduplicated, and announced after the fact.

<div align="center"><img src="docs/assets/voice.png" alt="Voice mode" width="850"></div>

## The endpoint matrix

Every external dependency is a configuration slot with defined behavior when empty:

| Slot | Contract | Powers | When absent |
|---|---|---|---|
| Main LLM | OpenAI `/v1` | Everything generated | Nothing generates; API surfaces still serve |
| Open WebUI | OWUI API | Chat, memory, promoted cards, self-authored skills | Chat features off; Pulse still researches |
| SearXNG | `/search` JSON | Research, signals news, image sourcing | Search-dependent features report unconfigured |
| Dream/coder LLM | OpenAI `/v1` + tool calls | Nightly consolidation, fact verification | Dreaming skips; daily features unaffected |
| Image gen | OpenAI Images API | Pulse cover art | Cards use the best researched image instead |
| Vision | OpenAI chat + `image_url` | Image understanding in chat | Vision tools report unconfigured |
| Voice | Wyoming + small HTTP API | Hands-free voice mode | Voice UI disabled |
| Playwright | run-server websocket | Full-page renders for research | Falls back to snippets |

| Integration | Powers | When absent |
|---|---|---|
| Home Assistant | Live home state, actuation, System-vein sources | Home features off |
| Grocy / Mealie | Kitchen inventory, expiry, recipes (pairing unlocks suggestions) | Kitchen tools off |
| Overseerr | Media requests, weekly curation digest (consent-gated) | Media vein unavailable |
| Unraid | Container updates, host actuation | Those update sources drop from System |
| FRED / EIA keys | Credit-spread and grid-stress signals | Those collectors skip cleanly |

<details>
<summary><b>Example deployment</b></summary>

<br>

One Linux server runs Open WebUI, vera-api, SearXNG, and an RTX 3090 serving the main model via llama-swap. A Mac Studio runs the MLX satellite services (image generation, vision, the dream/coder model) on demand, and a Mac mini runs voice. A single capable machine can run the entire stack, and any component can be replaced by a hosted equivalent by changing one URL.

</details>

## Quick start

**Backend** (any Docker host — pulls the released image from GHCR):

```sh
git clone https://github.com/DisplacedForest/vera.git && cd vera
cp .env.example .env     # fill in your LLM + OWUI endpoints; everything else is optional
docker compose up -d
docker compose logs vera-api | head -60    # the config report — what's wired, what's not
```

**App** (macOS 14+): download `Vera.app.zip` from the [latest release](https://github.com/DisplacedForest/vera/releases/latest), unzip, drag to Applications. The app is ad-hoc signed, so the first launch needs right-click → Open.

Building either from source instead:

```sh
docker compose up -d --build        # backend (uncomment `build:` in docker-compose.yml)
cd apps/vera-mac && scripts/deploy.sh   # app — packages Vera.app and installs it to /Applications
```

Onboarding asks for your endpoints, then offers the opt-in veins; integrations are configured anytime from the sidebar.

<div align="center"><img src="docs/assets/onboarding.png" alt="Onboarding" width="700"></div>

The full walkthrough, including Open WebUI wiring and every integration: **[docs/SETUP.md](docs/SETUP.md)**.

## Constraints

- The reference satellite services (voice, image, vision, coder) are MLX-based and require Apple Silicon. Each implements a documented HTTP contract (OpenAI Images, OpenAI chat, Wyoming) that any compatible service can satisfy — the contracts are the interface; the references are one implementation.
- The native app is macOS-only (14+). The backend runs anywhere Docker runs.
- Vera is built for a single household, not multi-tenancy.

## Contributing

Issues and PRs are welcome; merged work is credited in the next release's notes. See [CONTRIBUTING.md](CONTRIBUTING.md) for the conventions: everything parameterized, live data only, graceful degradation, one capability per router.

## License

[MIT](LICENSE).
