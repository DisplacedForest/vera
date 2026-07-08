# Contributing to Vera

This page covers how the project is maintained, the code conventions, and how to land a change.

## How this repo is maintained

Vera was developed privately and published with a fresh history.

What that means in practice:

- **PRs are welcome and they land.** A merged PR is credited in the next release's notes and ships in that release.
- **Issues on this repo are the project's tracker** for bug reports and feature requests.

## Branching & releases

Trunk-based, deliberately simple:

- **`main` is the only long-lived branch and is always releasable.** There is no dev branch.
- **Changes arrive as PRs**: fork → short-lived branch → PR → squash-merge. CI must be green to merge; `main` is protected (no force-pushes, no deletions, required checks).
- **A release is an annotated tag on `main`** — `vX.Y.Z`, matching the root `VERSION` file. The tag triggers the release workflow, which builds and attaches `Vera.app.zip`, pushes `ghcr.io/displacedforest/vera-api:vX.Y.Z` (+ `:latest`), and creates the GitHub Release with generated notes. A tag that does not match `VERSION` fails the pipeline.
- **Release cadence**: bump `VERSION` → commit → `git tag -a vX.Y.Z` → push the tag. CI does the rest.
- `release/X.Y.x` branches appear only if a fix ever needs backporting to an older version; none exist until then.

## Ground rules for code

Enforced in review:

1. **Everything is parameterized.** No hardcoded endpoints, IPs, hostnames, home-directory paths, model names, or personal data — anywhere. Every external service is a URL in config. Every judgment call (taste, thresholds, region) is a config value with a neutral default. `scripts/leak-gate.sh` enforces this in CI: it scans the tracked tree for LAN IPs, home-directory paths, owner/location values, ticket references in comments, and key-shaped strings (hex, base64, `sk-`, JWT, PEM), and fails the build on any hit. For a proven false positive, add a justified regex to `scripts/leak-allow.txt`.
2. **Live data only.** Fetch from the real source; if it is unavailable, show "N/A" or an honest error state. Never substitute a fake default, never fake success.
3. **Degrade gracefully.** Every capability must behave sensibly when its endpoint or integration is unconfigured: report itself as off in the config report, refuse politely at runtime, and never affect the rest of the stack.
4. **One capability = one router.** New server-side capabilities are a single `APIRouter` module in `services/vera-api/routers/` plus one `include_router` line in `main.py`. No new containers, no sidecars.
5. **Icons, not emojis** — in the UI and in docs.
6. **Typed throughout.** Python is type-hinted, Swift is Swift 6 strict.
7. **Comments describe the present.** What the code does and why, never its history or what it replaced.
8. **No em dashes in product copy.** UI strings use periods, commas, or parentheses; missing values render as "N/A". `scripts/check-copy.sh` enforces this for the app's string literals and runs as part of `deploy.sh`; server-side user-facing strings (API error details, card titles) follow the same rule in review. Prompts, log lines, and code comments are exempt.
9. **Math over LLM for numbers; learned over hand-set once data exists.** Anything quantitative (ranking, dedup distance, engagement decay, urgency) is deterministic math with declared, env-tunable constants, never an LLM choosing a number that feeds another number. Pulse's five ranking weights ship hand-set and are replaced by a periodic logistic-regression fit over recorded feedback once enough labeled cards accrue: a transparent linear model over the same five features, gated on a sample threshold, never a black box.
10. **One tool-calling contract.** Text-protocol tool use speaks the Hermes wire format, owned by `services/vera-api/routers/tool_protocol.py`: tools are advertised in the system prompt as a JSON array of OpenAI-style function schemas inside `<tools></tools>`, the model emits `<tool_call>{"arguments": {...}, "name": "..."}</tool_call>`, and results return as `<tool_response>{"name": ..., "content": ...}</tool_response>`. Every call is validated against the module's pydantic `FunctionCall` schema (`name: str`, `arguments: dict`, both required) before dispatch; a malformed call gets a corrective `<tool_response>`, never an exception. New tools and new agentic surfaces use this module rather than inventing a format (servers that emit native OpenAI `tool_calls` keep the standard transport).
11. **code_interpreter is the capability-gap fallback, not the first resort.** A recurring need gets a real router; a one-off task the model cannot cover with a named tool runs as Python in the hardened sandbox (`scripts/vera-sandbox-setup.sh`, egress-free, ephemeral) through the `code_interpreter` tool. The tool only exists when the `sandbox` integration is configured.
12. **Structured model output validates then repairs.** Any surface that expects JSON from the model routes through `services/vera-api/routers/structured.py`'s `parsed(call, schema)`: the reply is schema-validated (pydantic), a failure earns up to `STRUCTURED_REPAIR_ATTEMPTS` re-prompts carrying the schema and the validation error, and a still-invalid result degrades to the surface's empty state. Never trust raw model JSON, never parse it ad hoc.

## Project layout

```
apps/vera-mac/          native macOS app (SwiftPM, Swift 6, SwiftUI)
services/vera-api/      the shared FastAPI container — every capability is a router
services/vera-voice/    STT/TTS reference service (MLX; HTTP + Wyoming protocol)
services/vera-image/    image-gen reference service (MLX; OpenAI Images API + native)
services/vera-vision/   vision serving notes + launchd template
services/vera-coder/    coding-agent harness for the dream/coder endpoint
services/owui-tools/    Open WebUI tools (model-invokable)
services/owui-functions/ Open WebUI filter functions (every-turn pipeline)
scripts/                ops scripts; each documents its own env in its header
docs/                   SETUP.md + screenshots
```

## Running tests

**vera-api** (Python 3.12):

```sh
cd services/vera-api
pip install -r requirements.txt pytest
pytest
```

The API suite is hermetic (every store runs against a temp dir; no live endpoints), and
`pytest.ini` sets the import path — `pytest` works from `services/vera-api` or as
`pytest services/vera-api/tests` from the repo root, no `PYTHONPATH` required.

**Mac app** (no XCTest by design — the app validates through its own harness):

```sh
cd apps/vera-mac
swift build
.build/debug/Vera --selftest
```

The app builds **only on macOS** — it is SwiftUI/AppKit. On Linux (containers, cloud
agents, CI shells) `swift build` fails at the toolchain or first Apple-framework import;
that is environmental, not a code defect. Validate Swift changes there by reading the
diff and rely on the Python suite as the runnable check.

For UI work, render any view headlessly and inspect it:

```sh
.build/debug/Vera --shot /tmp/view.png --view pulse   # chat | pulse | memory | plugins | agentic | voice | onboarding | settings …
.build/debug/Vera --shot /tmp/view-light.png --view pulse --appearance light   # render either appearance (default dark)
```

Run all of the above before opening a PR. CI runs the same suites on every PR and must be green to merge.

## Sending a change

1. Fork, branch, build, and make sure the tests above pass locally.
2. Keep the diff focused — one concern per PR.
3. If you add config, add it to `.env.example` with a comment and make sure it shows up in the startup config report (a test fails if an env var is read but undocumented).
4. If you add a capability, it must degrade gracefully when unconfigured — that is the first thing reviewed.
5. Open the PR with a plain description of what changed and why. No fixed template.

## Filing issues

**Bugs**: include the output of `GET /version`, the startup config report from the container log (`docker compose logs vera-api | head -60` — secrets are masked), and what you expected vs. what you saw.

**Feature requests**: describe the problem before the solution. Vera stays one shared container with parameterized capabilities — proposals that fit that shape are straightforward to land.

## License

By contributing, you agree your contributions are licensed under the [MIT License](LICENSE).
