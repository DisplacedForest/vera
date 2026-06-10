# Adaptive Memory v3 — valve overrides

The `adaptive_memory_v3` OWUI function lives only in OWUI's DB (`function` table). This file
records the **non-default valve values** we set so the config is recoverable if the DB is lost.
Set them in OWUI → Workspace → Functions → Adaptive Memory v3 → Valves (or in the `function.valves`
JSON). After changing, restart the `open-webui` container so the new valves load.

## Provider (pre-existing — points at Vera, not the dead Ollama default)

| Valve | Value |
|---|---|
| `llm_provider_type` | `openai_compatible` |
| `llm_model_name` | `qwen3-30b-a3b` |
| `llm_api_endpoint_url` | `http://localhost:11434/v1/chat/completions` |
| `llm_api_key` | `local` |
| `timezone` | `America/Chicago` |

## Dedup / consolidation tuning

| Valve | Default | Set to | Why |
|---|---|---|---|
| `embedding_similarity_threshold` | 0.97 | **0.93** | Catch near-duplicate memories at write time (observed near-dup pairs sat ~0.95). |
| `similarity_threshold` | 0.95 | **0.92** | Same, for the text-similarity fallback path. |
| `summarization_min_cluster_size` | 3 | **2** | Let the background consolidation task merge a *pair* of dupes, not just clusters of 3+. |
| `summarization_min_memory_age_days` | 7 | **3** | Consolidate sooner instead of waiting a week. |

## Prompt addition (date handling & transience)

Appended to the `memory_identification_prompt` valve (after the default text):

```
**ADDITIONAL RULES (date handling & transience) - these OVERRIDE any example above:**
7.  ABSOLUTE DATES ONLY: Never store relative time expressions ("today", "tonight", "tomorrow",
    "this weekend", "next week", "next month", "soon"). Use the current date provided in the CONTEXT
    section to convert every time reference to an absolute date or range (e.g. "next week" ->
    "the week of 2026-06-08"; "tomorrow" -> "on 2026-06-01"). If you cannot determine the absolute
    date, omit the time reference entirely rather than storing a relative word.
8.  DURABLE INTEREST BAR: Do NOT save transient, one-off curiosities as persistent facts. A momentary
    request such as "what events are on today" or "what's the weather" is NOT a durable interest. Only
    save an interest, goal, plan, or preference if it will plausibly still matter to the user weeks
    from now.
```

(The built-in few-shot example at the call site teaches "next week" retention, so rule 7 is phrased
to explicitly override it. Verified working: "CRM conference next week" → "the week of 2026-06-07";
"what events today nearby?" → `[]`.)

## Prompt addition (episodic expiry tagging)

Appended a rule 9 to the `memory_identification_prompt` valve: the extractor labels each memory
durable vs episodic, and for **episodic** facts appends an expiry marker to the content in the exact
form ` [Expires: YYYY-MM-DD]` (durable facts get none). The `vera-api` `memory` router
(`POST /memory/groom`, nightly at 4 AM CT) deletes memories whose marker date has passed. Verified:
"CRM conference next week" → `...week of 2026-06-08 [Expires: 2026-06-14]`; "I love natural wine" →
no marker.

## Still deferred

Provenance (`[Src: <chat_id>]`) — needs a code edit to the function so it stamps the originating
chat id at extraction time (the extraction prompt alone can't know it). Not yet done.
