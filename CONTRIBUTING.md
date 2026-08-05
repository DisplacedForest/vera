# Contributing to Vera

How to set up, test, and land a change.

## How this repo is maintained

Vera was developed privately and published with a fresh history.

- **PRs are welcome and they land.** A merged PR is credited in the next release's notes and ships in that release.
- **Issues on this repo are the project's tracker** for bug reports and feature requests.

## Branching and releases

Trunk-based, deliberately simple:

- **`main` is the only long-lived branch and is always releasable.** There is no dev branch.
- **Changes arrive as PRs**: fork, short-lived branch, PR, squash merge. CI must be green to merge; `main` is protected (no force-pushes, no deletions, required checks).
- **Releases and tags are cut by the maintainer.** Don't bump `VERSION` or push tags in a PR.

## Ground rules for code

Enforced in review:

1. **Everything is parameterized.** No hardcoded endpoints, IPs, hostnames, home-directory paths, model names, or personal data. Every external service is a URL in config. Every judgment call (taste, thresholds, region) is a config value with a neutral default.
2. **Degrade gracefully.** Every capability must behave sensibly when its endpoint or integration is unconfigured: report itself as off in the config report, refuse politely at runtime, and never affect the rest of the stack.
3. **One capability = one router.** New server-side capabilities are a single `APIRouter` module in `services/vera-api/routers/` plus one entry in the `CAPABILITIES` table in `main.py`. No new containers, no sidecars.
4. **Typed throughout.** Python is type-hinted, Swift is Swift 6 strict.

## Project layout

```
apps/vera-mac/          native macOS app (SwiftPM, Swift 6, SwiftUI)
services/vera-api/      the shared FastAPI container; every capability is a router
services/vera-voice/    STT/TTS reference service (MLX; HTTP + Wyoming protocol)
services/vera-image/    image-gen reference service (MLX; OpenAI Images API + native)
services/vera-vision/   vision serving notes + launchd template
services/vera-coder/    coding-agent harness for the dream/coder endpoint
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
`pytest.ini` sets the import path. `pytest` works from `services/vera-api` or as
`pytest services/vera-api/tests` from the repo root, no `PYTHONPATH` required.

**Mac app** (no XCTest by design; the app validates through its own harness):

```sh
cd apps/vera-mac
swift build
.build/debug/Vera --selftest
```

The app builds **only on macOS**. It is SwiftUI/AppKit; on Linux (containers, cloud
agents, CI shells) `swift build` fails at the toolchain or first Apple-framework import,
which is environmental, not a code defect.

For UI work, render any view headlessly and inspect it:

```sh
.build/debug/Vera --shot /tmp/view.png --view chat
.build/debug/Vera --shot /tmp/view-light.png --view pulse --appearance light
```

Run all of the above before opening a PR. CI must be green to merge.

## Sending a change

1. Fork, branch, build, and make sure the tests above pass locally.
2. Keep the diff focused: one concern per PR.
3. If you add config, add it to `.env.example` with a comment and make sure it shows up in the startup config report (a test fails if an env var is read but undocumented).
4. If you add a capability, it must degrade gracefully when unconfigured. That is the first thing reviewed.
5. Open the PR with a plain description of what changed and why. No fixed template.

## Filing issues

**Bugs**: include the output of `GET /version`, the startup config report from the container log (`docker compose logs vera-api | head -60`, secrets are masked), and what you expected vs. what you saw.

**Feature requests**: describe the problem before the solution. Vera stays one shared container with parameterized capabilities; proposals that fit that shape are straightforward to land.

## License

By contributing, you agree your contributions are licensed under the [MIT License](LICENSE).
