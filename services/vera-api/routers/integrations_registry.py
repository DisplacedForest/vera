REGISTRY: dict[str, dict] = {
    "coder": {
        "display_name": "Coder / Dream model",
        "fields": [
            {"id": "url", "env": "DREAM_BASE", "label": "OpenAI-compatible base URL", "secret": False,
             "hint": "any /v1 endpoint (llama.cpp, vLLM, llama-swap, mlx_lm.server, or a hosted API)"},
            {"id": "model", "env": "DREAM_MODEL", "label": "Model id", "secret": False},
            {"id": "tool_protocol", "env": "DREAM_TOOL_PROTOCOL", "label": "Tool-call protocol",
             "secret": False, "optional": True, "choices": ["openai", "hermes"],
             "hint": "openai = standard tool_calls (default); hermes = Hermes-style text tool "
                     "calls for servers that pass model text through untouched"},
        ],
        "unlocks": ["nightly dreaming consolidation and grooming",
                    "fact verification research with web search"],
    },
    "sandbox": {
        "display_name": "Code sandbox",
        "fields": [
            {"id": "url", "env": "VERA_SANDBOX_URL", "label": "Jupyter base URL", "secret": False,
             "hint": "the hardened kernel container from scripts/vera-sandbox-setup.sh "
                     "(reachable from vera-api, e.g. over the sandbox network)"},
            {"id": "token", "env": "VERA_SANDBOX_TOKEN", "label": "Jupyter token",
             "secret": True, "optional": True},
        ],
        "unlocks": ["code_interpreter tool for capability-gap tasks"],
    },
    "image_gen": {
        "display_name": "Image generation",
        "fields": [
            {"id": "url", "env": "VERA_IMAGE_BASE", "label": "Base URL", "secret": False,
             "hint": "any endpoint serving the OpenAI Images API (POST /v1/images/generations). "
                     "services/vera-image works out of the box"},
            {"id": "protocol", "env": "IMAGE_PROTOCOL", "label": "Protocol",
             "secret": False, "optional": True, "choices": ["openai", "vera"],
             "hint": "openai = standard Images API (default); vera = the bespoke reference "
                     "contract (deterministic seeds + the vision pause/resume extension)"},
        ],
        "unlocks": ["cover art on Pulse briefing cards"],
    },
    "home_assistant": {
        "display_name": "Home Assistant",
        "fields": [
            {"id": "url", "env": "HOME_ASSISTANT_BASE", "label": "Base URL", "secret": False,
             "hint": "use an IP, not .local. Containers on a bridge network can't resolve mDNS"},
            {"id": "token", "env": "HOME_ASSISTANT_KEY", "label": "Long-lived access token", "secret": True},
        ],
        "unlocks": ["live home state in chat, heartbeat, and cards",
                    "confirm-gated device actuation",
                    "home map reconciliation against live entities"],
        "features": [
            {"id": "home_modeling", "label": "Home modeling",
             "ramifications": (
                 "Captures every Home Assistant state change house-wide (roughly 5,000–15,000 "
                 "events per day on a 30-day rolling window) and models the household's rhythm "
                 "from 10–90 days of accumulation. Adds nightly model, reconcile, and digest jobs. "
                 "Experimental: the miners are unvalidated at scale.")},
        ],
    },
    "grocy": {
        "display_name": "Grocy",
        "fields": [
            {"id": "url", "env": "GROCY_BASE", "label": "Base URL", "secret": False},
            {"id": "api_key", "env": "GROCY_KEY", "label": "API key", "secret": True},
        ],
        "unlocks": ["kitchen inventory and expiry tracking", "shopping list", "stock adjustments from chat"],
        "paired_with": {"id": "mealie", "label": "recipe suggestions from expiring inventory"},
    },
    "mealie": {
        "display_name": "Mealie",
        "fields": [
            {"id": "url", "env": "MEALIE_BASE", "label": "Base URL", "secret": False},
            {"id": "api_key", "env": "MEALIE_KEY", "label": "API token", "secret": True},
        ],
        "unlocks": ["recipe import and browse", "recipe classification"],
        "paired_with": {"id": "grocy", "label": "recipe suggestions from expiring inventory"},
    },
    "overseerr": {
        "display_name": "Overseerr",
        "fields": [
            {"id": "url", "env": "OVERSEERR_BASE", "label": "Base URL", "secret": False},
            {"id": "api_key", "env": "OVERSEERR_KEY", "label": "API key", "secret": True},
        ],
        "unlocks": ["media requests from chat", "library availability checks"],
        "features": [
            {"id": "media_curation", "label": "Media curation digest",
             "ramifications": (
                 "Adds a weekly job that sweeps discovery sources through Overseerr, runs an LLM "
                 "taste pass over the pool, and posts a worth-adding digest card. Experimental: "
                 "it has run exactly once at scale. Expect rough edges in selection quality.")},
        ],
    },
    "unraid": {
        "display_name": "Unraid",
        "fields": [
            {"id": "url", "env": "UNRAID_BASE", "label": "GraphQL endpoint", "secret": False},
            {"id": "api_key", "env": "UNRAID_KEY", "label": "API key", "secret": True},
        ],
        "unlocks": ["confirm-gated container updates and host actuation"],
    },
    "searxng": {
        "display_name": "SearXNG",
        "fields": [
            {"id": "url", "env": "SEARXNG_BASE", "label": "Search endpoint", "secret": False,
             "hint": "the /search endpoint of your SearXNG instance"},
        ],
        "unlocks": ["web search for chat, research, Pulse, and watcher veins"],
    },
    "embeddings": {
        "display_name": "Embeddings",
        "fields": [
            {"id": "url", "env": "VERA_EMBED_URL", "label": "OpenAI-compatible /v1 base URL", "secret": False,
             "hint": "any /v1 endpoint serving POST /v1/embeddings (llama.cpp, vLLM, llama-swap, "
                     "or a hosted API)"},
            {"id": "model", "env": "VERA_EMBED_MODEL", "label": "Embedding model id", "secret": False,
             "optional": True, "hint": "required by multi-model servers (llama-swap, hosted APIs); "
                                       "single-model servers ignore it"},
        ],
        "unlocks": ["Pulse novelty ranking and the duplicate-finding floor",
                    "profile-graph node embeddings for dedup-merge"],
    },
    "reddit": {
        "display_name": "Reddit",
        "fields": [
            {"id": "client_id", "env": "REDDIT_CLIENT_ID", "label": "App client ID", "secret": True,
             "hint": "create a 'script' app at reddit.com/prefs/apps; the id sits under the app name"},
            {"id": "client_secret", "env": "REDDIT_CLIENT_SECRET", "label": "App secret", "secret": True},
            {"id": "user_agent", "env": "REDDIT_USER_AGENT", "label": "User-Agent", "secret": False,
             "optional": True, "hint": "a descriptive UA, e.g. vera-scout/1.0 by /u/you"},
        ],
        "unlocks": ["Reddit as a Pulse research source (reddit-native search via the official API)"],
    },
    "apple_reminders": {
        "display_name": "Apple Reminders",
        "fields": [
            {"id": "url", "env": "VERA_REMINDERS_URL", "label": "Bridge URL", "secret": False,
             "hint": "the vera-reminders bridge on a Mac signed into iCloud "
                     "(services/vera-reminders, default port 8132)"},
        ],
        "unlocks": ["read and write Reminders lists from chat, shared lists included"],
    },
}

# Legacy kill-switches: these env vars can force a feature OFF (back-compat with
# pre-registry deployments) but can never turn one on — consent always comes first.
_FEATURE_KILL_SWITCH = {
    ("home_assistant", "home_modeling"): "HOME_EVENTS_ENABLED",
    ("overseerr", "media_curation"): "MEDIA_CURATION_ENABLED",
}
