# Vera (macOS app)

Native SwiftUI client for Vera (Open WebUI). Chat streams through OWUI's pipeline
(memory + tools) over Socket.IO; surfaces for Pulse, Journal, Memory, and the Agentic
canvas. Veins are managed from the Pulse header; the Plugins and MCP managers live in Settings.

## Develop
```bash
swift run                       # dev build + launch
swift build                     # compile check
.build/debug/Vera --selftest    # headless: exercise the live OWUI client
.build/debug/Vera --shot out.png --view chat|pulse|journal|memory|agentic|veins|settings-plugins|settings-mcp|settings|onboarding   # render a screenshot
```
Config lives in `~/.vera/config.json`, editable in-app via Settings (⌘,): `base`, `api_key`,
`model`, `completions_url`, `voice_base`, `vera_api_base`, `owui_email`, `owui_password`,
`owner_name`. Env vars (`OWUI_BASE`, `OWUI_API_KEY`, …) override file values. First launch
with no config opens an onboarding sheet.

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
`spctl --assess` will report it as rejected — expected for ad-hoc; it still runs.
