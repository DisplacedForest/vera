import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

import data_root

DATA_ROOT = data_root.apply()

from routers import actions, agentic, authoring, config_report, dreaming, feedback, groom_session, health, heartbeat, home, home_events, home_model, home_reconcile, images, integrations, journal, kitchen, knowledge, knowledge_groom, knowledge_restore, media_curation, memory, overseerr, pulse, pulse_veins, reminders, research, sandbox, scheduler, signals, updates, user_profile, vein_builder, vera_memory, vera_memory_groom, weather, websearch

# vera-api: ONE container, many capabilities.
# To add a capability: create routers/<name>.py exposing `router` (an APIRouter
# whose routes live under /<name>), then add it to CAPABILITIES below.
# Never add a new container.

logging.basicConfig(level=logging.INFO)


def _version() -> str:
    """Stack version: VERSION baked into the image (or the repo root in a dev
    checkout), else VERA_VERSION env, else a dev marker."""
    here = os.path.dirname(__file__)
    for p in (os.path.join(here, "VERSION"), os.path.join(here, "..", "..", "VERSION")):
        try:
            with open(p, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return os.environ.get("VERA_VERSION", "0.0.0-dev")


VERSION = _version()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_report.report(VERSION, data_root=DATA_ROOT)
    # The home-events supervisor (starts capture only while the home_modeling feature
    # is enabled) + the built-in scheduler run for the app's lifetime.
    await home_events.start()
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await home_events.stop()


app = FastAPI(title="vera-api", version=VERSION, lifespan=lifespan)

CAPABILITIES = {
    "search": websearch.router,
    "images": images.router,
    "feedback": feedback.router,
    "pulse": pulse.router,
    "pulse_veins": pulse_veins.router,
    "vein_builder": vein_builder.router,
    "memory": memory.router,
    "weather": weather.router,
    "signals": signals.router,
    "journal": journal.router,
    "kitchen": kitchen.router,
    "knowledge": knowledge.router,
    "knowledge_groom": knowledge_groom.router,
    "knowledge_restore": knowledge_restore.router,
    "user_profile": user_profile.router,
    "vera_memory": vera_memory.router,
    "vera_memory_groom": vera_memory_groom.router,
    "dreaming": dreaming.router,
    "groom_session": groom_session.router,
    "authoring": authoring.router,
    "home": home.router,
    "home_events": home_events.router,
    "home_model": home_model.router,
    "home_reconcile": home_reconcile.router,
    "integrations": integrations.router,
    "actions": actions.router,
    "agentic": agentic.router,
    "overseerr": overseerr.router,
    "reminders": reminders.router,
    "media_curation": media_curation.router,
    "research": research.router,
    "sandbox": sandbox.router,
    "health": health.router,
    "heartbeat": heartbeat.router,
    "updates": updates.router,
    "scheduler": scheduler.router,
}

for _name, _router in CAPABILITIES.items():
    app.include_router(_router)


@app.get("/health")
async def health_root():
    return {"ok": True, "version": VERSION, "routers": sorted(CAPABILITIES.keys())}


@app.get("/version")
async def version():
    return {"version": VERSION}


def serve():
    import uvicorn
    host = os.environ.get("VERA_BIND", "").strip() or "127.0.0.1"
    port = int(os.environ.get("VERA_PORT", "").strip() or 8089)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()
