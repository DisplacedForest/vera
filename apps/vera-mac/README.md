# Vera (macOS app)

Native SwiftUI client for Vera. Text chat streams directly from a configured OpenAI-compatible
`/v1` endpoint and stores conversations in `~/.vera/vera.sqlite`. Pulse, Journal, Memory,
and the Agentic canvas remain optional surfaces. Veins are managed from the Pulse header;
the Plugins and MCP managers live in Settings.

## Develop
```bash
swift run                       # dev build + launch
swift build                     # compile check
.build/debug/Vera --selftest    # headless: native transport, persistence, and optional live checks
.build/debug/Vera --shot out.png --view chat|pulse|journal|memory|agentic|veins|settings-plugins|settings-mcp|settings|onboarding   # render a screenshot
.build/debug/Vera --dump-context   # print the assembled per-request system context, section by section
```
Config lives in `~/.vera/config.json`, editable in-app via Settings (⌘,): `model_base`,
`model_api_key`, `model`, `voice_base`, `vera_api_base`, and `owner_name`. The model base must
end in `/v1`; its key is optional. `VERA_MODEL_BASE`, `VERA_MODEL_API_KEY`, and `VERA_MODEL`
override file values. Existing Open WebUI keys remain readable for transitional surfaces and
0.3.1 rollback. First launch with no native model config opens onboarding.

Native chat is text only in this slice. Attachments, voice, document knowledge, and
Open WebUI import are deferred. Pulse itself keeps working whenever the optional vera-api
URL is configured, and Continue in chat lands the card in the local database as a native
conversation: text, citations, and provenance persist locally and reopen offline, while
remote card images stay uncached and use the normal failure placeholders if their source
goes away.

`NativeContextAssembler` builds each request's system context deterministically, in a fixed
order: app policy, the editable persona, session facts (date, time, owner name), recalled
memory, a world-model seam, and capability context (per-tool usage contracts plus the ask,
artifact, and chart format contracts, injected only when the model and configuration support
them). Every section has a character cap, identical inputs assemble byte-identical context,
and `--dump-context` shows exactly what a request would contain.

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
