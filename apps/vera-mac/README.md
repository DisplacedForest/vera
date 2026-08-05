# Vera (macOS app)

Native SwiftUI client for Vera. Text chat streams directly from a configured OpenAI-compatible
`/v1` endpoint and stores conversations in `~/.vera/vera.sqlite`. Pulse, Memory,
and the Agentic canvas remain optional surfaces backed by vera-api. Veins are managed from
the Pulse header; the Plugins manager lives in Settings.

## Develop
```bash
swift run                       # dev build + launch
swift build                     # compile check
.build/debug/Vera --selftest    # headless: native transport, persistence, importers, stores
.build/debug/Vera --shot out.png --view chat|pulse|memory|agentic|veins|settings-plugins|settings|settings-advanced|onboarding   # render a screenshot
.build/debug/Vera --dump-context   # print the assembled per-request system context, section by section
```
Config lives in `~/.vera/config.json`, editable in-app via Settings (⌘,): `model_base`,
`model_api_key`, `model`, `voice_base`, `vera_api_base`, and `owner_name`. The model base must
end in `/v1`; its key is optional. `VERA_MODEL_BASE`, `VERA_MODEL_API_KEY`, and `VERA_MODEL`
override file values. First launch with no native model config opens onboarding. The app is
standalone: chat, history, memory, and tools need only the model endpoint, and every other
surface degrades cleanly when its optional service is unset. The one legacy exception is the
voice session, which still streams through an Open WebUI socket until voice moves onto the
native engine; its connection settings live entirely inside that quarantined module.

Chat history starts fresh on a native install by design; prior Open WebUI conversations are
not imported. Knowledge collections are the one dataset carried over, migrated server-side
into the vera-api document store (see `docs/SETUP.md`). Pulse keeps working whenever the
optional vera-api URL is configured, and Continue in chat lands the card in the local
database as a native conversation: text, citations, and provenance persist locally and
reopen offline, while remote card images stay uncached and use the normal failure
placeholders if their source goes away.

Native tools live in Settings, Tools and are disabled by default. Apple Reminders runs
locally after macOS permission. Web search and deep research call the configured vera-api
(`vera_api_base`); with no vera-api URL set they are unavailable and their rows show the
unconfigured hint. Deep research runs on its own 300-second deadline, and its report renders
with citation chips and a numbered sources row that persist with the conversation.

Advanced model controls (Settings, Model, Advanced) tune per-model request parameters:
sampling, tokens and stops, reasoning, streaming, a context-length ceiling, and custom
chat-template-kwargs entries. Overrides are stored per endpoint profile and model in
`native_chat.parameterOverrides`; unset controls are omitted from the payload, capability
gating follows the model's profile (including the reasoning flag), and
`VERA_CHAT_TEMPLATE_KWARGS` still replaces the kwargs object wholesale when set. The
section's Last request panel shows the resolved parameter set for the most recent send.

Config-driven tools extend the same registry. A JSON declaration dropped in
`~/.vera/tools.d/` loads at startup and again on every Settings save, and a valid one joins
the built-ins in Settings, Tools, the tool-calling loop, and the confirmation path exactly
like a native tool defined in code.

`NativeContextAssembler` builds each request's system context deterministically, in a fixed
order: app policy, the active persona, user context, per-conversation instructions, session
facts (date, time, owner name), recalled memory, a world-model seam, and capability context (per-tool usage contracts plus the ask,
artifact, chart, and citation format contracts, injected only when the model and
configuration support them; citations join only when a research-capable tool is active). Every section has a character cap, identical inputs assemble byte-identical context,
and `--dump-context` shows exactly what a request would contain.

The prompt library (Settings, Persona) manages that user-authored text in the local
database: personas with one active selection, user-context profiles, and reusable prompts
inserted into the composer as message text. Every save keeps a revision snapshot with exact
restore, entries import and export as markdown with a small front-matter header, and
validation rejects oversize content, credential-shaped text, and anything claiming the
policy scope. The legacy single system prompt migrates in as the default persona on first
launch. Per-conversation instructions live on the conversation row and are edited from a
chip above the composer.

## Package & install
```bash
scripts/package.sh              # -> build/Vera.app (ad-hoc signed)
scripts/deploy.sh               # package + install to /Applications (+ a second Mac if configured)
VERA_STUDIO_HOST=user@my-other-mac scripts/deploy.sh    # also push to a second Mac
```
Bump `VERSION` for a new release version (the build number is the git short sha).

### First launch (ad-hoc signed)
The app is **ad-hoc signed** (no Developer ID), so Gatekeeper will block a double-click
on a fresh copy. `deploy.sh` clears the quarantine attribute on install. If you ever
copy it manually, either right-click → **Open** once, or:
```bash
xattr -dr com.apple.quarantine /Applications/Vera.app
```
`spctl --assess` will report it as rejected: expected for ad-hoc; it still runs.
